from __future__ import annotations

import json
import os
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite
from pydantic import BaseModel, Field
from sqlalchemy import select

try:
    from models import CrmInteraction
except Exception:  # pragma: no cover - startup fallback if models drift
    CrmInteraction = None  # type: ignore[assignment]

try:
    from brain.opportunity_pipeline import get_pipeline_by_status
except Exception:  # pragma: no cover - keep boot resilient
    get_pipeline_by_status = None  # type: ignore[assignment]

try:
    from tools.ycloud_client import send_ycloud_whatsapp_text_message
except Exception:  # pragma: no cover - keep boot resilient
    async def send_ycloud_whatsapp_text_message(*_: Any, **__: Any) -> dict[str, Any]:
        # TODO: replace stub if WhatsApp sender import path changes.
        return {"sent": False, "reason": "ycloud_sender_unavailable"}


class ProductRevenueProjection(BaseModel):
    months_3: int
    months_6: int
    months_12: int


class ProductProposal(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    unmet_need_key: str
    service_name: str
    suggested_price_mxn: int
    delivery_cost_mxn: int
    margin_pct: float
    revenue_projection: ProductRevenueProjection
    required_stack: list[str]
    go_to_market: str
    raw_signals_count: int
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


_PROPOSAL_SPECS: dict[str, dict[str, Any]] = {
    "social_media_management": {
        "keywords": [
            "community manager",
            "manejo de redes",
            "redes sociales",
            "instagram manager",
            "social media",
        ],
        "service_name": "Manejo de Redes Sociales",
        "suggested_price_mxn": 3000,
        "delivery_cost_mxn": 1200,
        "required_stack": ["content_ops", "meta_ads_operator", "whatsapp_closer"],
        "go_to_market": (
            "Activar oferta de entrada para negocios locales sin equipo de marketing; "
            "combinar outreach por WhatsApp con caso corto y upsell a anuncios o chatbot."
        ),
    },
    "website_development": {
        "keywords": [
            "pagina web",
            "página web",
            "sitio web",
            "landing page",
            "web developer",
            "website",
        ],
        "service_name": "Landing + Sitio Web Comercial",
        "suggested_price_mxn": 6000,
        "delivery_cost_mxn": 2200,
        "required_stack": ["web_builder", "analytics", "whatsapp_closer"],
        "go_to_market": (
            "Ofrecer sitio rápido con CTA a WhatsApp para leads que hoy dependen solo de Instagram o Maps."
        ),
    },
    "whatsapp_automation": {
        "keywords": [
            "automatizacion whatsapp",
            "automatización whatsapp",
            "whatsapp bot",
            "chatbot",
            "seguimiento whatsapp",
            "responder mensajes",
        ],
        "service_name": "Chatbot de WhatsApp con Seguimiento",
        "suggested_price_mxn": 3500,
        "delivery_cost_mxn": 1400,
        "required_stack": ["ycloud", "crm", "agenda"],
        "go_to_market": (
            "Cerrar como oferta de entrada para negocios con fuga de leads en conversaciones y escalar a plan mensual."
        ),
    },
    "seo_content": {
        "keywords": [
            "seo",
            "blog",
            "posicionamiento",
            "contenido organico",
            "contenido orgánico",
        ],
        "service_name": "SEO + Blog Mensual",
        "suggested_price_mxn": 2500,
        "delivery_cost_mxn": 900,
        "required_stack": ["content_ops", "seo", "web_builder"],
        "go_to_market": (
            "Vender a clientes con sitio existente y usarlo como upsell mensual para incrementar retención."
        ),
    },
    "email_marketing": {
        "keywords": [
            "email marketing",
            "newsletter",
            "correo masivo",
            "mailing",
        ],
        "service_name": "Email Marketing Automatizado",
        "suggested_price_mxn": 1800,
        "delivery_cost_mxn": 700,
        "required_stack": ["email_ops", "crm", "copy_system"],
        "go_to_market": (
            "Activar sobre base de clientes o leads existentes con oferta simple de recuperación y seguimiento."
        ),
    },
}


def _db_path() -> str:
    explicit = os.getenv("PRODUCT_DISCOVERY_DB_PATH")
    if explicit:
        return explicit
    inherited = os.getenv("ATTRIBUTION_ENGINE_DB_PATH")
    if inherited:
        return inherited
    data_dir = Path("data")
    data_dir.mkdir(parents=True, exist_ok=True)
    return str(data_dir / "product_discovery.sqlite3")


def _normalize_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        return " ".join(_normalize_text(v) for v in value.values())
    if isinstance(value, list):
        return " ".join(_normalize_text(item) for item in value)
    return str(value).strip().lower()


def _extract_signal_texts_from_snapshot(snapshot: dict[str, Any] | None) -> list[str]:
    if not snapshot:
        return []
    texts: list[str] = []
    for key in ("crm_signals", "conversation_signals", "opportunity_signals", "lost_deals", "conversations"):
        value = snapshot.get(key)
        if not value:
            continue
        if isinstance(value, list):
            for item in value:
                normalized = _normalize_text(item)
                if normalized:
                    texts.append(normalized)
        else:
            normalized = _normalize_text(value)
            if normalized:
                texts.append(normalized)
    return texts


def _extract_need_counts(texts: list[str]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for text in texts:
        for need_key, spec in _PROPOSAL_SPECS.items():
            if any(keyword in text for keyword in spec["keywords"]):
                counts[need_key] += 1
    return counts


def _projection_from_signals(price: int, signal_count: int) -> ProductRevenueProjection:
    monthly_close_estimate = max(1, signal_count // 10)
    return ProductRevenueProjection(
        months_3=price * monthly_close_estimate * 3,
        months_6=price * monthly_close_estimate * 6,
        months_12=price * monthly_close_estimate * 12,
    )


def _build_proposal(need_key: str, signal_count: int) -> ProductProposal:
    spec = _PROPOSAL_SPECS[need_key]
    price = int(spec["suggested_price_mxn"])
    cost = int(spec["delivery_cost_mxn"])
    margin_pct = round(((price - cost) / price) * 100, 2) if price else 0.0
    return ProductProposal(
        unmet_need_key=need_key,
        service_name=str(spec["service_name"]),
        suggested_price_mxn=price,
        delivery_cost_mxn=cost,
        margin_pct=margin_pct,
        revenue_projection=_projection_from_signals(price, signal_count),
        required_stack=list(spec["required_stack"]),
        go_to_market=str(spec["go_to_market"]),
        raw_signals_count=signal_count,
    )


def _proposal_whatsapp_message(proposal: ProductProposal) -> str:
    return (
        f"Detecte una oportunidad nueva: {proposal.service_name}\n"
        f"Senales: {proposal.raw_signals_count}\n"
        f"Precio sugerido: ${proposal.suggested_price_mxn:,} MXN\n"
        f"Costo estimado: ${proposal.delivery_cost_mxn:,} MXN\n"
        f"Margen: {proposal.margin_pct}%\n"
        f"Proyeccion 3/6/12 meses: "
        f"${proposal.revenue_projection.months_3:,} / "
        f"${proposal.revenue_projection.months_6:,} / "
        f"${proposal.revenue_projection.months_12:,} MXN\n"
        f"Stack: {', '.join(proposal.required_stack)}\n"
        f"GTM: {proposal.go_to_market}\n\n"
        "Responde: APPROVE / REJECT / MODIFY"
    )


class ProductDiscovery:
    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = db_path or _db_path()

    async def initialize(self) -> None:
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS product_proposals (
                    id TEXT PRIMARY KEY,
                    unmet_need_key TEXT UNIQUE,
                    service_name TEXT NOT NULL,
                    suggested_price_mxn INTEGER NOT NULL,
                    delivery_cost_mxn INTEGER NOT NULL,
                    margin_pct REAL NOT NULL,
                    revenue_projection_json TEXT NOT NULL,
                    required_stack_json TEXT NOT NULL,
                    go_to_market TEXT NOT NULL,
                    raw_signals_count INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            await conn.commit()

    async def _load_signals_from_session(self, session: Any | None) -> list[str]:
        if session is None:
            return []

        texts: list[str] = []
        if CrmInteraction is not None:
            try:
                result = await session.execute(
                    select(CrmInteraction.content, CrmInteraction.interaction_metadata)
                )
                for content, interaction_metadata in result.all():
                    normalized = _normalize_text(content)
                    if normalized:
                        texts.append(normalized)
                    meta_text = _normalize_text(interaction_metadata)
                    if meta_text:
                        texts.append(meta_text)
            except Exception:
                # TODO: narrow this when CRM schema stabilizes across deployments.
                pass

        if get_pipeline_by_status is not None:
            try:
                pipeline = await get_pipeline_by_status()
                for status in ("DETECTED", "EVALUATED"):
                    for item in pipeline.get(status, []):
                        text = " ".join(
                            filter(
                                None,
                                [
                                    _normalize_text(item.get("business_name")),
                                    _normalize_text(item.get("vertical")),
                                    _normalize_text(item.get("offer_summary")),
                                    _normalize_text(item.get("business_need")),
                                ],
                            )
                        )
                        if text:
                            texts.append(text)
            except Exception:
                # TODO: replace with a typed pipeline adapter once the pipeline contract is stable.
                pass

        return texts

    async def _persist_proposal(self, proposal: ProductProposal) -> None:
        async with aiosqlite.connect(self.db_path) as conn:
            cursor = await conn.execute(
                "SELECT id, created_at FROM product_proposals WHERE unmet_need_key = ?",
                (proposal.unmet_need_key,),
            )
            existing_row = await cursor.fetchone()
            proposal_id = proposal.id
            created_at = proposal.created_at.isoformat()
            if existing_row:
                proposal_id = existing_row[0]
                created_at = existing_row[1]
            await conn.execute(
                """
                INSERT INTO product_proposals (
                    id,
                    unmet_need_key,
                    service_name,
                    suggested_price_mxn,
                    delivery_cost_mxn,
                    margin_pct,
                    revenue_projection_json,
                    required_stack_json,
                    go_to_market,
                    raw_signals_count,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(unmet_need_key) DO UPDATE SET
                    service_name = excluded.service_name,
                    suggested_price_mxn = excluded.suggested_price_mxn,
                    delivery_cost_mxn = excluded.delivery_cost_mxn,
                    margin_pct = excluded.margin_pct,
                    revenue_projection_json = excluded.revenue_projection_json,
                    required_stack_json = excluded.required_stack_json,
                    go_to_market = excluded.go_to_market,
                    raw_signals_count = excluded.raw_signals_count
                """,
                (
                    proposal_id,
                    proposal.unmet_need_key,
                    proposal.service_name,
                    proposal.suggested_price_mxn,
                    proposal.delivery_cost_mxn,
                    proposal.margin_pct,
                    json.dumps(proposal.revenue_projection.model_dump()),
                    json.dumps(proposal.required_stack),
                    proposal.go_to_market,
                    proposal.raw_signals_count,
                    created_at,
                ),
            )
            await conn.commit()

    async def list_proposals(self, limit: int = 20) -> list[ProductProposal]:
        await self.initialize()
        async with aiosqlite.connect(self.db_path) as conn:
            cursor = await conn.execute(
                """
                SELECT
                    id,
                    unmet_need_key,
                    service_name,
                    suggested_price_mxn,
                    delivery_cost_mxn,
                    margin_pct,
                    revenue_projection_json,
                    required_stack_json,
                    go_to_market,
                    raw_signals_count,
                    created_at
                FROM product_proposals
                ORDER BY raw_signals_count DESC, created_at DESC
                LIMIT ?
                """,
                (limit,),
            )
            rows = await cursor.fetchall()
        proposals: list[ProductProposal] = []
        for row in rows:
            proposals.append(
                ProductProposal(
                    id=row[0],
                    unmet_need_key=row[1],
                    service_name=row[2],
                    suggested_price_mxn=row[3],
                    delivery_cost_mxn=row[4],
                    margin_pct=row[5],
                    revenue_projection=ProductRevenueProjection(**json.loads(row[6])),
                    required_stack=list(json.loads(row[7])),
                    go_to_market=row[8],
                    raw_signals_count=row[9],
                    created_at=datetime.fromisoformat(row[10]),
                )
            )
        return proposals

    async def _notify_raul(self, proposal: ProductProposal) -> None:
        to_number = str(os.getenv("RAUL_WHATSAPP_TO") or "").strip()
        from_number = str(os.getenv("YCLOUD_WHATSAPP_FROM") or "").strip()
        if not to_number or not from_number:
            return
        await send_ycloud_whatsapp_text_message(
            from_number=from_number,
            to=to_number,
            text=_proposal_whatsapp_message(proposal),
        )

    async def run(
        self,
        *,
        snapshot: dict[str, Any] | None = None,
        session: Any | None = None,
    ) -> list[ProductProposal]:
        await self.initialize()
        texts = _extract_signal_texts_from_snapshot(snapshot)
        texts.extend(await self._load_signals_from_session(session))
        counts = _extract_need_counts(texts)

        proposals: list[ProductProposal] = []
        for unmet_need_key, signal_count in counts.items():
            if signal_count < 10:
                continue
            proposal = _build_proposal(unmet_need_key, signal_count)
            await self._persist_proposal(proposal)
            await self._notify_raul(proposal)
            proposals.append(proposal)
        return proposals
