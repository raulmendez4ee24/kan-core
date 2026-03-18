from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional
from zoneinfo import ZoneInfo

import aiosqlite
import httpx
from pydantic import BaseModel, ConfigDict, Field

from tools.ycloud_client import send_ycloud_whatsapp_text_message

logger = logging.getLogger("kan_core.brand_director")
BRAND_TZ = ZoneInfo("America/Mexico_City")


async def _ce_retry(coro_factory: Callable[[], Awaitable[Any]], *, name: str, max_retries: int = 2) -> Any:
    """
    Runs a CE API coroutine with exponential backoff on timeout.
    Catches httpx.ReadTimeout and asyncio.TimeoutError and retries up to max_retries times.
    Waits 5s before retry 1, 10s before retry 2.
    """
    for attempt in range(max_retries + 1):
        try:
            return await coro_factory()
        except (httpx.ReadTimeout, asyncio.TimeoutError) as exc:
            if attempt < max_retries:
                wait = (attempt + 1) * 5
                logger.warning(
                    "CE timeout on '%s' (attempt %d/%d) — retry in %ds",
                    name, attempt + 1, max_retries + 1, wait,
                )
                await asyncio.sleep(wait)
            else:
                raise exc

_DEFAULT_PILLARS = [
    "educacion",
    "prueba_social",
    "oferta",
    "behind_the_scenes",
    "objeciones",
    "casos_de_uso",
    "autoridad",
]

_DEFAULT_FORMATS = ["reel", "carousel", "static", "story", "reel", "carousel", "static"]
_DEFAULT_TIMES = ["09:00", "11:30", "13:00", "18:00", "20:00", "10:00", "19:00"]
_PLATFORMS = ["instagram", "instagram", "instagram", "instagram", "instagram", "facebook", "whatsapp_status"]


class BrandIdentity(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    who: str
    what: str
    why: str
    tone: str


class BrandVisualIdentity(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    colors: list[str] = Field(default_factory=list)
    fonts: list[str] = Field(default_factory=list)
    style: str


class BrandNarrative(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    origin_story: str
    content_pillars: list[str] = Field(default_factory=list)


class BrandBible(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    instagram_handle: Optional[str] = None
    website_url: Optional[str] = None
    identity: BrandIdentity
    visual_identity: BrandVisualIdentity
    narrative: BrandNarrative
    current_score: float


class ContentPost(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    post_id: str
    day_index: int
    platform: str
    pillar: str
    vertical: str | None = None
    format: str
    topic: str
    hook: str
    full_script: str
    visual_direction: str
    caption: str
    hashtags: list[str] = Field(default_factory=list)
    best_posting_time: str
    cta: str
    media_url: str | None = None
    thumbnail_url: str | None = None


class FeedReport(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    handle: str
    color_consistency: float
    layout_variety: float
    brand_recognition: float
    quality: float
    overall_score: float
    recommendations: list[str] = Field(default_factory=list)


def _today_local() -> date:
    return datetime.now(BRAND_TZ).date()


def _week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())


def _planning_week_start(day: date) -> date:
    if day.weekday() == 6:  # Sunday generates the plan for the next week.
        return day + timedelta(days=1)
    return _week_start(day)


def _db_path() -> Path:
    configured = str(os.getenv("BRAND_DIRECTOR_DB_PATH") or "").strip()
    if configured:
        path = Path(configured)
    else:
        path = Path(__file__).resolve().parents[1] / "data" / "brand_director.sqlite3"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _strip_tags(html: str) -> str:
    text = re.sub(r"<script[\s\S]*?</script>", " ", html, flags=re.IGNORECASE)
    text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _extract_meta_content(html: str, name: str) -> str:
    patterns = [
        rf'<meta[^>]+name=["\']{re.escape(name)}["\'][^>]+content=["\']([^"\']+)["\']',
        rf'<meta[^>]+property=["\']{re.escape(name)}["\'][^>]+content=["\']([^"\']+)["\']',
    ]
    for pattern in patterns:
        match = re.search(pattern, html, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return ""


def _extract_title(html: str) -> str:
    match = re.search(r"<title>(.*?)</title>", html, flags=re.IGNORECASE | re.DOTALL)
    return re.sub(r"\s+", " ", match.group(1)).strip() if match else ""


def _extract_colors(html: str) -> list[str]:
    colors = re.findall(r"#[0-9a-fA-F]{3,8}", html)
    counter = Counter(color.lower() for color in colors)
    return [color for color, _ in counter.most_common(5)]


def _extract_fonts(html: str) -> list[str]:
    families = re.findall(r"font-family\s*:\s*([^;\"}]+)", html, flags=re.IGNORECASE)
    tokens: list[str] = []
    for family in families:
        for raw in family.split(","):
            clean = raw.strip().strip("'\"")
            if clean and clean.lower() not in {"sans-serif", "serif", "monospace", "system-ui"}:
                tokens.append(clean)
    return [font for font, _ in Counter(tokens).most_common(4)]


def _detect_tone(text: str) -> str:
    lowered = text.lower()
    if any(word in lowered for word in ("premium", "exclusive", "luxury", "elegante")):
        return "premium, directa y confiable"
    if any(word in lowered for word in ("rápido", "ahorra", "automatiza", "ventas")):
        return "comercial, pragmática y enfocada en resultados"
    return "cercana, clara y profesional"


def _detect_pillars(text: str) -> list[str]:
    lowered = text.lower()
    mapping = {
        "educacion": ["guía", "aprende", "cómo", "tips", "estrategia"],
        "prueba_social": ["caso", "testimonio", "clientes", "resultados"],
        "oferta": ["precio", "paquete", "promo", "servicio", "solución"],
        "behind_the_scenes": ["equipo", "proceso", "detrás", "desarrollo"],
        "objeciones": ["no tengo tiempo", "caro", "duda", "vale la pena"],
        "casos_de_uso": ["negocio", "clínica", "restaurante", "dentista", "spa"],
        "autoridad": ["experto", "años", "metodología", "especialista"],
    }
    scored: list[tuple[str, int]] = []
    for pillar, words in mapping.items():
        score = sum(lowered.count(word) for word in words)
        scored.append((pillar, score))
    ranked = [pillar for pillar, score in sorted(scored, key=lambda item: item[1], reverse=True) if score > 0]
    return (ranked[:4] or _DEFAULT_PILLARS[:4])


def _style_from_assets(colors: list[str], fonts: list[str], text: str) -> str:
    lowered = text.lower()
    if any(word in lowered for word in ("minimal", "clean", "simple")):
        return "minimalista y ordenado"
    if colors and len(colors) >= 3:
        return "visual con contraste alto y presencia marcada"
    if fonts:
        return f"consistente alrededor de {fonts[0]}"
    return "funcional, comercial y directo"


class BrandDirector:
    def __init__(
        self,
        *,
        db_path: str | Path | None = None,
        fetcher: Optional[Callable[[str], Awaitable[str]]] = None,
        whatsapp_sender: Callable[..., Any] = send_ycloud_whatsapp_text_message,
        content_publisher: Any | None = None,
        asset_manager: Any | None = None,
        hook_generator: Optional[Callable[..., Awaitable[str]]] = None,
    ) -> None:
        self.db_path = Path(db_path) if db_path else _db_path()
        self.fetcher = fetcher
        self.whatsapp_sender = whatsapp_sender
        if content_publisher is None:
            from brain.content_publisher import ContentPublisher

            self.content_publisher = ContentPublisher()
        else:
            self.content_publisher = content_publisher
        if asset_manager is None:
            from brain.asset_manager import AssetManager

            self.asset_manager = AssetManager()
        else:
            self.asset_manager = asset_manager
        self.hook_generator = hook_generator

    def _content_type_for_post(self, post: ContentPost) -> str:
        mapping = {
            "educacion": "educational_tip",
            "prueba_social": "testimonial",
            "oferta": "offer_launch",
            "behind_the_scenes": "workflow_upgrade",
            "objeciones": "objection_breaker",
            "casos_de_uso": "case_study",
            "autoridad": "brand_authority",
        }
        return mapping.get(str(post.pillar or "").strip(), "general")

    def _fallback_hook_for_pillar(self, pillar: str) -> str:
        return {
            "educacion": "Responder tarde cuesta ventas.",
            "prueba_social": "Esto cambió todo el cierre.",
            "oferta": "Esto sí incluye el paquete.",
            "behind_the_scenes": "Así opera un negocio ordenado.",
            "objeciones": "Perder leads sale más caro.",
            "casos_de_uso": "Esto automatizaría primero.",
            "autoridad": "Arregla cierre antes del tráfico.",
        }.get(pillar, "Automatiza antes de perder más.")

    def _normalize_hook(self, raw_hook: str, *, pillar: str) -> str:
        hook = re.sub(r"\s+", " ", str(raw_hook or "").strip())
        hook = re.sub(r"^[\"'“”‘’]+|[\"'“”‘’]+$", "", hook).strip()
        hook = hook.replace("\n", " ").strip()
        if not hook:
            return self._fallback_hook_for_pillar(pillar)
        words = hook.split()
        if words and words[0].lower() == "si":
            words = words[1:]
        filtered: list[str] = []
        for word in words:
            if word.lower() == "si" and not filtered:
                continue
            filtered.append(word)
        hook = " ".join(filtered[:6]).strip(" ,;:-")
        if not hook:
            return self._fallback_hook_for_pillar(pillar)
        if len(hook.split()) > 6:
            hook = " ".join(hook.split()[:6])
        return hook

    async def _generate_hook_with_claude(
        self,
        *,
        topic: str,
        pillar: str,
        platform: str,
        brand_bible: BrandBible,
    ) -> str:
        if self.hook_generator is not None:
            generated = await self.hook_generator(
                topic=topic,
                pillar=pillar,
                platform=platform,
                brand_bible=brand_bible,
            )
            return self._normalize_hook(generated, pillar=pillar)

        api_key = str(os.getenv("ANTHROPIC_API_KEY") or "").strip()
        if not api_key:
            return self._fallback_hook_for_pillar(pillar)

        model = str(os.getenv("BRAND_DIRECTOR_MODEL") or "claude-sonnet-4-6").strip()
        prompt = (
            "Generate a hook for an Instagram post. Maximum 6 words. Must stop the scroll. "
            "Direct, provocative, or data-driven. No conditional sentences. No soft language. "
            "Think billboard, not paragraph.\n\n"
            f"Brand: {brand_bible.identity.who}\n"
            f"What: {brand_bible.identity.what}\n"
            f"Why: {brand_bible.identity.why}\n"
            f"Tone: {brand_bible.identity.tone}\n"
            f"Platform: {platform}\n"
            f"Pillar: {pillar}\n"
            f"Topic: {topic}\n\n"
            "Return only the hook text. No quotes. No bullets. No explanation."
        )

        payload = {
            "model": model,
            "max_tokens": 40,
            "messages": [{"role": "user", "content": prompt}],
        }
        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.post(
                    "https://api.anthropic.com/v1/messages",
                    json=payload,
                    headers=headers,
                )
                response.raise_for_status()
                data = response.json()
            text = ""
            for block in data.get("content", []):
                if block.get("type") == "text":
                    text = str(block.get("text") or "").strip()
                    break
            return self._normalize_hook(text, pillar=pillar)
        except Exception:
            logger.exception("Brand Director hook generation failed; using fallback hook")
            return self._fallback_hook_for_pillar(pillar)

    async def initialize(self) -> None:
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS weekly_content_plans (
                    week_start TEXT PRIMARY KEY,
                    generated_at TEXT NOT NULL,
                    plan_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS brand_bibles (
                    key TEXT PRIMARY KEY,
                    generated_at TEXT NOT NULL,
                    content_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS alert_cooldown (
                    key TEXT PRIMARY KEY,
                    last_alert_at TEXT NOT NULL
                );
                """
            )
            await conn.commit()

    async def _fetch(self, url: str) -> str:
        if self.fetcher is not None:
            return await self.fetcher(url)
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            response = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
            response.raise_for_status()
            return response.text

    async def brand_audit(self, instagram_handle: str, website_url: str) -> BrandBible:
        await self.initialize()
        website_html = ""
        instagram_html = ""
        if website_url:
            try:
                website_html = await self._fetch(website_url)
            except Exception:
                logger.exception("Brand audit website fetch failed for %s", website_url)
        if instagram_handle:
            ig_url = f"https://www.instagram.com/{instagram_handle.strip().lstrip('@')}/"
            try:
                instagram_html = await self._fetch(ig_url)
            except Exception:
                logger.exception("Brand audit Instagram fetch failed for %s", instagram_handle)

        combined_html = "\n".join(part for part in [website_html, instagram_html] if part)
        combined_text = _strip_tags(combined_html)
        title = _extract_title(website_html) or _extract_title(instagram_html)
        description = (
            _extract_meta_content(website_html, "description")
            or _extract_meta_content(website_html, "og:description")
            or _extract_meta_content(instagram_html, "description")
            or combined_text[:220]
        )

        who = title or instagram_handle or website_url or "Marca en crecimiento"
        what = description[:160] or "Servicio con propuesta de valor poco definida."
        why = (
            "Ayudar al cliente a tomar acción más rápido con una propuesta clara y reconocible."
            if combined_text
            else "Definir una identidad comercial clara para acelerar reconocimiento y ventas."
        )
        tone = _detect_tone(combined_text or description)
        colors = _extract_colors(combined_html)
        fonts = _extract_fonts(combined_html)
        pillars = _detect_pillars(f"{title} {description} {combined_text}")
        style = _style_from_assets(colors, fonts, combined_text)

        score = 35.0
        score += min(len(colors) * 8, 24)
        score += min(len(fonts) * 6, 18)
        score += 10 if website_html else 0
        score += 10 if instagram_html else 0
        score += min(len(pillars) * 4, 16)
        score = round(min(score, 100.0), 2)

        bible = BrandBible(
            instagram_handle=instagram_handle or None,
            website_url=website_url or None,
            identity=BrandIdentity(who=who, what=what, why=why, tone=tone),
            visual_identity=BrandVisualIdentity(colors=colors, fonts=fonts, style=style),
            narrative=BrandNarrative(
                origin_story=description[:220] or "La marca necesita una historia de origen más clara.",
                content_pillars=pillars,
            ),
            current_score=score,
        )

        key = f"{instagram_handle.strip().lower()}|{website_url.strip().lower()}"
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute(
                """
                INSERT INTO brand_bibles (key, generated_at, content_json)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    generated_at = excluded.generated_at,
                    content_json = excluded.content_json
                """,
                (key, datetime.now(timezone.utc).isoformat(), bible.model_dump_json()),
            )
            await conn.commit()
        return bible

    async def analyze_feed_consistency(self, handle: str) -> FeedReport:
        html = ""
        if handle:
            try:
                html = await self._fetch(f"https://www.instagram.com/{handle.strip().lstrip('@')}/")
            except Exception:
                logger.exception("Feed consistency fetch failed for %s", handle)
        text = _strip_tags(html)
        posts = re.findall(r"(?:alt=|aria-label=|\"caption\":)([^<>]{10,240})", html, flags=re.IGNORECASE)
        if not posts:
            posts = re.findall(r"post[^.]{0,160}", text, flags=re.IGNORECASE)
        posts = posts[:18]
        colors = _extract_colors(html)
        color_consistency = round(min(100.0, 100.0 / max(len(colors), 1)), 2) if colors else 30.0
        layout_markers = set(
            marker
            for marker in ("reel", "carousel", "video", "photo", "story")
            if marker in text.lower()
        )
        layout_variety = round(min(100.0, len(layout_markers) * 22.5), 2) if layout_markers else 35.0
        brand_hits = sum(1 for item in posts if handle.lower().strip("@") in item.lower()) if handle else 0
        brand_recognition = round(min(100.0, 35.0 + brand_hits * 6.0), 2)
        avg_post_len = (sum(len(item) for item in posts) / len(posts)) if posts else 0.0
        quality = round(min(100.0, 30.0 + avg_post_len / 3.0), 2) if posts else 25.0
        overall = round((color_consistency + layout_variety + brand_recognition + quality) / 4.0, 2)

        recommendations: list[str] = []
        if color_consistency < 55:
            recommendations.append("Reduce la dispersión cromática y repite 2-3 colores base en las próximas 3 semanas.")
        if layout_variety < 55:
            recommendations.append("Alterna reels, carruseles y estáticos para que el feed no se vea monótono.")
        if brand_recognition < 60:
            recommendations.append("Incluye más branding visible: logo, tipografía recurrente y frase ancla en portada.")
        if quality < 60:
            recommendations.append("Sube el estándar visual: mejor portada, textos más cortos y enfoque más limpio.")
        if not recommendations:
            recommendations.append("La base del feed ya es consistente; enfoca la mejora en convertir más con CTA más directos.")

        return FeedReport(
            handle=handle,
            color_consistency=color_consistency,
            layout_variety=layout_variety,
            brand_recognition=brand_recognition,
            quality=quality,
            overall_score=overall,
            recommendations=recommendations,
        )

    def _build_post(
        self,
        *,
        plan_week_start: date,
        day_index: int,
        platform: str,
        pillar: str,
        brand_bible: BrandBible,
    ) -> ContentPost:
        topic = {
            "educacion": "Error común que frena ventas",
            "prueba_social": "Resultado real de cliente",
            "oferta": "Oferta base y para quién sí aplica",
            "behind_the_scenes": "Cómo se ejecuta tu servicio",
            "objeciones": "Objeción de precio o confianza",
            "casos_de_uso": "Caso por nicho",
            "autoridad": "Punto de vista experto",
        }.get(pillar, "Contenido estratégico")
        hook = self._fallback_hook_for_pillar(pillar)
        content_format = _DEFAULT_FORMATS[day_index % len(_DEFAULT_FORMATS)]
        best_time = _DEFAULT_TIMES[day_index % len(_DEFAULT_TIMES)]
        cta = "Escríbeme y te digo cómo aterrizarlo en tu negocio."
        full_script = (
            f"{hook}\n\n"
            f"Contexto: {brand_bible.identity.what}\n"
            f"Desarrollo: explica el problema, muestra el contraste antes/después y aterriza una acción concreta.\n"
            f"Cierre: {cta}"
        )
        visual_direction = (
            f"Usa colores {', '.join(brand_bible.visual_identity.colors[:3]) or 'de la marca'}, "
            f"tipografía {brand_bible.visual_identity.fonts[0] if brand_bible.visual_identity.fonts else 'consistente'}, "
            f"y estilo {brand_bible.visual_identity.style}."
        )
        try:
            from brain.marketing_skills import load_skill

            _social_skill = load_skill("social_content")
        except Exception:
            _social_skill = ""
        caption = (
            f"{hook} {brand_bible.identity.why} "
            f"{cta}"
        )
        # Enrich the writing guide with social content best practices when skill is available
        if _social_skill:
            full_script = (
                f"{hook}\n\n"
                f"Contexto: {brand_bible.identity.what}\n"
                f"Desarrollo: explica el problema, muestra el contraste antes/después y aterriza una acción concreta.\n"
                f"Cierre: {cta}\n\n"
                f"---\nGuía de contenido social:\n{_social_skill[:600]}"
            )
        hashtags = [
            "#marketing",
            "#ventas",
            "#automatizacion",
            f"#{pillar}",
            f"#{platform.replace('-', '')}",
        ]
        return ContentPost(
            post_id=f"{plan_week_start.isoformat()}-{day_index}",
            day_index=day_index,
            platform=platform,
            pillar=pillar,
            format=content_format,
            topic=topic,
            hook=hook,
            full_script=full_script,
            visual_direction=visual_direction,
            caption=caption,
            hashtags=hashtags,
            best_posting_time=best_time,
            cta=cta,
            media_url=str(os.getenv("CONTENT_DEFAULT_MEDIA_URL") or "").strip() or None,
        )

    def _publish_at_for_post(self, post: ContentPost, *, reference_date: date) -> datetime:
        hour = 9
        minute = 0
        raw_time = str(post.best_posting_time or "").strip()
        try:
            if ":" in raw_time:
                parsed_hour, parsed_minute = raw_time.split(":", 1)
                hour = int(parsed_hour)
                minute = int(parsed_minute)
        except ValueError:
            hour = 9
            minute = 0
        local_dt = datetime(
            reference_date.year,
            reference_date.month,
            reference_date.day,
            hour,
            minute,
            tzinfo=BRAND_TZ,
        )
        return local_dt.astimezone(timezone.utc)

    def _rebuild_post_with_hook(self, *, post: ContentPost, hook: str, brand_bible: BrandBible | None) -> ContentPost:
        normalized_hook = self._normalize_hook(hook, pillar=post.pillar)
        context_line = (
            brand_bible.identity.what
            if brand_bible is not None
            else "desarrolla el problema, el contraste antes/después y la acción concreta"
        )
        full_script = (
            f"{normalized_hook}\n\n"
            f"Contexto: {context_line}\n"
            f"Desarrollo: explica el problema, muestra el contraste antes/después y aterriza una acción concreta.\n"
            f"Cierre: {post.cta}"
        )
        caption = f"{normalized_hook} {post.cta}".strip()
        return post.model_copy(
            update={
                "hook": normalized_hook,
                "full_script": full_script,
                "caption": caption,
            }
        )

    async def generate_weekly_content_plan(
        self,
        *,
        instagram_handle: str | None = None,
        website_url: str | None = None,
        brand_bible: BrandBible | None = None,
        reference_date: date | None = None,
    ) -> list[ContentPost]:
        await self.initialize()
        ref_day = reference_date or _today_local()
        week_start = _planning_week_start(ref_day)
        bible = brand_bible or await self.brand_audit(instagram_handle or "", website_url or "")
        pillars = (bible.narrative.content_pillars or _DEFAULT_PILLARS)[:]
        while len(pillars) < 7:
            pillars.extend(_DEFAULT_PILLARS)
        plan = [
            self._build_post(
                plan_week_start=week_start,
                day_index=index,
                platform=_PLATFORMS[index % len(_PLATFORMS)],
                pillar=pillars[index],
                brand_bible=bible,
            )
            for index in range(7)
        ]
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute(
                """
                INSERT INTO weekly_content_plans (week_start, generated_at, plan_json)
                VALUES (?, ?, ?)
                ON CONFLICT(week_start) DO UPDATE SET
                    generated_at = excluded.generated_at,
                    plan_json = excluded.plan_json
                """,
                (
                    week_start.isoformat(),
                    datetime.now(timezone.utc).isoformat(),
                    json.dumps([item.model_dump(mode="json") for item in plan], ensure_ascii=False),
                ),
            )
            await conn.commit()
        return plan

    async def _load_weekly_plan(self, *, week_start: date) -> list[ContentPost]:
        await self.initialize()
        async with aiosqlite.connect(self.db_path) as conn:
            cursor = await conn.execute(
                "SELECT plan_json FROM weekly_content_plans WHERE week_start = ?",
                (week_start.isoformat(),),
            )
            row = await cursor.fetchone()
            await cursor.close()
        if not row:
            return []
        payload = json.loads(str(row[0]))
        return [ContentPost.model_validate(item) for item in payload]

    async def _send_to_raul(self, text: str, *, bypass_cooldown: bool = False) -> None:
        to_number = str(os.getenv("RAUL_WHATSAPP_TO") or "").strip()
        from_number = str(os.getenv("YCLOUD_WHATSAPP_FROM") or "").strip()
        if not to_number or not from_number or not str(text or "").strip():
            return
        if not bypass_cooldown:
            cooldown_hours = float(os.getenv("BRAND_DIRECTOR_ALERT_COOLDOWN_HOURS") or "6")
            now = datetime.now(timezone.utc)
            await self.initialize()
            async with aiosqlite.connect(self.db_path) as conn:
                cursor = await conn.execute(
                    "SELECT last_alert_at FROM alert_cooldown WHERE key = 'default'"
                )
                row = await cursor.fetchone()
                await cursor.close()
                if row:
                    last_alert_at = datetime.fromisoformat(str(row[0]))
                    elapsed = (now - last_alert_at).total_seconds() / 3600
                    if elapsed < cooldown_hours:
                        logger.debug(
                            "_send_to_raul suppressed by cooldown (%.1fh < %.1fh)",
                            elapsed,
                            cooldown_hours,
                        )
                        return
                await conn.execute(
                    """
                    INSERT INTO alert_cooldown (key, last_alert_at)
                    VALUES ('default', ?)
                    ON CONFLICT(key) DO UPDATE SET last_alert_at = excluded.last_alert_at
                    """,
                    (now.isoformat(),),
                )
                await conn.commit()
        await self.whatsapp_sender(from_number=from_number, to=to_number, text=text)

    # ----------------------------------------------------------------
    # BRAND DIRECTOR SYSTEM PROMPT — temperature 0.4, execution phase
    # ----------------------------------------------------------------
    _BD_SYSTEM = (
        "Eres un director de marca que EJECUTA una visión creativa ya definida. "
        "No improvisas — traduces un concepto creativo en un post completo listo para producir.\n\n"
        "REGLAS DE COPY:\n"
        "- El hook ya está decidido. No lo cambies. Úsalo exacto.\n"
        "- El body copy expande el hook pero NO lo explica. Profundiza la provocación.\n"
        "- Máximo 1200 caracteres de body. Español mexicano natural.\n"
        "- CTA: parte de la conversación, no botón pegado al final.\n"
        "- Emojis: máximo 3, solo si aportan. Cero es válido.\n"
        "- Hashtags: máximo 15. 5 de nicho + 5 de alcance medio + 5 de alto volumen.\n\n"
        "REGLAS DE VISUAL BRIEF:\n"
        "- EXTREMADAMENTE específico. No 'foto estética'. Sí: composición, iluminación, "
        "paleta hex, elementos, textura, mood, qué NO debe aparecer.\n"
        "- Si la dirección incluye ruptura, el brief debe hacer que sea el punto focal.\n\n"
        "Responde SOLO en JSON:\n"
        "{\n"
        '  "post": {\n'
        '    "hook": "el hook ganador exacto",\n'
        '    "body_copy": "el copy completo",\n'
        '    "cta": "call to action (o null)",\n'
        '    "hashtags": ["lista"],\n'
        '    "suggested_time": "HH:MM"\n'
        "  },\n"
        '  "visual_brief": {\n'
        '    "format": "single_image|carousel|reel_cover",\n'
        '    "aspect_ratio": "1:1|4:5|9:16",\n'
        '    "description": "descripción completa del visual",\n'
        '    "color_palette": ["#hex1", "#hex2"],\n'
        '    "typography": {"text_on_image": "texto o null", "font_mood": "estilo", "placement": "dónde"},\n'
        '    "composition": "descripción espacial",\n'
        '    "lighting": "tipo de iluminación",\n'
        '    "must_avoid": ["elementos prohibidos"],\n'
        '    "rupture_element": "cómo se manifiesta la ruptura visualmente",\n'
        '    "style_preset_suggestion": "premium|editorial|human|documentary|glassmorphism_dark|terminal_luxury|custom"\n'
        "  }\n"
        "}"
    )

    async def _build_post_from_direction(
        self,
        *,
        post: "ContentPost",
        direction: dict,
        best_hook: str,
        brand_bible: "BrandBible",
    ) -> "ContentPost":
        """
        Uses BRAND_DIRECTOR_SYSTEM (temp 0.4) to build the complete post
        from the creative direction + hook. Falls back to _rebuild_post_with_hook.
        """
        api_key = str(os.getenv("ANTHROPIC_API_KEY") or "").strip()
        if not api_key:
            return self._rebuild_post_with_hook(post=post, hook=best_hook, brand_bible=brand_bible)

        model = str(os.getenv("BRAND_DIRECTOR_MODEL") or "claude-sonnet-4-6").strip()
        import json as _json
        from brain.creative_engine.universal_director import parse_llm_json as _parse_llm_json

        user_msg = (
            f"DIRECCIÓN CREATIVA (con ruptura integrada):\n"
            f"{_json.dumps(direction, ensure_ascii=False, indent=2)}\n\n"
            f"HOOK GANADOR (usa exacto): {best_hook}\n\n"
            f"BRAND DNA:\n"
            f"- Marca: {brand_bible.identity.who}\n"
            f"- Propuesta: {brand_bible.identity.what}\n"
            f"- Por qué: {brand_bible.identity.why}\n"
            f"- Tono: {brand_bible.identity.tone}\n"
            f"- Pilar: {post.pillar} | Plataforma: {post.platform}\n\n"
            "Produce el post completo. El hook NO se modifica. Responde solo en JSON."
        )

        payload = {
            "model": model,
            "max_tokens": 3000,
            "temperature": 0.4,
            "system": self._BD_SYSTEM,
            "messages": [{"role": "user", "content": user_msg}],
        }
        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=40.0) as client:
                response = await client.post(
                    "https://api.anthropic.com/v1/messages",
                    json=payload,
                    headers=headers,
                )
                response.raise_for_status()
                data = response.json()

            raw = ""
            for block in data.get("content", []):
                if block.get("type") == "text":
                    raw = str(block.get("text") or "").strip()
                    break

            bd_out = _parse_llm_json(raw)
            post_data = bd_out.get("post") or {}
            visual = bd_out.get("visual_brief") or {}

            hook = str(post_data.get("hook") or best_hook).strip()
            body = str(post_data.get("body_copy") or "").strip()
            cta = str(post_data.get("cta") or post.cta).strip()
            hashtags = list(post_data.get("hashtags") or post.hashtags)
            suggested_time = str(post_data.get("suggested_time") or post.best_posting_time)
            visual_desc = str(visual.get("description") or post.visual_direction)
            style_hint = str(visual.get("style_preset_suggestion") or "premium")

            caption = f"{hook}\n\n{body[:400]}\n\n{cta}".strip() if body else f"{hook}\n\n{cta}"

            updated = post.model_copy(
                update={
                    "hook": hook,
                    "full_script": f"{hook}\n\n{body}" if body else hook,
                    "caption": caption,
                    "cta": cta,
                    "hashtags": hashtags,
                    "best_posting_time": suggested_time,
                    "visual_direction": visual_desc,
                }
            )
            # Attach style hint so image generation can use it
            object.__setattr__(updated, "_style_hint", style_hint)
            return updated

        except Exception:
            logger.exception("_build_post_from_direction failed; using rebuild fallback")
            return self._rebuild_post_with_hook(post=post, hook=best_hook, brand_bible=brand_bible)

    async def generate_daily_post(
        self,
        *,
        reference_date: date | None = None,
        instagram_handle: str | None = None,
        website_url: str | None = None,
    ) -> ContentPost:
        ref_day = reference_date or _today_local()
        week_start = _week_start(ref_day)
        daily_bible: BrandBible | None = None

        # Load / generate the weekly skeleton plan
        plan = await self._load_weekly_plan(week_start=week_start)
        if not plan:
            if instagram_handle or website_url:
                daily_bible = await self.brand_audit(instagram_handle or "", website_url or "")
            plan = await self.generate_weekly_content_plan(
                instagram_handle=instagram_handle,
                website_url=website_url,
                brand_bible=daily_bible,
                reference_date=ref_day,
            )
        day_index = (ref_day - week_start).days
        post = plan[day_index % len(plan)]
        if daily_bible is None and (instagram_handle or website_url):
            daily_bible = await self.brand_audit(instagram_handle or "", website_url or "")

        bible = daily_bible or BrandBible(
            identity=BrandIdentity(
                who="KAN Logic",
                what="Automatización y crecimiento para negocios.",
                why="Convertir más conversaciones en ventas.",
                tone="directa y comercial",
            ),
            visual_identity=BrandVisualIdentity(colors=[], fonts=[], style="premium"),
            narrative=BrandNarrative(origin_story="", content_pillars=[]),
            current_score=70.0,
        )

        # ════════════════════════════════════════════════════════
        # CREATIVE ENGINE v3 PIPELINE — image-first, critic async
        # Order: hook_lab → image → brand director → QC → publish
        # creative_critic fires async after publish (non-blocking).
        # ════════════════════════════════════════════════════════
        best_hook: str = self._fallback_hook_for_pillar(post.pillar)
        _media_url_early: str | None = None
        _style_preset = "premium"
        _architect_prompt: dict | None = None

        # ── Step 1: Context (cache reads — no LLM) ────────────
        culture: dict = {}
        competitor: dict = {}
        top_hooks: list[str] = []
        failed_hooks: list[str] = []
        try:
            from brain.creative_engine import culture_radar as _cr
            from brain.creative_engine import competitor_watch as _cw
            from brain.creative_memory import get_top_patterns
            culture = await asyncio.wait_for(
                _cr.scan(
                    client_industry=str(
                        bible.narrative.origin_story[:80]
                        if bible.narrative.origin_story
                        else "servicios digitales"
                    ),
                    client_audience="dueños de PyMEs y directores comerciales",
                ),
                timeout=15.0,
            )
            competitor = _cw._get_cached(_cw._week_start_str()) or {}
            top_pats = get_top_patterns(domain="campaign_execution", limit=5)
            top_hooks = [p.hook for p in top_pats if p.hook]
            failed_hooks = list(competitor.get("hooks_to_avoid") or [])
        except Exception:
            logger.warning("CE step 1 (context) failed; continuing with empty context")

        # ── Step 2: HookLab (FIRST — no direction needed) ─────
        try:
            from brain.creative_engine import hook_lab as _hlab
            lab_result = await asyncio.wait_for(
                _hlab.generate_hooks(
                    topic=post.topic,
                    pillar=post.pillar,
                    platform=post.platform,
                    brand_who=bible.identity.who,
                    brand_what=bible.identity.what,
                    brand_tone=bible.identity.tone,
                    culture_context=str(culture.get("overall_mood") or ""),
                    competitor_gap=str(competitor.get("opportunity_gap") or ""),
                    top_hooks=top_hooks,
                    failed_hooks=failed_hooks,
                ),
                timeout=160.0,
            )
            if lab_result.get("winner"):
                best_hook = str(lab_result["winner"])
                logger.info("HookLab winner: %s", best_hook[:60])
        except Exception:
            logger.warning("CE step 2 (hook_lab) failed or timed out; using fallback hook")

        # ── Step 3: Image generation (IMMEDIATELY after hook) ─
        try:
            from brain.creative_engine.style_dna import get_best_style
            _style_hint = getattr(post, "_style_hint", None)
            _style_preset = _style_hint or get_best_style("kan_logic")
        except Exception:
            pass
        try:
            from brain.creative_engine.image_architect import build_cinematic_prompt
            from brain.creative_engine.universal_director import brand_bible_to_dna
            _bible_dna_img = brand_bible_to_dna(bible)
            _architect_prompt = await asyncio.wait_for(
                build_cinematic_prompt(
                    topic=post.topic,
                    hook_text=best_hook,
                    content_type=self._content_type_for_post(post),
                    pillar=post.pillar,
                    vertical=str(post.vertical or ""),
                    platform=post.platform,
                    brand_dna=_bible_dna_img,
                    creative_direction=None,
                    rupture_description="",
                ),
                timeout=130.0,
            )
            if _architect_prompt.get("style_preset") and _architect_prompt["style_preset"] != "custom":
                _style_preset = _architect_prompt["style_preset"]
        except Exception:
            logger.warning("CE step 3a (image_architect) failed; using base style")
        if post.platform == "instagram":
            try:
                _media_url_early = await self.asset_manager.generate_post_image(
                    topic=post.topic,
                    hook_text=best_hook,
                    style_preset=_style_preset,
                    format="square",
                    vertical=post.vertical,
                    content_type=self._content_type_for_post(post),
                    include_logo=True,
                    asset_id=post.post_id,
                    architect_prompt=_architect_prompt,
                )
                logger.info("CE step 3: early image generated → %s", (_media_url_early or "")[:60])
            except Exception:
                logger.exception("CE step 3b (image generation) failed; will retry after brand director")

        # ── Step 4: hook_generator override (optional) ────────
        if self.hook_generator is not None:
            best_hook = await self._generate_hook_with_claude(
                topic=post.topic,
                pillar=post.pillar,
                platform=post.platform,
                brand_bible=bible,
            )

        # ════════════════════════════════════════════════════════
        # Step 5 — Brand Director execution (temp 0.4, no critic direction)
        # ════════════════════════════════════════════════════════
        post = await self._build_post_from_direction(
            post=post,
            direction={},
            best_hook=best_hook,
            brand_bible=bible,
        )

        # ════════════════════════════════════════════════════════
        # Step 6 — Attach image + QC + Schedule
        # ════════════════════════════════════════════════════════
        message = (
            "Post de hoy (CE v3)\n\n"
            f"Plataforma: {post.platform}\n"
            f"Pilar: {post.pillar}\n"
            f"Formato: {post.format}\n"
            f"Hook: {post.hook}\n\n"
            f"Script:\n{post.full_script}\n\n"
            f"Dirección visual: {post.visual_direction}\n\n"
            f"CTA: {post.cta}\n"
            f"Hora sugerida: {post.best_posting_time}"
        )
        await self._send_to_raul(message)

        if post.platform == "instagram":
            _is_carousel = str(post.format or "").lower() == "carousel"

            if _is_carousel:
                # ── Carousel path ──────────────────────────────────────────
                _carousel_brief: dict = {}
                try:
                    from brain.creative_engine.carousel_architect import design_carousel
                    from brain.creative_engine.universal_director import brand_bible_to_dna
                    _bible_dna = brand_bible_to_dna(bible)
                    _ca_direction: dict = {}
                    _ca_hook = post.hook
                    _ca_cta = post.cta or ""
                    _ca_script = post.full_script or ""
                    _carousel_brief = await asyncio.wait_for(
                        design_carousel(
                            creative_direction=_ca_direction,
                            full_script=_ca_script,
                            selected_hook=_ca_hook,
                            cta=_ca_cta,
                            brand_dna=_bible_dna,
                            num_slides=7,
                        ),
                        timeout=130.0,
                    )
                except Exception:
                    logger.exception("CarouselArchitect failed; falling back to single image")
                    _is_carousel = False

                if _is_carousel and _carousel_brief.get("slides"):
                    try:
                        carousel_urls = await self.asset_manager.generate_carousel(
                            slides_briefs=list(_carousel_brief["slides"]),
                            topic=post.topic,
                            style_preset=_style_preset,
                            include_logo=True,
                        )
                        if carousel_urls:
                            post = post.model_copy(update={
                                "media_url": carousel_urls[0],
                                "carousel_urls": carousel_urls,
                                "carousel_brief": _carousel_brief,
                            })
                        else:
                            _is_carousel = False
                    except Exception:
                        logger.exception("Carousel image generation failed; falling back to single image")
                        _is_carousel = False

            if not _is_carousel:
                # ── Single-image path ──────────────────────────────────────
                if _media_url_early:
                    # Image already generated in step 3 — reuse it
                    post = post.model_copy(update={"media_url": _media_url_early})
                else:
                    # Step 3 failed — try once more with final direction/hook
                    try:
                        from brain.creative_engine.image_architect import build_cinematic_prompt
                        from brain.creative_engine.universal_director import brand_bible_to_dna
                        _bible_dna2 = brand_bible_to_dna(bible)
                        _architect_prompt2 = await asyncio.wait_for(
                            build_cinematic_prompt(
                                topic=post.topic,
                                hook_text=post.hook,
                                content_type=self._content_type_for_post(post),
                                pillar=post.pillar,
                                vertical=str(post.vertical or ""),
                                platform=post.platform,
                                brand_dna=_bible_dna2,
                                creative_direction=None,
                                rupture_description="",
                            ),
                            timeout=130.0,
                        )
                        if _architect_prompt2.get("style_preset") and _architect_prompt2["style_preset"] != "custom":
                            _style_preset = _architect_prompt2["style_preset"]
                        _architect_prompt = _architect_prompt2
                    except Exception:
                        logger.warning("Late ImageArchitect also failed; using base style")
                    media_url = await self.asset_manager.generate_post_image(
                        topic=post.topic,
                        hook_text=post.hook,
                        style_preset=_style_preset,
                        format="square",
                        vertical=post.vertical,
                        content_type=self._content_type_for_post(post),
                        include_logo=True,
                        asset_id=post.post_id,
                        architect_prompt=_architect_prompt,
                    )
                    post = post.model_copy(update={"media_url": media_url})

            # Creative QC
            try:
                from brain.agents.quality_control_agent import creative_review
                qc_result = await creative_review(
                    post_copy=post.caption or post.full_script,
                    original_direction=None,
                    rupture_description="",
                )
                verdict = str(qc_result.get("verdict") or "PUBLISH")
                if verdict == "ESCALATE":
                    await self._send_to_raul(
                        f"⚠️ Creative QC ESCALATE\n"
                        f"Post: {post.post_id}\n"
                        f"Motivo: {qc_result.get('refinement_notes') or qc_result.get('regeneration_reason') or 'Revisar manualmente'}"
                    )
                elif verdict == "REFINE":
                    logger.info("CreativeQC REFINE: %s", str(qc_result.get("refinement_notes") or "")[:100])
                logger.info("CreativeQC verdict: %s", verdict)
            except Exception:
                logger.exception("CreativeQC failed; proceeding with publish")

            await self.content_publisher.schedule_post(
                post,
                self._publish_at_for_post(post, reference_date=ref_day),
            )

            # Record in style_dna for future best_style selection
            try:
                from brain.creative_engine.style_dna import record_post_published
                record_post_published("kan_logic", _style_preset)
            except Exception:
                pass

            # ── Step 7: Fire creative_critic async (non-blocking) ──
            asyncio.create_task(
                self._run_creative_critic_async(
                    post=post,
                    bible=bible,
                    culture=culture,
                    competitor=competitor,
                    top_hooks=top_hooks,
                    failed_hooks=failed_hooks,
                )
            )

        return post

    async def _run_creative_critic_async(
        self,
        *,
        post: ContentPost,
        bible: BrandBible,
        culture: dict,
        competitor: dict,
        top_hooks: list,
        failed_hooks: list,
    ) -> None:
        """Fire-and-forget: run strategist → critic → rebel after publish. Results are logged only."""
        try:
            from brain.agents.creative_strategist_agent import generate_creative_directions
            brand_dna = {
                "who": bible.identity.who,
                "what": bible.identity.what,
                "why": bible.identity.why,
                "tone": bible.identity.tone,
                "colors": bible.visual_identity.colors,
                "style": bible.visual_identity.style,
            }
            strategist_result = await asyncio.wait_for(
                generate_creative_directions(
                    client_name=bible.identity.who,
                    client_industry="servicios de automatización y CRM",
                    client_audience="dueños de PyMEs y directores comerciales en México",
                    platform=post.platform,
                    content_pillar=post.pillar,
                    brand_dna=brand_dna,
                    culture_signals=list(culture.get("signals") or []),
                    top_performers=top_hooks[:3],
                    bottom_performers=failed_hooks[:2],
                    competitor_summary=str(competitor.get("dominant_pattern") or ""),
                ),
                timeout=100.0,
            )
            directions = list(strategist_result.get("directions") or [])
            if not directions:
                return
            from brain.agents.creative_critic_agent import evaluate_directions
            critic_result = await asyncio.wait_for(
                evaluate_directions(
                    directions,
                    client_context=f"{bible.identity.who} — {post.pillar}",
                ),
                timeout=130.0,
            )
            winner_info = critic_result.get("winner") or {}
            winner_id = int(winner_info.get("direction_id") or 1)
            winner_direction = next(
                (d for d in directions if int(d.get("direction_id") or 0) == winner_id),
                directions[0],
            )
            from brain.creative_engine import creative_rebel as _rebel
            rebel_result = await asyncio.wait_for(
                _rebel.apply_rupture(
                    winner_direction=winner_direction,
                    hooks_to_avoid=failed_hooks,
                ),
                timeout=100.0,
            )
            logger.info(
                "CE async critic done for post %s | rupture: %s",
                post.post_id,
                str(rebel_result.get("rupture_description") or "")[:80],
            )
        except Exception:
            logger.warning("CE async critic task failed for post %s", post.post_id)
