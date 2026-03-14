"""DB sync layer for Cold Email Ops (Opción B: Sheets + DB en paralelo).

Responsabilidades:
- sync_sheet_to_db   → upsert leads/inboxes/campaigns/domains desde los datos del Sheet
- create_run_record  → crea un ce_runs con status='running' al inicio de cada ejecución
- finish_run_record  → actualiza el run con resultado, bloqueos y timestamps
- create_send_records → inserta un ce_sends por cada lead en cola_de_hoy (snapshot congelado)

Diseño para migración futura a "solo DB":
- Todas las funciones reciben `data` directamente (el dict que hoy viene de Sheets).
- Cuando exista un input alternativo (UI, API propia), sólo hay que cambiar quién
  produce ese dict — las funciones de sync no cambian.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

_log = logging.getLogger("kan_core.cold_email_ops.db_sync")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _str(v: Any, maxlen: int = 0) -> Optional[str]:
    """Coerce to str, strip whitespace. Returns None if empty."""
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    return s[:maxlen] if maxlen else s


def _int(v: Any) -> Optional[int]:
    if v is None:
        return None
    try:
        return int(float(str(v)))
    except (ValueError, TypeError):
        return None


def _lead_key(row: Dict[str, Any], idx: int) -> str:
    """Stable unique key for a lead: sheet lead_id → str, fallback to email, fallback to idx."""
    v = _str(row.get("lead_id"))
    if v:
        return v
    v = _str(row.get("email"))
    if v:
        return v
    return f"__row_{idx}"


def _inbox_key(row: Dict[str, Any], idx: int) -> str:
    v = _str(row.get("inbox_id"))
    if v:
        return v
    v = _str(row.get("from_email"))
    if v:
        return v
    return f"__row_{idx}"


def _campaign_key(row: Dict[str, Any], idx: int) -> str:
    v = _str(row.get("campaign_id"))
    if v:
        return v
    return f"__row_{idx}"


# ---------------------------------------------------------------------------
# create_run_record
# ---------------------------------------------------------------------------

async def create_run_record(
    db: AsyncSession,
    spreadsheet_id: str,
    client_id: Optional[str] = None,
) -> Any:
    """Insert a ce_runs row with status='running'. Returns the ORM object."""
    from models import ColdEmailRun  # late import — avoids circular at module load

    run = ColdEmailRun(
        id=uuid.uuid4(),
        spreadsheet_id=spreadsheet_id,
        client_id=client_id,
        status="running",
        leads_synced=0,
        inboxes_synced=0,
        leads_queued=0,
        bloqueos=[],
        result_summary={},
    )
    db.add(run)
    await db.flush()  # get the PK without committing
    return run


# ---------------------------------------------------------------------------
# finish_run_record
# ---------------------------------------------------------------------------

async def finish_run_record(
    db: AsyncSession,
    run: Any,
    response: Any,
    leads_synced: int = 0,
    inboxes_synced: int = 0,
) -> None:
    """Update ce_runs after the orchestrator finishes."""
    bloqueos = getattr(response, "bloqueos", []) or []
    cola = getattr(response, "cola_de_hoy", {}) or {}
    leads_queued = len(cola.get("leads", []))

    run.status = "blocked" if bloqueos else "ok"
    run.bloqueos = bloqueos
    run.leads_synced = leads_synced
    run.inboxes_synced = inboxes_synced
    run.leads_queued = leads_queued
    run.result_summary = {
        "resumen_ejecutivo": getattr(response, "resumen_ejecutivo", []),
        "archivos_generados": getattr(response, "archivos_generados", []),
        "log_decisiones": getattr(response, "log_decisiones", [])[-10:],  # last 10 only
    }
    run.finished_at = datetime.now(timezone.utc)
    await db.flush()


# ---------------------------------------------------------------------------
# sync_sheet_to_db
# ---------------------------------------------------------------------------

async def sync_sheet_to_db(
    db: AsyncSession,
    spreadsheet_id: str,
    data: Dict[str, List[Dict[str, Any]]],
) -> Dict[str, int]:
    """Upsert leads, inboxes, campaigns, domains from Sheet data into DB.

    Returns counts: {"leads": N, "inboxes": N, "campaigns": N, "domains": N}
    Uses PostgreSQL INSERT … ON CONFLICT DO UPDATE for idempotent upserts.
    """
    counts = {"leads": 0, "inboxes": 0, "campaigns": 0, "domains": 0}

    counts["leads"] = await _upsert_leads(db, spreadsheet_id, data.get("leads", []))
    counts["inboxes"] = await _upsert_inboxes(db, spreadsheet_id, data.get("inboxes", []))
    counts["campaigns"] = await _upsert_campaigns(db, spreadsheet_id, data.get("campaign", []))
    counts["domains"] = await _upsert_domains(db, spreadsheet_id, data.get("domains", []))

    return counts


async def _upsert_leads(
    db: AsyncSession,
    spreadsheet_id: str,
    rows: List[Dict[str, Any]],
) -> int:
    if not rows:
        return 0
    from models import ColdEmailLead

    records = []
    for idx, row in enumerate(rows):
        key = _lead_key(row, idx)
        records.append({
            "id": uuid.uuid4(),
            "spreadsheet_id": spreadsheet_id,
            "sheet_lead_id": key,
            "company": _str(row.get("company"), 240),
            "website": _str(row.get("website"), 500),
            "first_name": _str(row.get("first_name"), 120),
            "last_name": _str(row.get("last_name"), 120),
            "title": _str(row.get("title"), 200),
            "email": _str(row.get("email"), 320),
            "linkedin": _str(row.get("linkedin"), 500),
            "country": _str(row.get("country"), 8),
            "segment": _str(row.get("segment"), 80),
            "status": _str(row.get("status"), 40),
            "last_contacted_at": _str(row.get("last_contacted_at"), 32),
            "source": _str(row.get("source"), 80),
            "notes": _str(row.get("notes")),
            "personalization_snippet": _str(row.get("personalization_snippet")),
            "email_validation_status": _str(row.get("email_validation_status"), 20),
            "score": _int(row.get("score")),
        })

    stmt = pg_insert(ColdEmailLead).values(records)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_ce_leads_sheet",
        set_={
            "company": stmt.excluded.company,
            "website": stmt.excluded.website,
            "first_name": stmt.excluded.first_name,
            "last_name": stmt.excluded.last_name,
            "title": stmt.excluded.title,
            "email": stmt.excluded.email,
            "linkedin": stmt.excluded.linkedin,
            "country": stmt.excluded.country,
            "segment": stmt.excluded.segment,
            "status": stmt.excluded.status,
            "last_contacted_at": stmt.excluded.last_contacted_at,
            "source": stmt.excluded.source,
            "notes": stmt.excluded.notes,
            "personalization_snippet": stmt.excluded.personalization_snippet,
            "email_validation_status": stmt.excluded.email_validation_status,
            "score": stmt.excluded.score,
            "synced_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
        },
    )
    await db.execute(stmt)
    return len(records)


async def _upsert_inboxes(
    db: AsyncSession,
    spreadsheet_id: str,
    rows: List[Dict[str, Any]],
) -> int:
    if not rows:
        return 0
    from models import ColdEmailInbox

    records = []
    for idx, row in enumerate(rows):
        key = _inbox_key(row, idx)
        records.append({
            "id": uuid.uuid4(),
            "spreadsheet_id": spreadsheet_id,
            "sheet_inbox_id": key,
            "provider": _str(row.get("provider"), 40),
            "from_name": _str(row.get("from_name"), 200),
            "from_email": _str(row.get("from_email"), 320),
            "domain": _str(row.get("domain"), 253),
            "warmup_status": _str(row.get("warmup_status"), 20),
            "daily_limit": _int(row.get("daily_limit")),
            "sent_today": _int(row.get("sent_today")),
            "health_score": _int(row.get("health_score")),
            "last_error": _str(row.get("last_error")),
            "rotation_group": _str(row.get("rotation_group"), 80),
        })

    stmt = pg_insert(ColdEmailInbox).values(records)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_ce_inboxes_sheet",
        set_={
            "provider": stmt.excluded.provider,
            "from_name": stmt.excluded.from_name,
            "from_email": stmt.excluded.from_email,
            "domain": stmt.excluded.domain,
            "warmup_status": stmt.excluded.warmup_status,
            "daily_limit": stmt.excluded.daily_limit,
            "sent_today": stmt.excluded.sent_today,
            "health_score": stmt.excluded.health_score,
            "last_error": stmt.excluded.last_error,
            "rotation_group": stmt.excluded.rotation_group,
            "synced_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
        },
    )
    await db.execute(stmt)
    return len(records)


async def _upsert_campaigns(
    db: AsyncSession,
    spreadsheet_id: str,
    rows: List[Dict[str, Any]],
) -> int:
    if not rows:
        return 0
    from models import ColdEmailCampaign

    records = []
    for idx, row in enumerate(rows):
        key = _campaign_key(row, idx)
        records.append({
            "id": uuid.uuid4(),
            "spreadsheet_id": spreadsheet_id,
            "sheet_campaign_id": key,
            "offer": _str(row.get("offer")),
            "target_segment": _str(row.get("target_segment"), 160),
            "sequence": _str(row.get("sequence")),
            "spacing_days": _int(row.get("spacing_days")),
            "send_window_localtime": _str(row.get("send_window_localtime"), 40),
            "max_new_leads_per_day": _int(row.get("max_new_leads_per_day")),
            "personalization_rules": _str(row.get("personalization_rules")),
        })

    stmt = pg_insert(ColdEmailCampaign).values(records)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_ce_campaigns_sheet",
        set_={
            "offer": stmt.excluded.offer,
            "target_segment": stmt.excluded.target_segment,
            "sequence": stmt.excluded.sequence,
            "spacing_days": stmt.excluded.spacing_days,
            "send_window_localtime": stmt.excluded.send_window_localtime,
            "max_new_leads_per_day": stmt.excluded.max_new_leads_per_day,
            "personalization_rules": stmt.excluded.personalization_rules,
            "synced_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
        },
    )
    await db.execute(stmt)
    return len(records)


async def _upsert_domains(
    db: AsyncSession,
    spreadsheet_id: str,
    rows: List[Dict[str, Any]],
) -> int:
    if not rows:
        return 0
    from models import ColdEmailDomain

    records = []
    for row in rows:
        domain = _str(row.get("domain"), 253)
        if not domain:
            continue
        records.append({
            "id": uuid.uuid4(),
            "spreadsheet_id": spreadsheet_id,
            "domain": domain,
            "registrar": _str(row.get("registrar"), 120),
            "dns_status": _str(row.get("dns_status"), 20),
            "spf_status": _str(row.get("spf_status"), 20),
            "dkim_status": _str(row.get("dkim_status"), 20),
            "dmarc_status": _str(row.get("dmarc_status"), 20),
            "tracking_subdomain_status": _str(row.get("tracking_subdomain_status"), 20),
            "tls_status": _str(row.get("tls_status"), 20),
            "notes": _str(row.get("notes")),
            "monthly_cost_estimate": _str(row.get("monthly_cost_estimate"), 40),
        })

    if not records:
        return 0

    stmt = pg_insert(ColdEmailDomain).values(records)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_ce_domains_sheet",
        set_={
            "registrar": stmt.excluded.registrar,
            "dns_status": stmt.excluded.dns_status,
            "spf_status": stmt.excluded.spf_status,
            "dkim_status": stmt.excluded.dkim_status,
            "dmarc_status": stmt.excluded.dmarc_status,
            "tracking_subdomain_status": stmt.excluded.tracking_subdomain_status,
            "tls_status": stmt.excluded.tls_status,
            "notes": stmt.excluded.notes,
            "monthly_cost_estimate": stmt.excluded.monthly_cost_estimate,
            "synced_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
        },
    )
    await db.execute(stmt)
    return len(records)


# ---------------------------------------------------------------------------
# create_send_records
# ---------------------------------------------------------------------------

async def create_send_records(
    db: AsyncSession,
    run: Any,
    cola_de_hoy: Dict[str, Any],
) -> int:
    """Insert one ce_sends row per lead in cola_de_hoy['leads'].

    Fields are a frozen snapshot at queue time — no updates after insert.
    The inbox assignment (from_email) comes from the lead dict after inbox_rotation.
    """
    from models import ColdEmailSend

    leads = cola_de_hoy.get("leads", [])
    if not leads:
        return 0

    # Detect campaign_id from first campaign row stored in run context (best-effort)
    records = []
    for idx, lead in enumerate(leads):
        key = _lead_key(lead, idx)
        records.append({
            "id": uuid.uuid4(),
            "run_id": run.id,
            "spreadsheet_id": run.spreadsheet_id,
            "sheet_lead_id": key,
            "email": _str(lead.get("email"), 320),
            "company": _str(lead.get("company"), 240),
            "first_name": _str(lead.get("first_name"), 120),
            "last_name": _str(lead.get("last_name"), 120),
            "segment": _str(lead.get("segment"), 80),
            # from_email is set by inbox_rotation phase onto each lead dict
            "from_email": _str(lead.get("from_email"), 320),
            "campaign_id": _str(lead.get("campaign_id"), 160),
            "personalization_snippet": _str(lead.get("personalization_snippet")),
            "email_validation_status": _str(lead.get("email_validation_status"), 20),
            "score": _int(lead.get("score")),
            "status": "queued",
        })

    if not records:
        return 0

    stmt = pg_insert(ColdEmailSend).values(records)
    # On re-run same day: ignore duplicates (uq_ce_sends_run_lead)
    stmt = stmt.on_conflict_do_nothing(constraint="uq_ce_sends_run_lead")
    await db.execute(stmt)
    return len(records)
