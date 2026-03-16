from __future__ import annotations

import hashlib
import html
import json
import logging
import os
import sqlite3
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field

from brain.creative_memory import CreativePattern, get_top_patterns
from brain.revenue_operators.hunter import OpportunityInput

logger = logging.getLogger("kan_core.offer_forge")

_VERTICAL_HINTS: dict[str, tuple[str, ...]] = {
    "clinics": ("clinica", "clínica", "medic", "doctor", "salud", "paciente", "consulta"),
    "dentists": ("dent", "odont", "sonrisa", "ortodoncia", "dental"),
    "restaurants": ("restaurant", "restaurante", "reserv", "menu", "comida", "mesa"),
    "spas": ("spa", "belleza", "estet", "facial", "wellness"),
    "barbershops": ("barber", "barberia", "barbería", "corte", "peluquer"),
    "services": ("negocio", "servicio", "cliente", "ventas", "whatsapp", "leads"),
}


class OfferDetails(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    description: str
    features: list[str] = Field(default_factory=list)
    price_mxn: int
    guarantee: str
    timeline: str


class OutreachStep(BaseModel):
    model_config = ConfigDict(extra="ignore")

    day: int
    label: str
    goal: str
    message: str


class CompleteOffer(BaseModel):
    model_config = ConfigDict(extra="ignore")

    offer_id: str
    vertical: str
    opportunity_name: str
    offer: OfferDetails
    whatsapp_copy: str
    outreach_sequence: list[OutreachStep] = Field(default_factory=list)
    patterns_used: list[dict[str, Any]] = Field(default_factory=list)
    generation_model: str
    stripe_payment_url: str
    landing_path: str | None = None


class AdCreative(BaseModel):
    model_config = ConfigDict(extra="ignore")

    headline: str
    body: str
    cta_button_text: str
    suggested_visual_description: str


class GeneratedOfferRecord(BaseModel):
    model_config = ConfigDict(extra="ignore")

    offer_id: str
    vertical: str
    opportunity_name: str
    offer: OfferDetails
    whatsapp_copy: str
    outreach_sequence: list[OutreachStep] = Field(default_factory=list)
    landing_html: str | None = None
    landing_path: str | None = None
    ad_creatives: list[AdCreative] = Field(default_factory=list)
    generation_model: str
    stripe_payment_url: str
    created_at: str


def _normalized(value: str) -> str:
    return (
        str(value or "")
        .strip()
        .lower()
        .translate(str.maketrans({"á": "a", "é": "e", "í": "i", "ó": "o", "ú": "u", "ñ": "n"}))
    )


def _vertical_hints(vertical: str) -> tuple[str, ...]:
    return _VERTICAL_HINTS.get(_normalized(vertical), (_normalized(vertical),))


def _offer_id(vertical: str, opportunity_name: str, offer_name: str) -> str:
    raw = "|".join([_normalized(vertical), _normalized(opportunity_name), _normalized(offer_name)])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _default_stripe_payment_url() -> str:
    return str(
        os.getenv("STRIPE_PAYMENT_LINK")
        or os.getenv("STRIPE_PAYMENT_URL")
        or "https://buy.stripe.com/test_placeholder"
    ).strip()


def _landings_dir() -> Path:
    configured = str(os.getenv("OFFER_LANDINGS_DIR") or "").strip()
    path = Path(configured) if configured else (Path(__file__).resolve().parents[1] / "data" / "landings")
    path.mkdir(parents=True, exist_ok=True)
    return path


def _forge_db_path() -> Path:
    configured = str(os.getenv("FORGE_DB_PATH") or "").strip()
    path = Path(configured) if configured else (Path(__file__).resolve().parents[1] / "data" / "offer_forge.sqlite3")
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _connect_forge_db() -> sqlite3.Connection:
    conn = sqlite3.connect(_forge_db_path())
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS generated_offers (
            offer_id TEXT PRIMARY KEY,
            vertical TEXT NOT NULL,
            opportunity_name TEXT NOT NULL,
            offer_json TEXT NOT NULL,
            whatsapp_copy TEXT NOT NULL,
            outreach_sequence_json TEXT NOT NULL,
            landing_html TEXT,
            landing_path TEXT,
            ad_creatives_json TEXT NOT NULL,
            generation_model TEXT NOT NULL,
            stripe_payment_url TEXT NOT NULL,
            created_at TIMESTAMP NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_generated_offers_created_at
        ON generated_offers(created_at DESC)
        """
    )
    conn.commit()
    return conn


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _theme_for_vertical(vertical: str) -> dict[str, str]:
    normalized = _normalized(vertical)
    themes = {
        "clinics": {
            "bg": "#f4f8ff",
            "surface": "#ffffff",
            "primary": "#1d4ed8",
            "accent": "#93c5fd",
            "text": "#0f172a",
            "muted": "#475569",
        },
        "restaurants": {
            "bg": "#fff7ed",
            "surface": "#fffaf0",
            "primary": "#ea580c",
            "accent": "#fbbf24",
            "text": "#431407",
            "muted": "#7c2d12",
        },
        "dentists": {
            "bg": "#f0fdf4",
            "surface": "#ffffff",
            "primary": "#16a34a",
            "accent": "#86efac",
            "text": "#14532d",
            "muted": "#166534",
        },
        "spas": {
            "bg": "#faf5ff",
            "surface": "#fffaf5",
            "primary": "#9333ea",
            "accent": "#e9d5ff",
            "text": "#3b0764",
            "muted": "#6b21a8",
        },
    }
    return themes.get(
        normalized,
        {
            "bg": "#f8fafc",
            "surface": "#ffffff",
            "primary": "#0f766e",
            "accent": "#99f6e4",
            "text": "#0f172a",
            "muted": "#475569",
        },
    )


def _clip(value: str, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _row_to_generated_offer(row: sqlite3.Row) -> GeneratedOfferRecord:
    return GeneratedOfferRecord(
        offer_id=str(row["offer_id"]),
        vertical=str(row["vertical"]),
        opportunity_name=str(row["opportunity_name"]),
        offer=OfferDetails(**json.loads(str(row["offer_json"]))),
        whatsapp_copy=str(row["whatsapp_copy"]),
        outreach_sequence=[OutreachStep(**item) for item in json.loads(str(row["outreach_sequence_json"]))],
        landing_html=str(row["landing_html"]) if row["landing_html"] else None,
        landing_path=str(row["landing_path"]) if row["landing_path"] else None,
        ad_creatives=[AdCreative(**item) for item in json.loads(str(row["ad_creatives_json"]))],
        generation_model=str(row["generation_model"]),
        stripe_payment_url=str(row["stripe_payment_url"]),
        created_at=str(row["created_at"]),
    )


def save_generated_offer(
    offer: CompleteOffer,
    *,
    landing_html: str | None = None,
    ad_creatives: list[AdCreative] | None = None,
) -> GeneratedOfferRecord:
    created_at = _now_iso()
    with _connect_forge_db() as conn:
        conn.execute(
            """
            INSERT INTO generated_offers (
                offer_id, vertical, opportunity_name, offer_json, whatsapp_copy,
                outreach_sequence_json, landing_html, landing_path, ad_creatives_json,
                generation_model, stripe_payment_url, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(offer_id) DO UPDATE SET
                offer_json = excluded.offer_json,
                whatsapp_copy = excluded.whatsapp_copy,
                outreach_sequence_json = excluded.outreach_sequence_json,
                landing_html = COALESCE(excluded.landing_html, generated_offers.landing_html),
                landing_path = COALESCE(excluded.landing_path, generated_offers.landing_path),
                ad_creatives_json = excluded.ad_creatives_json,
                generation_model = excluded.generation_model,
                stripe_payment_url = excluded.stripe_payment_url
            """,
            (
                offer.offer_id,
                offer.vertical,
                offer.opportunity_name,
                json.dumps(offer.offer.model_dump(mode="json"), ensure_ascii=False),
                offer.whatsapp_copy,
                json.dumps([step.model_dump(mode="json") for step in offer.outreach_sequence], ensure_ascii=False),
                landing_html,
                offer.landing_path,
                json.dumps([item.model_dump(mode="json") for item in (ad_creatives or [])], ensure_ascii=False),
                offer.generation_model,
                offer.stripe_payment_url,
                created_at,
            ),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM generated_offers WHERE offer_id = ?",
            (offer.offer_id,),
        ).fetchone()
    if row is None:
        raise RuntimeError(f"failed to persist generated offer {offer.offer_id}")
    return _row_to_generated_offer(row)


def list_generated_offers(limit: int = 100) -> list[GeneratedOfferRecord]:
    safe_limit = max(1, min(int(limit or 100), 500))
    with _connect_forge_db() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM generated_offers
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (safe_limit,),
        ).fetchall()
    return [_row_to_generated_offer(row) for row in rows]


class OfferForge:
    def __init__(self) -> None:
        self.model = str(os.getenv("OFFER_FORGE_MODEL") or "claude-sonnet-4-6").strip()

    def _load_patterns_for_vertical(self, vertical: str, *, limit: int = 6) -> list[CreativePattern]:
        candidates = get_top_patterns(domain="campaign_execution", limit=30)
        hints = _vertical_hints(vertical)
        matches: list[CreativePattern] = []
        fallbacks: list[CreativePattern] = []
        for pattern in candidates:
            haystack = _normalized(
                " ".join([pattern.offer, pattern.hook, pattern.angle, pattern.cta, pattern.pattern_used])
            )
            if any(hint in haystack for hint in hints):
                matches.append(pattern)
            else:
                fallbacks.append(pattern)
        selected = matches[:limit]
        if len(selected) < limit:
            selected.extend(fallbacks[: limit - len(selected)])
        return selected

    def _load_fatigued_hooks(self, vertical: str) -> set[str]:
        fatigued: set[str] = set()
        for pattern in get_top_patterns(domain="campaign_execution", limit=120):
            haystack = _normalized(" ".join([pattern.offer, pattern.hook, pattern.angle, pattern.cta, pattern.pattern_used]))
            if not any(hint in haystack for hint in _vertical_hints(vertical)):
                continue
            ctr = pattern.ctr if pattern.ctr is not None else None
            reply_rate = pattern.reply_rate if pattern.reply_rate is not None else None
            close_rate = pattern.close_rate if pattern.close_rate is not None else None
            fatigued_signal = (
                (ctr is not None and ctr <= 0.01)
                or (reply_rate is not None and reply_rate < 0.15)
                or (close_rate is not None and close_rate == 0.0)
                or pattern.critic_score < 70
            )
            if fatigued_signal and pattern.hook:
                fatigued.add(_normalized(pattern.hook))
        return fatigued

    def _fresh_hooks(self, offer: CompleteOffer) -> list[str]:
        base_candidates: list[str] = []
        for pattern in offer.patterns_used:
            hook = str(pattern.get("hook") or "").strip()
            if hook:
                base_candidates.append(hook)
        if not base_candidates:
            base_candidates.append(offer.outreach_sequence[0].message if offer.outreach_sequence else offer.whatsapp_copy)

        fatigued = self._load_fatigued_hooks(offer.vertical)
        fresh = [hook for hook in base_candidates if _normalized(hook) not in fatigued]
        if not fresh:
            fresh = [
                f"{offer.opportunity_name}: dejas dinero en la mesa cada vez que un lead no recibe respuesta inmediata",
                f"{offer.offer.name}: activa seguimiento y conversión sin depender de contestar manualmente",
                f"Lo caro no es implementar {offer.offer.name}, lo caro es seguir perdiendo prospectos",
            ]
        return fresh[:3]

    async def _call_claude(self, prompt: str) -> str:
        from brain.marketing_skills import load_skills

        api_key = str(os.getenv("ANTHROPIC_API_KEY") or "").strip()
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY not configured")
        skill_context = load_skills("copywriting", "page_cro", "ad_creative", "pricing_strategy")
        system_prompt = (
            "Actua como un director de ofertas para una agencia de automatizacion, "
            "chatbots, paginas web y ventas por WhatsApp. Responde unicamente JSON valido. "
            "Debes ser directo, comercial, especifico y orientado a cerrar."
            + skill_context
        )
        async with httpx.AsyncClient(timeout=45.0) as client:
            response = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": self.model,
                    "max_tokens": 1800,
                    "system": system_prompt,
                    "messages": [{"role": "user", "content": prompt}],
                },
            )
            response.raise_for_status()
            payload = response.json()
        blocks = payload.get("content") or []
        texts = [str(block.get("text") or "").strip() for block in blocks if isinstance(block, dict)]
        return "\n".join(part for part in texts if part).strip()

    def _build_prompt(self, opportunity: OpportunityInput, patterns: list[CreativePattern]) -> str:
        pattern_payload = [
            {
                "offer": item.offer,
                "hook": item.hook,
                "angle": item.angle,
                "cta": item.cta,
                "pattern_used": item.pattern_used,
                "critic_score": item.critic_score,
                "close_rate": item.close_rate,
                "booking_rate": item.booking_rate,
                "reply_rate": item.reply_rate,
            }
            for item in patterns
        ]
        return (
            "Genera una oferta completa para prospeccion outbound.\n"
            "Regresa SOLO JSON con esta estructura:\n"
            "{\n"
            '  "offer": {"name": str, "description": str, "features": [str], "price_mxn": int, "guarantee": str, "timeline": str},\n'
            '  "whatsapp_copy": str,\n'
            '  "outreach_sequence": [\n'
            '    {"day": 1, "label": "day_1_hook", "goal": str, "message": str},\n'
            '    {"day": 3, "label": "day_3_follow_up", "goal": str, "message": str},\n'
            '    {"day": 7, "label": "day_7_case_study", "goal": str, "message": str},\n'
            '    {"day": 14, "label": "day_14_urgency", "goal": str, "message": str}\n'
            "  ]\n"
            "}\n\n"
            "Reglas:\n"
            "- Escribe en español.\n"
            "- La oferta debe ser concreta para el negocio detectado.\n"
            "- El precio debe ser realista en MXN para KAN Logic.\n"
            "- La promesa debe vender resultado, no solo features.\n"
            "- La secuencia debe sentirse humana, consultiva y orientada a cita o respuesta.\n"
            "- Usa los mejores patrones creativos como inspiración, no los copies literal.\n\n"
            f"Oportunidad:\n{json.dumps(opportunity.model_dump(mode='json'), ensure_ascii=False, indent=2)}\n\n"
            f"Patrones ganadores disponibles:\n{json.dumps(pattern_payload, ensure_ascii=False, indent=2)}"
        )

    def _extract_json(self, raw: str) -> dict[str, Any]:
        clean = str(raw or "").strip()
        if clean.startswith("```"):
            clean = clean.strip("`")
            if clean.lower().startswith("json"):
                clean = clean[4:].strip()
        try:
            return json.loads(clean)
        except json.JSONDecodeError:
            start = clean.find("{")
            end = clean.rfind("}")
            if start >= 0 and end > start:
                return json.loads(clean[start : end + 1])
            raise

    def _fallback_offer(self, opportunity: OpportunityInput, patterns: list[CreativePattern]) -> CompleteOffer:
        vertical_label = opportunity.vertical.replace("_", " ").title()
        offer_name = f"{vertical_label} Web + Chatbot"
        features = [
            "Landing optimizada para captar leads",
            "Bot en WhatsApp para responder y filtrar",
            "Panel de seguimiento de prospectos",
            "Secuencia inicial de seguimiento comercial",
        ]
        price = 7500 if opportunity.payment_capacity_score >= 0.75 else 4900
        hook = patterns[0].hook if patterns else f"Te ayudo a vender más sin perder prospectos en {opportunity.business_name}"
        angle = patterns[0].angle if patterns else "captación y seguimiento"
        offer_id = _offer_id(opportunity.vertical, opportunity.business_name, offer_name)
        return CompleteOffer(
            offer_id=offer_id,
            vertical=opportunity.vertical,
            opportunity_name=opportunity.business_name,
            offer=OfferDetails(
                name=offer_name,
                description=(
                    f"Implementación enfocada en {vertical_label.lower()} para captar prospectos, "
                    "responder más rápido y moverlos a cita o venta."
                ),
                features=features,
                price_mxn=price,
                guarantee="Si en 21 días no queda implementado el flujo base, extiendo soporte sin costo.",
                timeline="7 a 10 días hábiles",
            ),
            whatsapp_copy=(
                f"Hola, revisé {opportunity.business_name} y veo una oportunidad clara para mejorar {angle}. "
                f"Yo preparé una propuesta de {offer_name} desde ${price:,} MXN para captar y dar seguimiento automático. "
                "¿Te la comparto?"
            ),
            outreach_sequence=[
                OutreachStep(day=1, label="day_1_hook", goal="abrir conversación", message=hook),
                OutreachStep(
                    day=3,
                    label="day_3_follow_up",
                    goal="mostrar relevancia",
                    message=f"Te escribo de nuevo porque en {opportunity.business_name} probablemente hoy ya se están perdiendo leads por falta de seguimiento.",
                ),
                OutreachStep(
                    day=7,
                    label="day_7_case_study",
                    goal="bajar riesgo percibido",
                    message="Te comparto un caso corto: negocios similares mejoran respuesta y citas cuando aterrizan web + WhatsApp automatizado.",
                ),
                OutreachStep(
                    day=14,
                    label="day_14_urgency",
                    goal="forzar decisión",
                    message="Si esto sigue pendiente, el costo real no es la herramienta: son los prospectos que ya no vuelven a escribir.",
                ),
            ],
            patterns_used=[asdict(item) for item in patterns],
            generation_model=f"{self.model}:fallback",
            stripe_payment_url=_default_stripe_payment_url(),
        )

    async def forge_offer(self, opportunity: OpportunityInput) -> CompleteOffer:
        patterns = self._load_patterns_for_vertical(opportunity.vertical)
        prompt = self._build_prompt(opportunity, patterns)
        try:
            raw = await self._call_claude(prompt)
            payload = self._extract_json(raw)
            offer = OfferDetails(**dict(payload.get("offer") or {}))
            sequence = [OutreachStep(**item) for item in list(payload.get("outreach_sequence") or [])]
            if len(sequence) != 4:
                raise ValueError("outreach_sequence must contain 4 steps")
            offer_id = _offer_id(opportunity.vertical, opportunity.business_name, offer.name)
            result = CompleteOffer(
                offer_id=offer_id,
                vertical=opportunity.vertical,
                opportunity_name=opportunity.business_name,
                offer=offer,
                whatsapp_copy=str(payload.get("whatsapp_copy") or "").strip(),
                outreach_sequence=sequence,
                patterns_used=[asdict(item) for item in patterns],
                generation_model=self.model,
                stripe_payment_url=str(payload.get("stripe_payment_url") or _default_stripe_payment_url()).strip(),
            )
            if not result.whatsapp_copy:
                raise ValueError("whatsapp_copy is required")
            return result
        except Exception:
            logger.exception("OfferForge Claude generation failed; using fallback offer")
            return self._fallback_offer(opportunity, patterns)

    def generate_landing_page(self, offer: CompleteOffer) -> str:
        theme = _theme_for_vertical(offer.vertical)
        hook = str((offer.patterns_used[0].get("hook") if offer.patterns_used else "") or "").strip()
        if not hook:
            hook = offer.outreach_sequence[0].message if offer.outreach_sequence else offer.whatsapp_copy
        angle = str((offer.patterns_used[0].get("angle") if offer.patterns_used else "") or "").strip()
        pain_points = [
            offer.outreach_sequence[0].message if offer.outreach_sequence else "Estás perdiendo prospectos por falta de seguimiento.",
            offer.outreach_sequence[1].message if len(offer.outreach_sequence) > 1 else "Responder tarde hace que el lead se enfríe.",
            f"Hoy el cuello de botella principal es {angle or 'captación y conversión'}.",
        ]
        feature_items = "".join(f"<li>{html.escape(item)}</li>" for item in offer.offer.features)
        pain_items = "".join(f"<li>{html.escape(item)}</li>" for item in pain_points)
        landing_html = f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(offer.offer.name)} | KAN Logic</title>
  <style>
    :root {{
      --bg: {theme["bg"]};
      --surface: {theme["surface"]};
      --primary: {theme["primary"]};
      --accent: {theme["accent"]};
      --text: {theme["text"]};
      --muted: {theme["muted"]};
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; font-family: Georgia, "Times New Roman", serif; background: linear-gradient(135deg, var(--bg), var(--surface)); color: var(--text); }}
    .page {{ max-width: 1120px; margin: 0 auto; padding: 48px 20px 64px; }}
    .hero, .grid, .pricing, .proof {{ background: var(--surface); border: 1px solid rgba(15, 23, 42, 0.08); border-radius: 24px; padding: 32px; box-shadow: 0 18px 50px rgba(15, 23, 42, 0.08); }}
    .hero {{ display: grid; gap: 18px; margin-bottom: 24px; }}
    .eyebrow {{ color: var(--primary); font-size: 14px; letter-spacing: 0.12em; text-transform: uppercase; font-weight: 700; }}
    h1, h2 {{ margin: 0; line-height: 1.05; }}
    h1 {{ font-size: clamp(2.4rem, 4vw, 4.8rem); max-width: 10ch; }}
    p {{ line-height: 1.6; }}
    .lede {{ font-size: 1.1rem; max-width: 62ch; color: var(--muted); }}
    .cta-row {{ display: flex; gap: 14px; flex-wrap: wrap; align-items: center; }}
    .cta {{ display: inline-flex; align-items: center; justify-content: center; padding: 16px 28px; border-radius: 999px; text-decoration: none; font-weight: 700; background: var(--primary); color: white; }}
    .cta-secondary {{ color: var(--primary); font-weight: 700; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 24px; margin-bottom: 24px; }}
    .card {{ padding: 4px; }}
    ul {{ padding-left: 20px; margin: 14px 0 0; }}
    li {{ margin-bottom: 10px; }}
    .pricing {{ display: grid; gap: 16px; margin-bottom: 24px; }}
    .price {{ font-size: clamp(2rem, 4vw, 3.8rem); font-weight: 700; color: var(--primary); }}
    .proof {{ display: grid; gap: 14px; }}
    .badge {{ display: inline-block; padding: 8px 12px; border-radius: 999px; background: var(--accent); color: var(--text); font-size: 14px; font-weight: 700; }}
    .footer-note {{ color: var(--muted); font-size: 0.95rem; }}
    @media (max-width: 700px) {{
      .page {{ padding: 24px 16px 40px; }}
      .hero, .grid, .pricing, .proof {{ padding: 24px; border-radius: 20px; }}
    }}
  </style>
</head>
<body>
  <main class="page">
    <section class="hero">
      <span class="eyebrow">Oferta KAN Logic</span>
      <h1>{html.escape(hook)}</h1>
      <p class="lede">{html.escape(offer.offer.description)}</p>
      <div class="cta-row">
        <a class="cta" href="{html.escape(offer.stripe_payment_url)}">Pagar con Stripe</a>
        <span class="cta-secondary">{html.escape(offer.offer.timeline)} · Garantía: {html.escape(offer.offer.guarantee)}</span>
      </div>
    </section>

    <section class="grid">
      <article class="card">
        <h2>Dolores que resuelve</h2>
        <ul>{pain_items}</ul>
      </article>
      <article class="card">
        <h2>La solución</h2>
        <p>{html.escape(offer.whatsapp_copy)}</p>
        <ul>{feature_items}</ul>
      </article>
    </section>

    <section class="proof">
      <span class="badge">Prueba social</span>
      <h2>Placeholder para testimonios, casos y resultados</h2>
      <p>Agrega aquí capturas de chats, métricas de citas cerradas, antes/después y un testimonio del nicho para elevar confianza.</p>
    </section>

    <section class="pricing">
      <h2>{html.escape(offer.offer.name)}</h2>
      <div class="price">${offer.offer.price_mxn:,.0f} MXN</div>
      <p>{html.escape(offer.opportunity_name)} puede implementar este flujo para captar, responder y convertir mejor desde la primera semana.</p>
      <div class="cta-row">
        <a class="cta" href="{html.escape(offer.stripe_payment_url)}">Quiero activar esta oferta</a>
      </div>
      <p class="footer-note">Timeline: {html.escape(offer.offer.timeline)}. Garantía: {html.escape(offer.offer.guarantee)}</p>
    </section>
  </main>
</body>
</html>
"""
        landing_path = _landings_dir() / f"{offer.offer_id}.html"
        landing_path.write_text(landing_html, encoding="utf-8")
        offer.landing_path = str(landing_path)
        return landing_html

    def generate_ad_creatives(self, offer: CompleteOffer) -> list[AdCreative]:
        hooks = self._fresh_hooks(offer)
        social_proof = [
            "Negocios similares ya usan flujos automáticos para responder más rápido y agendar mejor.",
            "Este tipo de implementación suele recuperar leads que antes se quedaban en visto.",
            "Cuando el seguimiento deja de depender del humano, el cierre se vuelve más consistente.",
        ]
        visuals = {
            "clinics": [
                "Doctora frente a laptop revisando citas; interfaz limpia en azul y blanco con mensajes de WhatsApp entrando.",
                "Recepción de clínica con agenda saturada vs panel automatizado resolviendo preguntas en tiempo real.",
                "Paciente escribiendo por WhatsApp y flujo visual mostrando respuesta automática, calificación y cita confirmada.",
            ],
            "restaurants": [
                "Restaurante lleno, celular con reservas entrando y panel naranja/dorado mostrando seguimiento automático.",
                "Mesero atendiendo mientras un bot confirma reservaciones y capta pedidos desde WhatsApp.",
                "Comparativa antes/después: mensajes sin respuesta vs reservas cerradas con automatización.",
            ],
            "dentists": [
                "Consultorio dental limpio en verde y blanco; nuevos pacientes entrando por landing y WhatsApp.",
                "Dentista atendiendo consulta mientras un sistema agenda valoraciones automáticamente.",
                "Pantalla con chat de valoración dental y botón claro para agendar cita.",
            ],
            "spas": [
                "Ambiente de spa premium en morado/cream con mensajes de cita y seguimiento elegante.",
                "Especialista de spa enfocada en cliente mientras el sistema responde preguntas frecuentes por WhatsApp.",
                "Visual aspiracional antes/después con agenda automatizada y promociones premium.",
            ],
        }
        vertical_visuals = visuals.get(_normalized(offer.vertical), [
            "Dueño de negocio revisando mensajes y un dashboard que convierte prospectos en citas.",
            "Celular con leads entrando por WhatsApp y seguimiento automático activo.",
            "Comparativa entre caos comercial y proceso automatizado de ventas.",
        ])

        pain_variants = [
            "Te escriben y no siempre reciben respuesta a tiempo.",
            "El seguimiento depende demasiado del humano y eso enfría la venta.",
            "Cada lead perdido termina costando más que implementar una solución.",
        ]
        cta_variants = [
            "Quiero activarlo",
            "Quiero ver cómo funciona",
            "Quiero mi propuesta",
        ]

        creatives: list[AdCreative] = []
        for idx in range(3):
            hook = hooks[idx % len(hooks)]
            body = (
                f"{hook} "
                f"{pain_variants[idx]} "
                f"{offer.offer.name} aterriza la solución con {offer.offer.features[0].lower()} y {offer.offer.features[1].lower()}. "
                f"{social_proof[idx]} "
                f"Activa hoy desde ${offer.offer.price_mxn:,.0f} MXN y deja de perder prospectos."
            )
            creatives.append(
                AdCreative(
                    headline=_clip(hook, 80),
                    body=body,
                    cta_button_text=cta_variants[idx],
                    suggested_visual_description=vertical_visuals[idx % len(vertical_visuals)],
                )
            )
        return creatives
