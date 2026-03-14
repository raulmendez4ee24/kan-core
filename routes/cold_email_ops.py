"""Cold Email Ops API: POST /run con Sheets + DB sync (Opción B)."""
from __future__ import annotations

import asyncio
import logging
import os
from functools import partial
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from brain.cold_email_ops.orchestrator import run_cold_email_ops
from database import get_session
from schemas.cold_email_ops import ColdEmailOpsRunRequest, ColdEmailOpsRunResponse
from security import require_client_token

router = APIRouter(prefix="/cold-email-ops", tags=["cold-email-ops"])
_log = logging.getLogger("kan_core.routes.cold_email_ops")


def _is_enabled() -> bool:
    return os.getenv("ENABLE_COLD_EMAIL_OPS", "false").lower() in ("1", "true", "yes")


def _db_enabled() -> bool:
    """DB sync is opt-out: active unless COLD_EMAIL_DB_SYNC=false."""
    return os.getenv("COLD_EMAIL_DB_SYNC", "true").lower() not in ("0", "false", "no")


@router.post(
    "/run",
    response_model=ColdEmailOpsRunResponse,
    summary="Run Cold Email Ops pipeline",
    description=(
        "Loads Google Sheet (spreadsheet_id or COLD_EMAIL_SPREADSHEET_ID), runs audit (Phase 0-1). "
        "If solo_audit or blockers, returns immediately. Otherwise runs cleanup, verification, "
        "who-to-send, domains/costs, inbox rotation, copy gen, export, reply monitor. "
        "Auth: X-Client-Id + X-Client-Token (or CLIENT_TOKENS_JSON). "
        "DB sync (ce_runs, ce_sends, ce_leads, ce_inboxes, ce_campaigns, ce_domains) runs "
        "after the pipeline unless COLD_EMAIL_DB_SYNC=false."
    ),
)
async def run_cold_email_ops_route(
    req: ColdEmailOpsRunRequest,
    client_id: str = Depends(require_client_token),
    db: AsyncSession = Depends(get_session),
):
    if not _is_enabled():
        raise HTTPException(status_code=404, detail="ENABLE_COLD_EMAIL_OPS is disabled")

    # ------------------------------------------------------------------
    # 1. Correr el orchestrator en thread pool (hace I/O bloqueante a Sheets)
    # ------------------------------------------------------------------
    try:
        loop = asyncio.get_event_loop()
        result: ColdEmailOpsRunResponse = await loop.run_in_executor(
            None, partial(run_cold_email_ops, req)
        )
    except Exception as e:
        _log.exception("Orchestrator error")
        raise HTTPException(status_code=500, detail=str(e)) from e

    # ------------------------------------------------------------------
    # 2. DB sync — no bloquea la respuesta si falla
    # ------------------------------------------------------------------
    if _db_enabled():
        spreadsheet_id = req.spreadsheet_id or os.getenv("COLD_EMAIL_SPREADSHEET_ID", "").strip()
        try:
            await _persist_run(db, spreadsheet_id, client_id, req, result)
        except Exception as exc:
            # DB sync failure never rompe el flujo principal
            _log.warning("DB sync failed (non-fatal): %s", exc)

    return result


async def _persist_run(
    db: AsyncSession,
    spreadsheet_id: str,
    client_id: str,
    req: ColdEmailOpsRunRequest,
    result: ColdEmailOpsRunResponse,
) -> None:
    """Sync Sheet data → DB and record this run + queued sends."""
    from brain.cold_email_ops.db_sync import (
        create_run_record,
        create_send_records,
        finish_run_record,
        sync_sheet_to_db,
    )

    if not spreadsheet_id:
        return

    # Crear run record
    run = await create_run_record(db, spreadsheet_id, client_id)

    # Re-leer el Sheet para sincronizar a DB (los datos ya están en caché de gspread)
    sheet_data: Dict[str, Any] = {}
    try:
        loop = asyncio.get_event_loop()
        from tools.google_sheets_client import read_all_tabs
        sheet_data = await loop.run_in_executor(None, partial(read_all_tabs, spreadsheet_id))
    except Exception as exc:
        _log.warning("DB sync: could not re-read sheet for upsert: %s", exc)

    # Upsert leads/inboxes/campaigns/domains
    counts = {"leads": 0, "inboxes": 0}
    if sheet_data:
        counts = await sync_sheet_to_db(db, spreadsheet_id, sheet_data)

    # Registrar sends de la cola de hoy
    cola = getattr(result, "cola_de_hoy", {}) or {}
    await create_send_records(db, run, cola)

    # Cerrar el run
    await finish_run_record(
        db, run, result,
        leads_synced=counts.get("leads", 0),
        inboxes_synced=counts.get("inboxes", 0),
    )

    await db.commit()
    _log.info(
        "DB sync OK: run=%s leads_synced=%d leads_queued=%d",
        run.id,
        counts.get("leads", 0),
        len(cola.get("leads", [])),
    )
