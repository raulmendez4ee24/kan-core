from __future__ import annotations

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
        hook = {
            "educacion": "Si tu negocio depende de responder tarde, estás dejando dinero en la mesa.",
            "prueba_social": "Así cambia un negocio cuando deja de contestar manualmente todo.",
            "oferta": "Esto es exactamente lo que sí incluye nuestro paquete base.",
            "behind_the_scenes": "Así se ve por dentro una operación comercial bien automatizada.",
            "objeciones": "No es caro; caro es seguir perdiendo leads sin seguimiento.",
            "casos_de_uso": "Si fueras una clínica o restaurante, esto sería lo primero que automatizaría.",
            "autoridad": "La mayoría compra más tráfico antes de arreglar su cierre. Ese es el error.",
        }.get(pillar, "Hay una forma más rentable de hacer esto.")
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

    async def _send_to_raul(self, text: str) -> None:
        to_number = str(os.getenv("RAUL_WHATSAPP_TO") or "").strip()
        from_number = str(os.getenv("YCLOUD_WHATSAPP_FROM") or "").strip()
        if not to_number or not from_number or not str(text or "").strip():
            return
        await self.whatsapp_sender(from_number=from_number, to=to_number, text=text)

    async def generate_daily_post(
        self,
        *,
        reference_date: date | None = None,
        instagram_handle: str | None = None,
        website_url: str | None = None,
    ) -> ContentPost:
        ref_day = reference_date or _today_local()
        week_start = _week_start(ref_day)
        plan = await self._load_weekly_plan(week_start=week_start)
        if not plan:
            plan = await self.generate_weekly_content_plan(
                instagram_handle=instagram_handle,
                website_url=website_url,
                reference_date=ref_day,
            )
        day_index = (ref_day - week_start).days
        post = plan[day_index % len(plan)]
        message = (
            "Post de hoy\n\n"
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
            media_url = await self.asset_manager.generate_post_image(
                topic=post.topic,
                hook_text=post.hook,
                style_preset="premium",
                format="square",
                vertical=post.vertical,
                content_type=self._content_type_for_post(post),
                include_logo=True,
                asset_id=post.post_id,
            )
            post = post.model_copy(update={"media_url": media_url})
            await self.content_publisher.schedule_post(
                post,
                self._publish_at_for_post(post, reference_date=ref_day),
            )
        return post
