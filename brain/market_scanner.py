from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable, Iterable
from typing import Any

import httpx

from brain.revenue_operators.hunter import HunterOperator, OpportunityInput, _normalize_vertical

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
