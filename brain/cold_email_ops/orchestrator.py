"""Orchestrator: run Phases 0–9, stop on blockers after audit, optional solo_audit."""
from __future__ import annotations

import os
from typing import Any, Dict, List

from schemas.cold_email_ops import ColdEmailOpsRunRequest, ColdEmailOpsRunResponse

from brain.cold_email_ops.audit import run_audit
from brain.cold_email_ops.sheets_schema import validate_all_tabs


def run_cold_email_ops(req: ColdEmailOpsRunRequest) -> ColdEmailOpsRunResponse:
    """Load sheets, run audit; if solo_audit or blockers, return early. Else run phases 2–9."""
    spreadsheet_id = req.spreadsheet_id or os.getenv("COLD_EMAIL_SPREADSHEET_ID", "").strip()
    log: List[str] = []
    resumen: List[str] = []
    bloqueos: List[str] = []
    cola_de_hoy: Dict[str, Any] = {}
    cosas_por_pagar_configurar: List[Dict[str, Any]] = []
    archivos_generados: List[str] = []
    audit_details: Dict[str, Any] | None = None
    tabs_missing: List[Dict[str, Any]] | None = None

    if not spreadsheet_id:
        bloqueos.append("spreadsheet_id missing (request body or COLD_EMAIL_SPREADSHEET_ID)")
        return ColdEmailOpsRunResponse(
            resumen_ejecutivo=resumen,
            bloqueos=bloqueos,
            cola_de_hoy=cola_de_hoy,
            cosas_por_pagar_configurar=cosas_por_pagar_configurar,
            archivos_generados=archivos_generados,
            log_decisiones=log,
            audit=audit_details,
            tabs_missing=tabs_missing,
        )

    # Phase 0: load
    try:
        from tools.google_sheets_client import read_all_tabs
        data = read_all_tabs(spreadsheet_id)
    except Exception as e:
        bloqueos.append(f"Failed to load spreadsheet: {e}")
        return ColdEmailOpsRunResponse(
            resumen_ejecutivo=resumen,
            bloqueos=bloqueos,
            cola_de_hoy=cola_de_hoy,
            cosas_por_pagar_configurar=cosas_por_pagar_configurar,
            archivos_generados=archivos_generados,
            log_decisiones=log,
            audit=audit_details,
            tabs_missing=tabs_missing,
        )

    schema = validate_all_tabs(data)
    if not schema.valid:
        tabs_missing = [
            {"tab": t, "missing_columns": c}
            for t, c in schema.missing_columns.items()
            if c
        ]
        for tab, cols in schema.missing_columns.items():
            if cols:
                bloqueos.append(f"Tab '{tab}' missing columns: {', '.join(cols)}")

    # Phase 1: audit
    audit_result = run_audit(data)
    audit_details = {
        "bloqueos": audit_result.bloqueos,
        "warnings": audit_result.warnings,
        "resumen": audit_result.resumen,
        "details": audit_result.details,
    }
    bloqueos = list(dict.fromkeys(bloqueos + audit_result.bloqueos))
    resumen = list(audit_result.resumen)
    log.append("Phase 0: loaded tabs. Phase 1: audit done.")

    if req.solo_audit or bloqueos:
        return ColdEmailOpsRunResponse(
            resumen_ejecutivo=resumen,
            bloqueos=bloqueos,
            cola_de_hoy=cola_de_hoy,
            cosas_por_pagar_configurar=cosas_por_pagar_configurar,
            archivos_generados=archivos_generados,
            log_decisiones=log,
            audit=audit_details,
            tabs_missing=tabs_missing,
        )

    # Phases 2–9 (delegate to modules)
    data, log, resumen, bloqueos, cola_de_hoy, cosas_por_pagar_configurar, archivos_generados = _run_phases_2_to_9(
        spreadsheet_id=spreadsheet_id,
        data=data,
        log=log,
        resumen=resumen,
        bloqueos=bloqueos,
        cola_de_hoy=cola_de_hoy,
        cosas_por_pagar_configurar=cosas_por_pagar_configurar,
        archivos_generados=archivos_generados,
        export_format=req.export_format,
        include_export=req.include_export,
    )

    return ColdEmailOpsRunResponse(
        resumen_ejecutivo=resumen,
        bloqueos=bloqueos,
        cola_de_hoy=cola_de_hoy,
        cosas_por_pagar_configurar=cosas_por_pagar_configurar,
        archivos_generados=archivos_generados,
        log_decisiones=log,
        audit=audit_details,
        tabs_missing=tabs_missing,
    )


def _run_phases_2_to_9(
    spreadsheet_id: str,
    data: Dict[str, List[Dict[str, Any]]],
    log: List[str],
    resumen: List[str],
    bloqueos: List[str],
    cola_de_hoy: Dict[str, Any],
    cosas_por_pagar_configurar: List[Dict[str, Any]],
    archivos_generados: List[str],
    export_format: str,
    include_export: bool,
):
    """Run lead_cleanup, email_verification, who_to_send, domains_costs, inbox_rotation, copy_gen, export, reply_monitor."""
    from brain.cold_email_ops import lead_cleanup
    from brain.cold_email_ops import email_verification
    from brain.cold_email_ops import who_to_send
    from brain.cold_email_ops import domains_costs
    from brain.cold_email_ops import inbox_rotation
    from brain.cold_email_ops import copy_gen
    from brain.cold_email_ops import sheet_writeback
    from brain.cold_email_ops import export as export_mod
    from brain.cold_email_ops import reply_monitor

    # Phase 2: lead cleanup
    data, log, resumen, archivos_generados = lead_cleanup.run(
        spreadsheet_id, data, log, resumen, archivos_generados
    )
    # Phase 3: email verification
    data, log, resumen = email_verification.run(data, log, resumen)
    # Phase 4: who to send
    data, log, cola_de_hoy = who_to_send.run(data, log, cola_de_hoy)
    # Phase 5 & 6: domains/costs + inbox rotation (inbox_rotation uses cola from cola_de_hoy)
    data["_cola_de_hoy"] = cola_de_hoy.get("leads", [])
    cosas_por_pagar_configurar, log = domains_costs.run(data, cosas_por_pagar_configurar, log)
    data, log = inbox_rotation.run(data, log)
    # Phase 7: copy gen (fills personalization_snippet + subject/body placeholders)
    data, log = copy_gen.run(data, log)
    # Phase 7.5: pre-export writeback — persist queue state + copy to Sheet
    data, log = sheet_writeback.run(spreadsheet_id, data, log)
    # Phase 8: export
    if include_export:
        archivos_generados, log = export_mod.run(data, cola_de_hoy, export_format, archivos_generados, log)
    # Phase 9: reply monitor (check replies, update status)
    data, log = reply_monitor.run(data, log)
    # Phase 9.5: final writeback — persist reply status changes too
    data, log = sheet_writeback.run(spreadsheet_id, data, log)
    return data, log, resumen, bloqueos, cola_de_hoy, cosas_por_pagar_configurar, archivos_generados
