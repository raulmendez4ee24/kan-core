from __future__ import annotations

import hashlib
import logging
import os
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

import aiosqlite
from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger("kan_core.closer")

_PRICE_THRESHOLD = 15_000.0  # MXN — below this, send payment link; above, schedule demo

# ---------------------------------------------------------------------------
# Generic fallback responses keyed by common objection types.
# Used when creative_memory has no trained response for the given context.
# ---------------------------------------------------------------------------
_OBJECTION_FALLBACKS: dict[str, str] = {
    "price": (
        "Entiendo que el precio importa. Muchos clientes decían lo mismo antes de ver el ROI real. "
        "¿Qué resultados necesitarías ver para que tenga sentido?"
    ),
    "time": (
        "El tiempo es escaso para todos. Por eso automatizamos el 80% del proceso desde el día 1. "
        "¿Cuánto tiempo inviertes hoy en esto?"
    ),
    "trust": (
        "Es totalmente válido querer más información antes de decidir. "
        "¿Qué necesitarías ver para sentirte seguro con esto?"
    ),
    "competition": (
        "Tiene sentido evaluar opciones. "
        "¿Qué criterio te importa más al comparar?"
    ),
}
_OBJECTION_FALLBACK_DEFAULT = (
    "Entiendo tu preocupación. Cuéntame más para darte una respuesta concreta y orientada a tu situación."
)


class CloserStrategy(str, Enum):
    DIRECT_CLOSE = "direct_close"
    DEMOS_SOCIAL_PROOF = "demos_social_proof"
    VALUE_NO_PRESSURE = "value_no_pressure"
    NURTURE_ONLY = "nurture_only"


class PipelineLead(BaseModel):
    model_config = ConfigDict(extra="ignore")

    lead_id: str
    name: str = "Lead"
    source: str = "unknown"
    engagement_score: float = 0.0
    budget_score: float = 0.0
    urgency_score: float = 0.0
    last_contact_at: datetime | None = None
    vertical: str = "general"
    mentioned_budget: float | None = None
    objection: str | None = None
    ready_for_booking: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)
    # Payment / demo fields
    product_price: float | None = None   # price of the offer in MXN
    product_name: str = "nuestro servicio"
    phone: str | None = None             # WhatsApp destination for payment link


class ObjectionResponse(BaseModel):
    """Returned by ObjectionHandler.get_best_response()."""

    response_id: str | None  # None when using a built-in fallback
    response_text: str
    objection_type: str
    vertical: str
    conversion_rate: float
    is_fallback: bool


class CloserAction(BaseModel):
    lead_id: str
    action_type: str
    priority: int
    confidence: float
    reason: str
    message: str
    channel: str = "whatsapp"
    escalate_to_human: bool = False
    strategy: CloserStrategy = CloserStrategy.NURTURE_ONLY
    objection_response_id: str | None = None  # set when handle_objection fires
    payment_link: str | None = None          # set when action_type == "send_payment_link"
    calendar_event_id: str | None = None     # set when action_type == "schedule_demo"
    demo_scheduled_at: str | None = None     # ISO timestamp of the scheduled demo


_SendWhatsAppFn = Callable[[str, str], Awaitable[bool]]
_ScheduleDemoFn = Callable[["PipelineLead"], Awaitable[tuple[Optional[str], Optional[str]]]]


class CloserOperator:
    def __init__(
        self,
        *,
        strategy_selector: StrategySelector | None = None,
        objection_handler: ObjectionHandler | None = None,
        payment_connector: Any | None = None,
        send_whatsapp: _SendWhatsAppFn | None = None,
        schedule_demo: _ScheduleDemoFn | None = None,
    ) -> None:
        self._strategy_selector = strategy_selector or StrategySelector()
        self._objection_handler = objection_handler or ObjectionHandler()
        self._payment_connector = payment_connector  # lazily resolved when None
        self._send_whatsapp: _SendWhatsAppFn = send_whatsapp or _default_send_whatsapp
        self._schedule_demo: _ScheduleDemoFn = schedule_demo or _default_schedule_demo

    def _get_payment_connector(self) -> Any:
        if self._payment_connector is None:
            from brain.payment_connector import get_payment_connector
            self._payment_connector = get_payment_connector()
        return self._payment_connector

    def _lead_score(self, lead: PipelineLead) -> float:
        recency_bonus = 0.0
        if lead.last_contact_at:
            age_hours = max(0.0, (datetime.now(timezone.utc) - lead.last_contact_at).total_seconds() / 3600.0)
            recency_bonus = max(0.0, 20.0 - min(age_hours, 20.0))
        return (
            float(lead.engagement_score) * 0.40
            + float(lead.budget_score) * 0.20
            + float(lead.urgency_score) * 0.20
            + recency_bonus
        )

    async def prioritize_pipeline(self, leads: list[PipelineLead]) -> list[PipelineLead]:
        return sorted(leads, key=self._lead_score, reverse=True)

    async def _build_close_action(
        self,
        lead: PipelineLead,
        strategy: CloserStrategy,
        score: float,
    ) -> CloserAction:
        """Build a close action for high-score leads: payment link or demo scheduling."""
        price = lead.product_price  # guaranteed not None by caller
        assert price is not None

        if price < _PRICE_THRESHOLD:
            # --- Auto-close: generate Stripe payment link + WhatsApp ---
            description = f"Propuesta {lead.product_name} para {lead.name}"
            try:
                link = await self._get_payment_connector().create_payment_link(
                    lead.lead_id, lead.product_name, price, description
                )
            except Exception as exc:
                logger.error("closer: payment link creation failed for %s: %s", lead.lead_id, exc)
                link = None

            message = _build_payment_link_message(lead, link)
            try:
                from brain.humanizer import humanize_text

                message = humanize_text(message, channel="whatsapp")
            except Exception as exc:
                logger.debug("closer: humanizer skipped for payment link: %s", exc)

            if lead.phone and link:
                try:
                    await self._send_whatsapp(lead.phone, message)
                except Exception as exc:
                    logger.warning("closer: WhatsApp send failed for %s: %s", lead.lead_id, exc)

            return CloserAction(
                lead_id=lead.lead_id,
                action_type="send_payment_link",
                priority=98,
                confidence=0.92,
                reason=f"Score alto ({score:.0f}) y precio ${price:,.0f} MXN — link de pago enviado.",
                message=message,
                channel="whatsapp",
                strategy=strategy,
                payment_link=link,
            )

        else:
            # --- High-ticket: schedule demo with Raul via Google Calendar ---
            try:
                event_id, scheduled_at = await self._schedule_demo(lead)
            except Exception as exc:
                logger.error("closer: demo scheduling failed for %s: %s", lead.lead_id, exc)
                event_id, scheduled_at = None, None

            message = _build_demo_message(lead, scheduled_at)
            try:
                from brain.humanizer import humanize_text

                message = humanize_text(message, channel="whatsapp")
            except Exception as exc:
                logger.debug("closer: humanizer skipped for demo: %s", exc)

            return CloserAction(
                lead_id=lead.lead_id,
                action_type="schedule_demo",
                priority=97,
                confidence=0.90,
                reason=f"Score alto ({score:.0f}) y precio ${price:,.0f} MXN — requiere demo.",
                message=message,
                channel="whatsapp",
                strategy=strategy,
                calendar_event_id=event_id,
                demo_scheduled_at=scheduled_at,
            )

    async def build_followup_action(self, lead: PipelineLead) -> CloserAction:
        strategy = await self._strategy_selector.select_strategy(lead.lead_id)
        now = datetime.now(timezone.utc)
        last_contact = lead.last_contact_at or (now - timedelta(days=30))
        age_days = max(0.0, (now - last_contact).total_seconds() / 86400.0)
        score = self._lead_score(lead)

        # High-score + known price → payment link (< 15k) or demo (>= 15k)
        if score > 80 and lead.product_price is not None:
            return await self._build_close_action(lead, strategy, score)

        if lead.ready_for_booking or score >= 70:
            return CloserAction(
                lead_id=lead.lead_id,
                action_type="push_booking",
                priority=95,
                confidence=0.84,
                reason="Señal de compra alta y oportunidad de mover a llamada.",
                message="Yo ya vi suficiente interés. Voy a proponer una reunión corta para aterrizar cómo funcionaría en su negocio.",
                strategy=strategy,
            )
        if age_days >= 7:
            return CloserAction(
                lead_id=lead.lead_id,
                action_type="reactivate_cold_lead",
                priority=60,
                confidence=0.68,
                reason="Lead frío con ventana de reactivación.",
                message="Yo voy a reactivar con una oferta o caso breve para recuperar la conversación.",
                strategy=strategy,
            )
        if lead.objection:
            obj_resp = await self._objection_handler.get_best_response(
                lead.objection, lead.vertical, lead_id=lead.lead_id
            )
            return CloserAction(
                lead_id=lead.lead_id,
                action_type="handle_objection",
                priority=80,
                confidence=0.75,
                reason=f"Objeción detectada: {lead.objection}",
                message=obj_resp.response_text,
                strategy=strategy,
                objection_response_id=obj_resp.response_id,
            )
        return CloserAction(
            lead_id=lead.lead_id,
            action_type="advance_followup",
            priority=70,
            confidence=0.72,
            reason="Lead activo, conviene empujar siguiente paso.",
            message="Yo voy a dar seguimiento con un mensaje corto para mover la conversación.",
            strategy=strategy,
        )

    async def daily_close_plan(self, leads: list[PipelineLead]) -> list[CloserAction]:
        ordered = await self.prioritize_pipeline(leads)
        return [await self.build_followup_action(item) for item in ordered[:10]]


# ---------------------------------------------------------------------------
# Message builders
# ---------------------------------------------------------------------------


def _build_payment_link_message(lead: PipelineLead, link: str | None) -> str:
    price_fmt = f"${lead.product_price:,.0f} MXN" if lead.product_price is not None else ""
    link_part = f"\n\n🔗 Link de pago: {link}" if link else ""
    return (
        f"Hola {lead.name}, tengo buenas noticias. "
        f"Ya tenemos todo listo para arrancar con {lead.product_name}"
        f"{' a ' + price_fmt if price_fmt else ''}. "
        f"Aquí está el link para completar el pago de forma segura:{link_part}"
    )


def _build_demo_message(lead: PipelineLead, scheduled_at: str | None) -> str:
    time_part = f" para el {scheduled_at}" if scheduled_at else ""
    return (
        f"Hola {lead.name}, dada la magnitud de tu proyecto con {lead.product_name} "
        f"quiero asegurarme de que tengas una sesión directa con nuestro equipo. "
        f"Te agendé una demo personalizada{time_part}. "
        f"Te comparto los detalles en breve para confirmación."
    )


# ---------------------------------------------------------------------------
# Default injectable implementations
# ---------------------------------------------------------------------------


async def _default_send_whatsapp(to: str, message: str) -> bool:
    try:
        from tools.ycloud_client import send_ycloud_whatsapp_text_message  # type: ignore

        await send_ycloud_whatsapp_text_message(to, message)
        return True
    except Exception as exc:
        logger.warning("closer: default WhatsApp send failed: %s", exc)
        return False


async def _default_schedule_demo(
    lead: PipelineLead,
) -> tuple[Optional[str], Optional[str]]:
    calendar_id = (
        os.getenv("RAUL_CALENDAR_ID", "").strip()
        or os.getenv("GOOGLE_CALENDAR_ID", "").strip()
    )
    if not calendar_id:
        logger.warning("closer: RAUL_CALENDAR_ID / GOOGLE_CALENDAR_ID not set — demo not scheduled")
        return None, None

    now = datetime.now(timezone.utc)
    start = (now + timedelta(days=1, hours=2)).replace(minute=0, second=0, microsecond=0)
    end = start + timedelta(hours=1)

    try:
        from brain.booking_connector import _GoogleClient  # type: ignore

        gc = _GoogleClient(calendar_id=calendar_id)
        event = await gc.create_event(
            summary=f"Demo {lead.product_name} — {lead.name}",
            description=(
                f"Demo agendado automáticamente por CloserOperator.\n"
                f"Producto: {lead.product_name}\n"
                f"Presupuesto: ${lead.product_price:,.0f} MXN" if lead.product_price else ""
            ),
            start_at=start,
            end_at=end,
            timezone_name="America/Mexico_City",
            contact_name=lead.name,
            contact_email=lead.metadata.get("email"),
        )
        event_id = event.get("id")
        return event_id, start.isoformat()
    except Exception as exc:
        logger.warning("closer: demo scheduling via Google Calendar failed: %s", exc)
        return None, None


class LeadScorer:
    """Persist and update behavioural scores for leads using event deltas."""

    SCORE_DELTAS: dict[str, int] = {
        "budget_mentioned": 20,
        "asked_price": 15,
        "said_thinking": -10,
        "replied_fast": 5,
        "opened_link": 10,
    }

    @staticmethod
    def _db_path() -> Path:
        override = os.getenv("LEAD_SCORER_DB_PATH")
        path = Path(override).expanduser() if override else Path("data/lead_scores.sqlite3")
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    @classmethod
    async def _connect(cls) -> aiosqlite.Connection:
        db = await aiosqlite.connect(cls._db_path())
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA journal_mode = WAL;")
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS lead_scores (
                lead_id TEXT PRIMARY KEY,
                score   INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        await db.commit()
        return db

    async def update_score(self, lead_id: str, event_type: str) -> int:
        """Apply the delta for *event_type* to *lead_id*'s score and return the new value.

        Unknown event types are silently ignored (delta = 0).
        """
        delta = self.SCORE_DELTAS.get(event_type, 0)
        db = await self._connect()
        try:
            await db.execute(
                """
                INSERT INTO lead_scores (lead_id, score) VALUES (?, ?)
                ON CONFLICT(lead_id) DO UPDATE SET score = score + excluded.score
                """,
                (lead_id, delta),
            )
            await db.commit()
            cursor = await db.execute(
                "SELECT score FROM lead_scores WHERE lead_id = ?",
                (lead_id,),
            )
            row = await cursor.fetchone()
        finally:
            await db.close()
        return int(row["score"]) if row else 0

    async def get_score(self, lead_id: str) -> int:
        """Return the current score for *lead_id*, or 0 if no events have been recorded."""
        db = await self._connect()
        try:
            cursor = await db.execute(
                "SELECT score FROM lead_scores WHERE lead_id = ?",
                (lead_id,),
            )
            row = await cursor.fetchone()
        finally:
            await db.close()
        return int(row["score"]) if row else 0


class StrategySelector:
    """Map a lead's behavioural score to the appropriate closing strategy."""

    def __init__(self, *, scorer: LeadScorer | None = None) -> None:
        self._scorer = scorer or LeadScorer()

    async def select_strategy(self, lead_id: str) -> CloserStrategy:
        """Return the strategy tier based on the lead's current score.

        > 80  → DIRECT_CLOSE
        50–80 → DEMOS_SOCIAL_PROOF
        30–49 → VALUE_NO_PRESSURE
        < 30  → NURTURE_ONLY
        """
        score = await self._scorer.get_score(lead_id)
        if score > 80:
            return CloserStrategy.DIRECT_CLOSE
        if score >= 50:
            return CloserStrategy.DEMOS_SOCIAL_PROOF
        if score >= 30:
            return CloserStrategy.VALUE_NO_PRESSURE
        return CloserStrategy.NURTURE_ONLY


def _response_id(objection_type: str, vertical: str, response_text: str) -> str:
    raw = "|".join([objection_type.strip().lower(), vertical.strip().lower(), response_text.strip()])
    return hashlib.sha256(raw.encode()).hexdigest()


class ObjectionHandler:
    """Persist and serve objection responses ranked by conversion_rate.

    Responses are stored per (objection_type, vertical) pair.  Every lookup is
    logged to ``objection_usage_log`` so external feedback loops can later call
    ``update_conversion_rate()`` to improve the rankings.
    """

    @staticmethod
    def _db_path() -> Path:
        override = os.getenv("LEAD_SCORER_DB_PATH")
        path = Path(override).expanduser() if override else Path("data/lead_scores.sqlite3")
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    @classmethod
    async def _connect(cls) -> aiosqlite.Connection:
        db = await aiosqlite.connect(cls._db_path())
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA journal_mode = WAL;")
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS objection_responses (
                id              TEXT PRIMARY KEY,
                objection_type  TEXT NOT NULL,
                vertical        TEXT NOT NULL,
                response_text   TEXT NOT NULL,
                conversion_rate REAL NOT NULL DEFAULT 0.0,
                times_used      INTEGER NOT NULL DEFAULT 0,
                created_at      TEXT NOT NULL
            )
            """
        )
        await db.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_objection_responses_lookup
            ON objection_responses(objection_type, vertical, conversion_rate DESC)
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS objection_usage_log (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                response_id    TEXT,
                objection_type TEXT NOT NULL,
                vertical       TEXT NOT NULL,
                lead_id        TEXT,
                is_fallback    INTEGER NOT NULL DEFAULT 0,
                used_at        TEXT NOT NULL
            )
            """
        )
        await db.commit()
        return db

    async def add_response(
        self,
        objection_type: str,
        vertical: str,
        response_text: str,
        conversion_rate: float = 0.0,
    ) -> str:
        """Upsert a trained response and return its ID."""
        rid = _response_id(objection_type, vertical, response_text)
        db = await self._connect()
        try:
            await db.execute(
                """
                INSERT INTO objection_responses
                    (id, objection_type, vertical, response_text, conversion_rate, times_used, created_at)
                VALUES (?, ?, ?, ?, ?, 0, ?)
                ON CONFLICT(id) DO UPDATE SET
                    conversion_rate = excluded.conversion_rate
                """,
                (
                    rid,
                    objection_type.strip().lower(),
                    vertical.strip().lower(),
                    response_text.strip(),
                    float(conversion_rate),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            await db.commit()
        finally:
            await db.close()
        return rid

    async def update_conversion_rate(self, response_id: str, conversion_rate: float) -> bool:
        """Update the conversion rate for a stored response. Returns True if row existed."""
        db = await self._connect()
        try:
            cursor = await db.execute(
                "UPDATE objection_responses SET conversion_rate = ? WHERE id = ?",
                (float(conversion_rate), response_id),
            )
            await db.commit()
            updated = (cursor.rowcount or 0) > 0
        finally:
            await db.close()
        return updated

    async def get_best_response(
        self,
        objection_type: str,
        vertical: str,
        *,
        lead_id: str | None = None,
    ) -> ObjectionResponse:
        """Return the highest-conversion response for the given objection + vertical.

        Falls back to a built-in generic message when creative_memory has no
        trained response.  Every call is logged to ``objection_usage_log``.
        """
        obj_norm = objection_type.strip().lower()
        vert_norm = vertical.strip().lower()

        db = await self._connect()
        try:
            cursor = await db.execute(
                """
                SELECT id, response_text, conversion_rate
                FROM objection_responses
                WHERE objection_type = ? AND vertical = ?
                ORDER BY conversion_rate DESC, times_used DESC
                LIMIT 1
                """,
                (obj_norm, vert_norm),
            )
            row = await cursor.fetchone()

            if row:
                rid = str(row["id"])
                text = str(row["response_text"])
                rate = float(row["conversion_rate"])
                is_fallback = False
                # Increment usage counter
                await db.execute(
                    "UPDATE objection_responses SET times_used = times_used + 1 WHERE id = ?",
                    (rid,),
                )
            else:
                rid = None
                text = _OBJECTION_FALLBACKS.get(obj_norm, _OBJECTION_FALLBACK_DEFAULT)
                rate = 0.0
                is_fallback = True

            # Log usage so conversion rates can be updated later
            await db.execute(
                """
                INSERT INTO objection_usage_log
                    (response_id, objection_type, vertical, lead_id, is_fallback, used_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    rid,
                    obj_norm,
                    vert_norm,
                    lead_id,
                    1 if is_fallback else 0,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            await db.commit()
        finally:
            await db.close()

        return ObjectionResponse(
            response_id=rid,
            response_text=text,
            objection_type=obj_norm,
            vertical=vert_norm,
            conversion_rate=rate,
            is_fallback=is_fallback,
        )
