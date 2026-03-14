from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Awaitable, Callable, Iterable
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote_plus

import httpx
from sqlalchemy import select

from brain.revenue_operators.hunter import HunterOperator, OpportunityInput, _normalize_vertical
from database import AsyncSessionLocal
from models import ConversationLog, CrmInteraction

HtmlFetcher = Callable[[str], Awaitable[str]]


_JOB_KEYWORDS: dict[str, tuple[str, ...]] = {
    "community_manager": ("community manager", "social media", "redes sociales"),
    "recepcionista": ("recepcionista", "recepción", "atención al cliente"),
    "web_developer": ("web developer", "desarrollador web", "wordpress", "pagina web"),
}

_VERTICAL_HINTS: dict[str, tuple[str, ...]] = {
    "clinics": ("clinic", "clinica", "médic", "dental", "doctor", "hospital", "salud"),
    "restaurants": ("restaurant", "restaurante", "taquer", "cafeter", "cocina", "food"),
    "spas": ("spa", "beauty", "belleza", "esthetic", "estética", "salon"),
    "barbershops": ("barber", "barbería", "barbershop", "peluquer", "corte"),
    "dentists": ("dental", "dentista", "odont"),
}

_SERVICE_PAGE_RE = re.compile(
    r"""href=["'](?P<href>[^"']*(?:servicio|service|automatiz|chatbot|whatsapp|web|seo|ads|marketing|precios|pricing)[^"']*)["']""",
    re.IGNORECASE,
)
_PRICE_RE = re.compile(r"(?:(?:MXN|USD|\$)\s?[0-9][0-9,\.]*)|(?:[0-9][0-9,\.]*\s?(?:MXN|USD))", re.IGNORECASE)

_COMPLAINT_KEYWORDS: tuple[str, ...] = (
    "no funciona",
    "no existe",
    "alguien conoce",
    "necesito",
    "es muy caro",
    "alternativa a",
)

_NEED_THEMES: dict[str, tuple[str, ...]] = {
    "web_presence": (
        "pagina web",
        "página web",
        "sitio web",
        "landing page",
        "wordpress",
        "web developer",
        "desarrollador web",
        "no existe pagina",
        "sin web",
    ),
    "whatsapp_automation": (
        "automatizacion whatsapp",
        "automatización whatsapp",
        "whatsapp business",
        "responder whatsapp",
        "seguimiento whatsapp",
    ),
    "chatbot": (
        "chatbot",
        "bot para negocio",
        "bot de ventas",
        "bot de whatsapp",
    ),
    "social_media_management": (
        "community manager",
        "redes sociales",
        "manejo de redes",
        "social media",
    ),
    "seo": ("seo", "google maps", "posicionamiento", "aparecer en google"),
    "crm_followup": (
        "crm",
        "seguimiento a clientes",
        "seguimiento de leads",
        "pipeline",
        "recepcionista",
        "atencion a clientes",
    ),
    "voice_agent": (
        "agente de voz",
        "llamadas automaticas",
        "llamadas automáticas",
        "voz ia",
        "voice agent",
    ),
    "pricing_pressure": (
        "es muy caro",
        "caro",
        "precio",
        "alternativa a",
        "más barato",
        "mas barato",
    ),
}

_ARBITRAGE_VERTICAL_HINTS: dict[str, str] = {
    "chatbot": "services",
    "whatsapp": "services",
    "crm": "services",
    "booking": "services",
    "voice": "services",
    "sales": "services",
    "restaurant": "restaurants",
    "clinic": "clinics",
    "dental": "dentists",
    "spa": "spas",
    "barber": "barbershops",
}


async def _default_fetch(url: str) -> str:
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        response = await client.get(url, headers={"User-Agent": "KANLogic MarketScanner/1.0"})
        response.raise_for_status()
        return response.text


def _infer_vertical(text: str, default: str = "services") -> str:
    normalized = text.lower()
    for vertical, hints in _VERTICAL_HINTS.items():
        if any(hint in normalized for hint in hints):
            return vertical
    return default


def _sanitize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _normalized_text(text: str) -> str:
    lowered = _sanitize_text(text).lower()
    replacements = str.maketrans(
        {
            "á": "a",
            "é": "e",
            "í": "i",
            "ó": "o",
            "ú": "u",
            "ñ": "n",
        }
    )
    return lowered.translate(replacements)


def _resolve_theme(text: str) -> str:
    normalized = _normalized_text(text)
    for theme, hints in _NEED_THEMES.items():
        if any(_normalized_text(hint) in normalized for hint in hints):
            return theme
    words = [word for word in re.findall(r"[a-z0-9]{4,}", normalized) if word not in {"para", "como", "con", "esta"}]
    return words[0] if words else "general_automation"


def _estimate_signal_opportunity(
    *,
    theme: str,
    count: int,
    source: str,
    sample_texts: list[str],
    vertical: str = "services",
) -> OpportunityInput:
    intensity = min(1.0, count / 18.0)
    return OpportunityInput(
        business_name=f"{source}:{theme}",
        vertical=_normalize_vertical(vertical),
        city="MX",
        has_website=False,
        has_bot=False,
        estimated_market_size=max(120, count * 18),
        pain_score=round(min(0.96, 0.62 + (intensity * 0.26)), 4),
        payment_capacity_score=round(min(0.88, 0.64 + (intensity * 0.14)), 4),
        competition_score=round(max(0.14, 0.42 - (intensity * 0.16)), 4),
        stack_fit_score=round(min(0.96, 0.72 + (intensity * 0.18)), 4),
        source=source,
        metadata={
            "theme": theme,
            "signals_count": count,
            "sample_texts": sample_texts[:5],
        },
    )


def _extract_text_rows(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    extracted: list[dict[str, Any]] = []
    for row in rows:
        text = _sanitize_text(
            " ".join(
                str(row.get(field) or "")
                for field in ("text", "title", "body", "content", "description")
            )
        )
        if not text:
            continue
        payload = dict(row)
        payload["text"] = text
        payload["theme"] = str(row.get("theme") or _resolve_theme(text))
        extracted.append(payload)
    return extracted


def _infer_arbitrage_vertical(name: str, tagline: str) -> str:
    haystack = _normalized_text(f"{name} {tagline}")
    for hint, vertical in _ARBITRAGE_VERTICAL_HINTS.items():
        if hint in haystack:
            return vertical
    return "services"


def _extract_json_ld_blocks(html: str) -> list[dict[str, Any]]:
    blocks = re.findall(r"<script[^>]+application/ld\\+json[^>]*>(.*?)</script>", html, re.IGNORECASE | re.DOTALL)
    parsed: list[dict[str, Any]] = []
    for raw in blocks:
        raw = raw.strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except Exception:
            continue
        if isinstance(data, list):
            parsed.extend(item for item in data if isinstance(item, dict))
        elif isinstance(data, dict):
            parsed.append(data)
    return parsed


class MarketScanner(HunterOperator):
    async def scan_job_boards(
        self,
        city: str = "Guadalajara",
        *,
        fetcher: HtmlFetcher | None = None,
        results: Iterable[dict[str, Any]] | None = None,
    ) -> list[OpportunityInput]:
        if results is not None:
            rows = list(results)
        else:
            fetch = fetcher or _default_fetch
            searches = [
                ("indeed", f"https://mx.indeed.com/jobs?q=community+manager&l={city}"),
                ("indeed", f"https://mx.indeed.com/jobs?q=recepcionista&l={city}"),
                ("indeed", f"https://mx.indeed.com/jobs?q=web+developer&l={city}"),
                ("computrabajo", f"https://mx.computrabajo.com/trabajo-de-community-manager-en-{city.lower()}"),
                ("computrabajo", f"https://mx.computrabajo.com/trabajo-de-recepcionista-en-{city.lower()}"),
                ("computrabajo", f"https://mx.computrabajo.com/trabajo-de-desarrollador-web-en-{city.lower()}"),
            ]
            rows = []
            for board, url in searches:
                try:
                    html = await fetch(url)
                except Exception:
                    continue
                rows.extend(self._parse_job_board_html(board=board, html=html, city=city, url=url))

        opportunities: list[OpportunityInput] = []
        seen: set[str] = set()
        for row in rows:
            company = _sanitize_text(str(row.get("business_name") or row.get("company") or ""))
            if not company or company.lower() in seen:
                continue
            seen.add(company.lower())
            job_type = str(row.get("job_type") or "community_manager").lower()
            title = _sanitize_text(str(row.get("job_title") or ""))
            vertical = _normalize_vertical(str(row.get("vertical") or _infer_vertical(f"{company} {title}")))
            pain = 0.74
            if job_type == "recepcionista":
                pain = 0.82
            elif job_type == "web_developer":
                pain = 0.79
            opportunities.append(
                OpportunityInput(
                    business_name=company,
                    vertical=vertical,
                    city=city,
                    has_website=job_type != "web_developer",
                    has_bot=job_type != "recepcionista",
                    estimated_market_size=220,
                    pain_score=pain,
                    payment_capacity_score=float(row.get("payment_capacity_score") or 0.7),
                    competition_score=float(row.get("competition_score") or 0.34),
                    stack_fit_score=float(row.get("stack_fit_score") or 0.86),
                    source="job_boards",
                    metadata=dict(row),
                )
            )
        return opportunities

    def _parse_job_board_html(self, *, board: str, html: str, city: str, url: str) -> list[dict[str, Any]]:
        jobs: list[dict[str, Any]] = []
        for block in _extract_json_ld_blocks(html):
            if str(block.get("@type") or "").lower() != "jobposting":
                continue
            title = _sanitize_text(str(block.get("title") or ""))
            company = _sanitize_text(
                str(
                    (block.get("hiringOrganization") or {}).get("name")
                    or block.get("company")
                    or block.get("name")
                    or ""
                )
            )
            if not title or not company:
                continue
            jobs.append(
                {
                    "board": board,
                    "company": company,
                    "job_title": title,
                    "city": city,
                    "source_url": url,
                    "job_type": self._classify_job_type(title),
                    "vertical": _infer_vertical(f"{company} {title}"),
                }
            )
        if jobs:
            return jobs

        patterns = [
            re.compile(
                r'data-testid="(?:slider_item|jobTitle)[^"]*"[^>]*>(?P<title>[^<]+)</.*?(?:companyName|fs16)".*?>(?P<company>[^<]+)<',
                re.IGNORECASE | re.DOTALL,
            ),
            re.compile(
                r'(?P<title>community manager|recepcionista|desarrollador web|web developer)[^<]{0,80}</.*?(?P<company>[A-ZÁÉÍÓÚÑ][^<]{2,80})<',
                re.IGNORECASE | re.DOTALL,
            ),
        ]
        for pattern in patterns:
            for match in pattern.finditer(html):
                title = _sanitize_text(match.group("title"))
                company = _sanitize_text(match.group("company"))
                if not title or not company:
                    continue
                jobs.append(
                    {
                        "board": board,
                        "company": company,
                        "job_title": title,
                        "city": city,
                        "source_url": url,
                        "job_type": self._classify_job_type(title),
                        "vertical": _infer_vertical(f"{company} {title}"),
                    }
                )
        return jobs

    def _classify_job_type(self, title: str) -> str:
        normalized = title.lower()
        for job_type, hints in _JOB_KEYWORDS.items():
            if any(hint in normalized for hint in hints):
                return job_type
        return "community_manager"

    async def scan_trends(
        self,
        keywords: Iterable[str] | None = None,
        region: str = "MX",
        *,
        results: Iterable[dict[str, Any]] | None = None,
        trends_client: Any | None = None,
    ) -> list[OpportunityInput]:
        keywords = list(keywords or ["chatbot para negocio", "página web negocio", "automatización whatsapp"])
        if results is None:
            if trends_client is None:
                from pytrends.request import TrendReq

                trends_client = TrendReq(hl="es-MX", tz=360)
            trends_client.build_payload(keywords, timeframe="today 3-m", geo=region)
            frame = trends_client.interest_over_time()
            rows: list[dict[str, Any]] = []
            for keyword in keywords:
                if keyword not in frame:
                    continue
                values = [int(v) for v in frame[keyword].tolist() if int(v) >= 0]
                if not values:
                    continue
                rows.append(
                    {
                        "keyword": keyword,
                        "trend_score": round(sum(values) / len(values), 2),
                        "peak_score": max(values),
                    }
                )
            results = rows

        opportunities: list[OpportunityInput] = []
        for row in results or []:
            keyword = str(row.get("keyword") or "trend")
            trend_score = float(row.get("trend_score") or 0.0)
            vertical = _infer_vertical(keyword, default="services")
            business_name = f"trend:{keyword}"
            opportunities.append(
                OpportunityInput(
                    business_name=business_name,
                    vertical=_normalize_vertical(vertical),
                    city=region,
                    has_website=False,
                    has_bot=False,
                    estimated_market_size=300,
                    trend_score=trend_score,
                    pain_score=0.68,
                    payment_capacity_score=0.72,
                    competition_score=0.28,
                    stack_fit_score=0.84,
                    source="google_trends",
                    metadata=dict(row),
                )
            )
        return opportunities

    async def scan_competitor_activity(
        self,
        competitor_domains: Iterable[str],
        *,
        fetcher: HtmlFetcher | None = None,
        baseline_snapshots: dict[str, dict[str, Any]] | None = None,
    ) -> list[OpportunityInput]:
        fetch = fetcher or _default_fetch
        opportunities: list[OpportunityInput] = []
        for domain in competitor_domains:
            try:
                html = await fetch(domain if domain.startswith("http") else f"https://{domain}")
            except Exception:
                continue
            current = self._extract_competitor_signals(html)
            baseline = (baseline_snapshots or {}).get(domain, {})
            new_service_pages = sorted(set(current["service_pages"]) - set(baseline.get("service_pages", [])))
            new_prices = sorted(set(current["prices"]) - set(baseline.get("prices", [])))
            if not new_service_pages and not new_prices:
                continue
            opportunities.append(
                OpportunityInput(
                    business_name=domain.replace("https://", "").replace("http://", ""),
                    vertical=_normalize_vertical(_infer_vertical(" ".join(new_service_pages) or domain)),
                    city="MX",
                    has_website=True,
                    has_bot=False,
                    estimated_market_size=260,
                    pain_score=0.71,
                    payment_capacity_score=0.74,
                    competition_score=0.63,
                    stack_fit_score=0.76,
                    source="competitor_activity",
                    metadata={
                        "domain": domain,
                        "new_service_pages": new_service_pages,
                        "new_prices": new_prices,
                        "snapshot": current,
                    },
                )
            )
        return opportunities

    def _extract_competitor_signals(self, html: str) -> dict[str, list[str]]:
        service_pages = [match.group("href") for match in _SERVICE_PAGE_RE.finditer(html)]
        prices = _PRICE_RE.findall(html)
        return {
            "service_pages": sorted(set(service_pages)),
            "prices": sorted(set(_sanitize_text(price) for price in prices)),
        }

    async def scan_complaints(
        self,
        *,
        results: Iterable[dict[str, Any]] | None = None,
        fetcher: HtmlFetcher | None = None,
        pain_keywords: Iterable[str] | None = None,
    ) -> list[OpportunityInput]:
        rows: list[dict[str, Any]]
        if results is not None:
            rows = _extract_text_rows(results)
        else:
            fetch = fetcher or _default_fetch
            rows = []
            for subreddit in ("mexico", "entrepreneur", "smallbusiness"):
                for keyword in list(pain_keywords or _COMPLAINT_KEYWORDS):
                    url = (
                        f"https://www.reddit.com/r/{subreddit}/search.json?"
                        f"q={quote_plus(keyword)}&restrict_sr=1&sort=new&t=month&limit=25"
                    )
                    try:
                        raw = await fetch(url)
                        payload = json.loads(raw)
                    except Exception:
                        continue
                    for child in (((payload or {}).get("data") or {}).get("children") or []):
                        data = child.get("data") or {}
                        rows.append(
                            {
                                "platform": f"reddit:{subreddit}",
                                "title": data.get("title"),
                                "body": data.get("selftext"),
                                "url": f"https://reddit.com{data.get('permalink') or ''}",
                            }
                        )
            for keyword in list(pain_keywords or _COMPLAINT_KEYWORDS):
                url = f"https://nitter.net/search?f=tweets&q={quote_plus(keyword + ' negocio mexico')}"
                try:
                    html = await fetch(url)
                except Exception:
                    continue
                for match in re.finditer(r'<div class="tweet-content[^"]*">(?P<text>.*?)</div>', html, re.IGNORECASE | re.DOTALL):
                    text = re.sub(r"<[^>]+>", " ", match.group("text"))
                    rows.append({"platform": "x", "text": _sanitize_text(text)})

            rows = _extract_text_rows(rows)

        counts: Counter[str] = Counter()
        samples: dict[str, list[str]] = {}
        platforms: dict[str, set[str]] = {}
        for row in rows:
            theme = str(row.get("theme") or "").strip()
            if not theme:
                continue
            counts[theme] += 1
            samples.setdefault(theme, []).append(str(row.get("text") or ""))
            platform = str(row.get("platform") or "web")
            platforms.setdefault(theme, set()).add(platform)

        opportunities: list[OpportunityInput] = []
        for theme, count in counts.most_common():
            if count < 10:
                continue
            opportunity = _estimate_signal_opportunity(
                theme=theme,
                count=count,
                source="complaints",
                sample_texts=samples.get(theme, []),
            )
            opportunity.metadata["platforms"] = sorted(platforms.get(theme, set()))
            opportunities.append(opportunity)
        return opportunities

    async def scan_arbitrage(
        self,
        *,
        results: Iterable[dict[str, Any]] | None = None,
        fetcher: HtmlFetcher | None = None,
        search_fetcher: HtmlFetcher | None = None,
    ) -> list[OpportunityInput]:
        products: list[dict[str, Any]]
        if results is not None:
            products = [dict(row) for row in results]
        else:
            fetch = fetcher or _default_fetch
            try:
                html = await fetch("https://www.producthunt.com/")
            except Exception:
                products = []
            else:
                products = []
                for name, tagline in re.findall(r'"name":"([^"]+)".{0,200}?"tagline":"([^"]+)"', html):
                    if len(products) >= 12:
                        break
                    products.append({"name": _sanitize_text(name), "tagline": _sanitize_text(tagline)})

        search = search_fetcher or _default_fetch
        opportunities: list[OpportunityInput] = []
        for row in products:
            name = _sanitize_text(str(row.get("name") or ""))
            tagline = _sanitize_text(str(row.get("tagline") or row.get("description") or ""))
            if not name:
                continue
            has_spanish_competitor = row.get("has_spanish_competitor")
            search_query = str(row.get("search_query") or f'"{name}" mexico españa latam alternativa')
            if has_spanish_competitor is None:
                try:
                    search_html = await search(f"https://duckduckgo.com/html/?q={quote_plus(search_query)}")
                except Exception:
                    search_html = ""
                has_spanish_competitor = bool(
                    re.search(
                        r"(mexico|méxico|latam|espa[nñ]ol|alternativa|competidor|agencia)",
                        _normalized_text(search_html),
                        re.IGNORECASE,
                    )
                )
            if bool(has_spanish_competitor):
                continue
            vertical = _infer_arbitrage_vertical(name, tagline)
            opportunities.append(
                OpportunityInput(
                    business_name=f"arbitrage:{name}",
                    vertical=_normalize_vertical(vertical),
                    city="LATAM",
                    has_website=False,
                    has_bot=False,
                    estimated_market_size=int(row.get("estimated_market_size") or 260),
                    trend_score=float(row.get("trend_score") or 82.0),
                    pain_score=float(row.get("pain_score") or 0.79),
                    payment_capacity_score=float(row.get("payment_capacity_score") or 0.74),
                    competition_score=float(row.get("competition_score") or 0.18),
                    stack_fit_score=float(row.get("stack_fit_score") or 0.87),
                    source="arbitrage",
                    metadata={
                        "product_name": name,
                        "tagline": tagline,
                        "search_query": search_query,
                        "has_spanish_competitor": False,
                    },
                )
            )
        return opportunities

    async def scan_internal_demand(
        self,
        *,
        crm_texts: Iterable[str] | None = None,
        conversation_texts: Iterable[str] | None = None,
        since_days: int = 90,
    ) -> list[OpportunityInput]:
        texts: list[str] = []
        texts.extend(_sanitize_text(text) for text in (crm_texts or []) if _sanitize_text(text))
        texts.extend(_sanitize_text(text) for text in (conversation_texts or []) if _sanitize_text(text))

        if not texts:
            cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, since_days))
            try:
                async with AsyncSessionLocal() as session:
                    crm_rows = await session.execute(
                        select(CrmInteraction.content).where(CrmInteraction.created_at >= cutoff)
                    )
                    conv_rows = await session.execute(
                        select(ConversationLog.content).where(ConversationLog.timestamp >= cutoff)
                    )
                texts.extend(_sanitize_text(row[0]) for row in crm_rows.all() if row and _sanitize_text(str(row[0] or "")))
                texts.extend(_sanitize_text(row[0]) for row in conv_rows.all() if row and _sanitize_text(str(row[0] or "")))
            except Exception:
                pass

        counts: Counter[str] = Counter()
        samples: dict[str, list[str]] = {}
        for text in texts:
            theme = _resolve_theme(text)
            counts[theme] += 1
            samples.setdefault(theme, []).append(text)

        opportunities: list[OpportunityInput] = []
        for theme, count in counts.most_common():
            if count <= 0:
                continue
            opportunities.append(
                _estimate_signal_opportunity(
                    theme=theme,
                    count=count,
                    source="internal_demand",
                    sample_texts=samples.get(theme, []),
                )
            )
        return opportunities
