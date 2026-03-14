from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
import re
from typing import Any, Dict, List, Optional, TypedDict
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import desc, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from models import BusinessEvent, CrmLead, FollowUpJob

MX_TZ = ZoneInfo("America/Mexico_City")
MEETING_REQUEST_KIND = "meeting_request"
TOMORROW_AGENDA_KIND = "tomorrow_agenda_snapshot"
ACTIVE_MEETING_STATUSES = {"requested", "awaiting_availability", "scheduled_internal"}
NIGHTLY_HOUR = 21
SNAPSHOT_FRESHNESS_HOURS = 12

_MEETING_KEYWORDS = (
    "demo",
    "llamada",
    "reunion",
    "reunión",
    "agendar",
    "cotizacion",
    "cotización",
    "propuesta",
    "quiero hablar",
    "hablar con alguien",
    "implementar",
    "implementación",
)
_TOMORROW_AGENDA_KEYWORDS = (
    "agenda de manana",
    "agenda de mañana",
    "que tengo manana",
    "qué tengo mañana",
    "dame mi agenda de manana",
    "dame mi agenda de mañana",
)
_PRODUCT_KEYWORDS: Dict[str, tuple[str, ...]] = {
    "chatbots": ("chatbot", "chatbots", "whatsapp", "bot"),
    "agentes de voz": ("voz", "llamadas", "recepcionista virtual", "recepcionista"),
    "agentes autonomos": ("autonomo", "autónomo", "autonomos", "autónomos", "automatizacion", "automatización"),
    "paginas web personalizadas": ("pagina web", "página web", "landing", "sitio web", "tienda", "e-commerce", "ecommerce"),
    "starter": ("starter",),
    "growth": ("growth",),
    "commerce": ("commerce",),
    "enterprise": ("enterprise",),
    "combos": ("combo", "combos", "digitalizacion completa", "digitalización completa"),
    "add-ons": ("seo", "blog", "redes sociales", "ads", "email marketing", "mantenimiento web"),
}
_HIGH_URGENCY_KEYWORDS = ("urgente", "hoy", "lo antes posible", "ya", "esta semana", "esta misma semana")
_TIME_WINDOW_PATTERNS: tuple[tuple[str, str], ...] = (
    ("mañana por la mañana", "mañana por la mañana"),
    ("manana por la manana", "mañana por la mañana"),
    ("mañana por la tarde", "mañana por la tarde"),
    ("manana por la tarde", "mañana por la tarde"),
    ("hoy por la tarde", "hoy por la tarde"),
    ("hoy por la mañana", "hoy por la mañana"),
    ("esta semana", "esta semana"),
    ("mañana", "mañana"),
    ("manana", "mañana"),
    ("tarde", "por la tarde"),
    ("mañana temprano", "mañana temprano"),
    ("manana temprano", "mañana temprano"),
)


class MeetingRequestPayload(TypedDict, total=False):
    kind: str
    session_id: str
    channel: str
    source_message: str
    business_need: str
    products_of_interest: List[str]
    preferred_time_windows: List[str]
    urgency: str
    contact_name: Optional[str]
    phone: Optional[str]
    email: Optional[str]
    status: str


class TomorrowAgendaSnapshot(TypedDict):
    target_date: str
    source_status: str
    meetings: List[Dict[str, Any]]
    internal_followups: List[Dict[str, Any]]
    critical_items: List[Dict[str, Any]]
    summary_text: str
    generated_at: str


def _normalize(text: str) -> str:
    token = str(text or "").strip().lower()
    replacements = (
        ("á", "a"),
        ("é", "e"),
        ("í", "i"),
        ("ó", "o"),
        ("ú", "u"),
        ("ü", "u"),
    )
    for before, after in replacements:
        token = token.replace(before, after)
    return token


def looks_like_meeting_request(text: str) -> bool:
    normalized = _normalize(text)
    return any(keyword in normalized for keyword in _MEETING_KEYWORDS)


def looks_like_tomorrow_agenda_request(text: str) -> bool:
    normalized = _normalize(text)
    return any(keyword in normalized for keyword in _TOMORROW_AGENDA_KEYWORDS)


def _detect_channel(session_id: str) -> str:
    token = str(session_id or "").strip()
    if ":" in token:
        prefix, _ = token.split(":", 1)
        return prefix.strip() or "chat"
    return "chat"


def _extract_phone(session_id: str) -> Optional[str]:
    token = str(session_id or "").strip()
    if token.startswith("whatsapp:"):
        return token.split(":", 1)[1].strip() or None
    return None


def _extract_products(message: str) -> List[str]:
    normalized = _normalize(message)
    matches: List[str] = []
    for product, keywords in _PRODUCT_KEYWORDS.items():
        if any(keyword in normalized for keyword in keywords):
            matches.append(product)
    return matches


def _extract_time_windows(message: str) -> List[str]:
    normalized = _normalize(message)
    found: List[str] = []
    for pattern, label in _TIME_WINDOW_PATTERNS:
        if pattern in normalized and label not in found:
            found.append(label)
    return found


def _extract_urgency(message: str) -> str:
    normalized = _normalize(message)
    if any(keyword in normalized for keyword in _HIGH_URGENCY_KEYWORDS):
        return "high"
    if "mañana" in normalized or "manana" in normalized:
        return "medium"
    return "normal"


def _extract_contact_name(message: str) -> Optional[str]:
    match = re.search(r"\bsoy\s+([A-Za-zÁÉÍÓÚÜÑáéíóúüñ ]{2,40})", str(message or ""))
    if not match:
        return None
    token = match.group(1).strip()
    return token or None


def _default_followup_time(*, urgency: str, now_utc: Optional[datetime] = None) -> datetime:
    now = now_utc or datetime.now(timezone.utc)
    local_now = now.astimezone(MX_TZ)
    if urgency == "high" and local_now.hour < 17:
        candidate = (local_now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
        return candidate.astimezone(timezone.utc)
    tomorrow = (local_now + timedelta(days=1)).date()
    candidate = datetime.combine(tomorrow, time(hour=9, minute=0), tzinfo=MX_TZ)
    return candidate.astimezone(timezone.utc)


def _next_nightly_run(now_utc: Optional[datetime] = None) -> datetime:
    now = now_utc or datetime.now(timezone.utc)
    local_now = now.astimezone(MX_TZ)
    candidate_date = local_now.date()
    candidate = datetime.combine(candidate_date, time(hour=NIGHTLY_HOUR), tzinfo=MX_TZ)
    if candidate <= local_now:
        candidate = datetime.combine(candidate_date + timedelta(days=1), time(hour=NIGHTLY_HOUR), tzinfo=MX_TZ)
    return candidate.astimezone(timezone.utc)


def _tomorrow_local_date(now_utc: Optional[datetime] = None) -> date:
    now = now_utc or datetime.now(timezone.utc)
    return (now.astimezone(MX_TZ).date() + timedelta(days=1))


def _compose_meeting_reply(*, products: List[str], preferred_time_windows: List[str], status: str) -> str:
    intro = "Yo ya registré tu solicitud para una reunión comercial."
    if products:
        intro += f" Lo que más me pides revisar es: {', '.join(products)}."
    if preferred_time_windows:
        windows = ", ".join(preferred_time_windows[:2])
        return (
            f"{intro} Tomé como referencia {windows}. "
            "Yo te voy a dar seguimiento internamente para confirmar el mejor espacio, pero todavía no la tomo como reunión cerrada hasta validarlo."
        )
    return (
        f"{intro} Para avanzar, compárteme 1 o 2 ventanas de horario que te funcionen y yo las dejo registradas para seguimiento."
    )


def _meeting_job_to_item(job: FollowUpJob) -> Dict[str, Any]:
    payload = dict(job.payload or {})
    return {
        "job_id": str(job.id),
        "status": job.status,
        "scheduled_for": job.scheduled_for.astimezone(MX_TZ).isoformat(),
        "products_of_interest": list(payload.get("products_of_interest") or []),
        "preferred_time_windows": list(payload.get("preferred_time_windows") or []),
        "business_need": str(payload.get("business_need") or ""),
        "lead_id": str(job.lead_id) if job.lead_id else None,
    }


def _followup_job_to_item(job: FollowUpJob) -> Dict[str, Any]:
    payload = dict(job.payload or {})
    return {
        "job_id": str(job.id),
        "status": job.status,
        "scheduled_for": job.scheduled_for.astimezone(MX_TZ).isoformat(),
        "message": str(payload.get("message") or payload.get("kind") or "seguimiento interno"),
    }


def _lead_to_item(lead: CrmLead) -> Dict[str, Any]:
    return {
        "lead_id": str(lead.id),
        "name": lead.name,
        "email": lead.email,
        "phone": lead.phone,
        "source": lead.source,
        "score": float(lead.score or 0.0),
        "conversion_probability": float(lead.conversion_probability or 0.0),
    }


def _build_agenda_summary(snapshot: TomorrowAgendaSnapshot) -> str:
    target_date = snapshot["target_date"]
    meetings = snapshot["meetings"]
    followups = snapshot["internal_followups"]
    critical_items = snapshot["critical_items"]
    lines: List[str] = [f"Yo ya preparé mi agenda de mañana ({target_date})."]
    if snapshot["source_status"] != "calendar_ok":
        lines.append("No tuve acceso al calendario externo, así que armé este resumen con reuniones internas y pendientes críticos.")
    if meetings:
        lines.append(f"Reuniones por atender: {len(meetings)}.")
        for item in meetings[:3]:
            products = ", ".join(item.get("products_of_interest") or []) or "sin producto definido"
            when = item.get("preferred_time_windows") or [item.get("scheduled_for", "sin horario")]
            lines.append(f"- Reunión: {products} | {', '.join(when[:2])}")
    else:
        lines.append("No tengo reuniones internas confirmadas para mañana.")
    if followups:
        lines.append(f"Pendientes internos: {len(followups)}.")
        for item in followups[:3]:
            lines.append(f"- Seguimiento: {item.get('message')}")
    if critical_items:
        lines.append("Leads calientes:")
        for item in critical_items[:3]:
            name = item.get("name") or item.get("email") or item.get("phone") or item.get("lead_id")
            lines.append(f"- {name} | score {item.get('score', 0):.2f}")
    return "\n".join(lines)


async def ensure_tomorrow_agenda_snapshot_job(
    session: AsyncSession,
    *,
    organization_id: UUID,
) -> FollowUpJob:
    next_run = _next_nightly_run()
    stmt = (
        select(FollowUpJob)
        .where(
            FollowUpJob.organization_id == organization_id,
            FollowUpJob.status == "scheduled",
            FollowUpJob.scheduled_for == next_run,
        )
        .order_by(desc(FollowUpJob.created_at))
        .limit(10)
    )
    rows = list((await session.execute(stmt)).scalars().all())
    for row in rows:
        if dict(row.payload or {}).get("kind") == TOMORROW_AGENDA_KIND:
            return row

    job = FollowUpJob(
        organization_id=organization_id,
        lead_id=None,
        status="scheduled",
        scheduled_for=next_run,
        decision_reason="Nightly tomorrow agenda snapshot generation.",
        payload={
            "kind": TOMORROW_AGENDA_KIND,
            "timezone": "America/Mexico_City",
            "target_date": _tomorrow_local_date().isoformat(),
        },
        result={},
    )
    session.add(job)
    await session.commit()
    return job


async def create_meeting_request(
    session: AsyncSession,
    *,
    organization_id: UUID,
    session_id: str,
    message: str,
) -> Dict[str, Any]:
    from brain.crm_engine import upsert_lead
    from brain.creative_attribution import resolve_campaign
    from brain.creative_event_tracker import record_whatsapp_outcome

    channel = _detect_channel(session_id)
    phone = _extract_phone(session_id)
    products = _extract_products(message)
    preferred_time_windows = _extract_time_windows(message)
    urgency = _extract_urgency(message)
    contact_name = _extract_contact_name(message)

    lead = await upsert_lead(
        session,
        organization_id=organization_id,
        payload={
            "external_id": session_id,
            "name": contact_name,
            "phone": phone,
            "source": channel,
            "status": "meeting_requested",
            "tags": ["meeting_request", *products],
            "metadata": {
                "last_meeting_request_message": message,
                "products_of_interest": products,
                "preferred_time_windows": preferred_time_windows,
                "urgency": urgency,
            },
        },
    )

    stmt = (
        select(FollowUpJob)
        .where(
            FollowUpJob.organization_id == organization_id,
            FollowUpJob.lead_id == lead.id,
            FollowUpJob.status.in_(list(ACTIVE_MEETING_STATUSES)),
        )
        .order_by(desc(FollowUpJob.created_at))
        .limit(10)
    )
    active_jobs = list((await session.execute(stmt)).scalars().all())
    existing: Optional[FollowUpJob] = None
    for job in active_jobs:
        if dict(job.payload or {}).get("kind") == MEETING_REQUEST_KIND:
            existing = job
            break

    status = "requested" if preferred_time_windows else "awaiting_availability"
    payload: MeetingRequestPayload = {
        "kind": MEETING_REQUEST_KIND,
        "session_id": session_id,
        "channel": channel,
        "source_message": message,
        "business_need": message[:500],
        "products_of_interest": products,
        "preferred_time_windows": preferred_time_windows,
        "urgency": urgency,
        "contact_name": contact_name,
        "phone": phone,
        "email": lead.email,
        "status": status,
    }

    event_type = "meeting_request.updated"
    if existing is not None:
        merged_payload = dict(existing.payload or {})
        merged_payload.update(payload)
        existing.payload = merged_payload
        existing.status = status
        existing.decision_reason = "Meeting request refreshed from conversational intent."
        existing.scheduled_for = _default_followup_time(urgency=urgency)
        job = existing
    else:
        event_type = "meeting_request.created"
        job = FollowUpJob(
            organization_id=organization_id,
            lead_id=lead.id,
            status=status,
            scheduled_for=_default_followup_time(urgency=urgency),
            decision_reason="Commercial meeting request captured from conversational channel.",
            payload=payload,
            result={},
        )
        session.add(job)

    session.add(
        BusinessEvent(
            organization_id=organization_id,
            event_type=event_type,
            source="commercial_agenda",
            channel=channel,
            entity_id=session_id,
            payload={
                "lead_id": str(lead.id),
                "meeting_request_status": status,
                "products_of_interest": products,
                "preferred_time_windows": preferred_time_windows,
                "urgency": urgency,
            },
        )
    )
    await session.commit()
    try:
        resolved_campaign = resolve_campaign(
            session_id,
            hints={
                "raw_text": message,
                "source_campaign": dict(getattr(lead, "metadata", {}) or {}).get("source_campaign"),
                "utm_campaign": dict(getattr(lead, "metadata", {}) or {}).get("utm_campaign"),
                "ad_name": dict(getattr(lead, "metadata", {}) or {}).get("ad_name"),
            },
        )
        if resolved_campaign:
            record_whatsapp_outcome(
                resolved_campaign,
                meeting_requested=True,
            )
    except Exception:
        pass
    await ensure_tomorrow_agenda_snapshot_job(session, organization_id=organization_id)
    return {
        "reply": _compose_meeting_reply(
            products=products,
            preferred_time_windows=preferred_time_windows,
            status=status,
        ),
        "job": job,
        "lead": lead,
    }


async def _has_newer_agenda_inputs(
    session: AsyncSession,
    *,
    organization_id: UUID,
    snapshot_created_at: datetime,
) -> bool:
    meeting_stmt = (
        select(BusinessEvent)
        .where(
            BusinessEvent.organization_id == organization_id,
            BusinessEvent.event_type.in_(["meeting_request.created", "meeting_request.updated"]),
        )
        .order_by(desc(BusinessEvent.created_at))
        .limit(1)
    )
    meeting_evt = (await session.execute(meeting_stmt)).scalars().first()
    if meeting_evt is not None and meeting_evt.created_at > snapshot_created_at:
        return True

    followup_stmt = (
        select(FollowUpJob)
        .where(FollowUpJob.organization_id == organization_id)
        .order_by(desc(FollowUpJob.updated_at))
        .limit(1)
    )
    followup = (await session.execute(followup_stmt)).scalars().first()
    if followup is not None and followup.updated_at > snapshot_created_at:
        return True
    return False


async def get_recent_tomorrow_agenda_snapshot(
    session: AsyncSession,
    *,
    organization_id: UUID,
    target_date: Optional[date] = None,
) -> Optional[TomorrowAgendaSnapshot]:
    desired_date = target_date or _tomorrow_local_date()
    stmt = (
        select(BusinessEvent)
        .where(
            BusinessEvent.organization_id == organization_id,
            BusinessEvent.event_type == "tomorrow_agenda_snapshot.generated",
        )
        .order_by(desc(BusinessEvent.created_at))
        .limit(10)
    )
    rows = list((await session.execute(stmt)).scalars().all())
    freshness_cutoff = datetime.now(timezone.utc) - timedelta(hours=SNAPSHOT_FRESHNESS_HOURS)
    for row in rows:
        payload = dict(row.payload or {})
        if payload.get("target_date") != desired_date.isoformat():
            continue
        if row.created_at < freshness_cutoff:
            continue
        if await _has_newer_agenda_inputs(
            session,
            organization_id=organization_id,
            snapshot_created_at=row.created_at,
        ):
            continue
        return payload  # type: ignore[return-value]
    return None


async def build_tomorrow_agenda_snapshot(
    session: AsyncSession,
    *,
    organization_id: UUID,
    target_date: Optional[date] = None,
) -> TomorrowAgendaSnapshot:
    desired_date = target_date or _tomorrow_local_date()
    start_local = datetime.combine(desired_date, time.min, tzinfo=MX_TZ)
    end_local = datetime.combine(desired_date, time.max, tzinfo=MX_TZ)
    start_utc = start_local.astimezone(timezone.utc)
    end_utc = end_local.astimezone(timezone.utc)

    meeting_stmt = (
        select(FollowUpJob)
        .where(
            FollowUpJob.organization_id == organization_id,
            FollowUpJob.status.in_(list(ACTIVE_MEETING_STATUSES)),
            FollowUpJob.scheduled_for >= start_utc,
            FollowUpJob.scheduled_for <= end_utc,
        )
        .order_by(FollowUpJob.scheduled_for.asc())
        .limit(20)
    )
    meeting_jobs = [
        row
        for row in list((await session.execute(meeting_stmt)).scalars().all())
        if dict(row.payload or {}).get("kind") == MEETING_REQUEST_KIND
    ]

    followup_stmt = (
        select(FollowUpJob)
        .where(
            FollowUpJob.organization_id == organization_id,
            FollowUpJob.scheduled_for >= start_utc,
            FollowUpJob.scheduled_for <= end_utc,
        )
        .order_by(FollowUpJob.scheduled_for.asc())
        .limit(20)
    )
    followup_jobs = [
        row
        for row in list((await session.execute(followup_stmt)).scalars().all())
        if dict(row.payload or {}).get("kind") not in {MEETING_REQUEST_KIND, TOMORROW_AGENDA_KIND}
    ]

    leads_stmt = (
        select(CrmLead)
        .where(
            CrmLead.organization_id == organization_id,
            or_(
                CrmLead.score >= 0.65,
                CrmLead.conversion_probability >= 0.5,
            ),
        )
        .order_by(desc(CrmLead.conversion_probability), desc(CrmLead.score))
        .limit(10)
    )
    hot_leads = list((await session.execute(leads_stmt)).scalars().all())

    snapshot: TomorrowAgendaSnapshot = {
        "target_date": desired_date.isoformat(),
        "source_status": "calendar_unavailable",
        "meetings": [_meeting_job_to_item(job) for job in meeting_jobs],
        "internal_followups": [_followup_job_to_item(job) for job in followup_jobs[:6]],
        "critical_items": [_lead_to_item(lead) for lead in hot_leads[:5]],
        "summary_text": "",
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    snapshot["summary_text"] = _build_agenda_summary(snapshot)
    session.add(
        BusinessEvent(
            organization_id=organization_id,
            event_type="tomorrow_agenda_snapshot.generated",
            source="commercial_agenda",
            channel="internal",
            entity_id=desired_date.isoformat(),
            payload=dict(snapshot),
        )
    )
    await session.commit()
    return snapshot


async def maybe_handle_special_request(
    session: AsyncSession,
    *,
    organization_id: UUID,
    session_id: str,
    message: str,
) -> Optional[Dict[str, Any]]:
    if looks_like_tomorrow_agenda_request(message):
        snapshot = await get_recent_tomorrow_agenda_snapshot(
            session,
            organization_id=organization_id,
        )
        if snapshot is None:
            snapshot = await build_tomorrow_agenda_snapshot(
                session,
                organization_id=organization_id,
            )
        await ensure_tomorrow_agenda_snapshot_job(session, organization_id=organization_id)
        return {
            "type": "message",
            "content": snapshot["summary_text"],
            "agenda_snapshot": snapshot,
        }

    if looks_like_meeting_request(message):
        result = await create_meeting_request(
            session,
            organization_id=organization_id,
            session_id=session_id,
            message=message,
        )
        job = result["job"]
        return {
            "type": "message",
            "content": str(result["reply"]),
            "meeting_request": {
                "job_id": str(job.id),
                "status": job.status,
            },
        }

    return None
