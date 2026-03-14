"""Fase 9.5: Writeback — escribe los campos modificados por el pipeline de vuelta al Google Sheet.

Campos que el pipeline actualiza en memoria y que hay que persistir en Sheets:
  - email_validation_status  (Fase 3: email_verification)
  - personalization_snippet  (Fase 7: copy_gen)
  - status                   (si el pipeline marca leads como queued)
  - notes                    (metadata de secuencia / campaign para follow-ups)

Usa batch_update_by_header con lead_id como clave, que es la columna maestra del Sheet.
Si falla (red, permisos, etc.) loguea warning y continúa — nunca bloquea el pipeline.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Tuple

_log = logging.getLogger("kan_core.cold_email_ops.sheet_writeback")

# Columnas del Sheet que el pipeline puede modificar (las únicas que escribimos de vuelta)
WRITEBACK_COLUMNS = [
    "email_validation_status",
    "personalization_snippet",
    "status",
    "notes",
]


def run(
    spreadsheet_id: str,
    data: Dict[str, List[Dict[str, Any]]],
    log: List[str],
) -> Tuple[Dict[str, List[Dict[str, Any]]], List[str]]:
    """Escribe los campos modificados de vuelta al Sheet. No-op si no hay cambios o si falla."""
    leads = data.get("leads", [])
    if not leads or not spreadsheet_id:
        log.append("Sheet writeback: skipped (no leads or no spreadsheet_id)")
        return data, log

    # Construir el dict de updates: {lead_id_str: {col: value, ...}}
    updates: Dict[str, Dict[str, Any]] = {}
    for row in leads:
        key = str(row.get("lead_id") or row.get("email") or "").strip()
        if not key:
            continue
        updates[key] = {col: row.get(col) for col in WRITEBACK_COLUMNS if col in row}

    if not updates:
        log.append("Sheet writeback: no lead keys found, skipped")
        return data, log

    try:
        from tools.google_sheets_client import batch_update_by_header
        ok = batch_update_by_header(
            spreadsheet_id=spreadsheet_id,
            tab_name="leads",
            key_column="lead_id",
            updates_by_key=updates,
        )
        if ok:
            log.append(
                f"Sheet writeback: updated {len(updates)} leads "
                f"({', '.join(WRITEBACK_COLUMNS)}) → Sheet tab 'leads'"
            )
        else:
            log.append("Sheet writeback: batch_update_by_header returned False (check logs)")
    except Exception as exc:
        _log.warning("Sheet writeback failed (non-fatal): %s", exc)
        log.append(f"Sheet writeback: failed ({exc}), changes only in DB")

    return data, log
