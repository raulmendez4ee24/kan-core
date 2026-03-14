"""Fase 1: Audit — bloqueos (tabs/columns missing, no inboxes, no campaign), warnings, resumen."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

from brain.cold_email_ops.sheets_schema import EXPECTED_COLUMNS, validate_all_tabs


@dataclass
class AuditResult:
    bloqueos: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    resumen: List[str] = field(default_factory=list)
    details: Dict[str, Any] = field(default_factory=dict)


def run_audit(data: Dict[str, List[Dict[str, Any]]]) -> AuditResult:
    """Run audit on loaded sheet data. data keys: leads, inboxes, domains, campaign, costs."""
    out = AuditResult()

    # Schema validation
    schema = validate_all_tabs(data)
    if not schema.valid:
        for tab, cols in schema.missing_columns.items():
            if cols:
                out.bloqueos.append(f"Tab '{tab}' missing columns: {', '.join(cols)}")
    for tab, cols in schema.extra_columns.items():
        if cols:
            out.warnings.append(f"Tab '{tab}' has extra columns: {', '.join(cols)}")

    # Tab existence (empty = missing tab for our purposes)
    for tab in ["leads", "inboxes", "domains", "campaign", "costs"]:
        rows = data.get(tab, [])
        if not rows:
            out.bloqueos.append(f"Tab '{tab}' is missing or empty")
        else:
            out.resumen.append(f"Tab '{tab}': {len(rows)} rows")

    # At least one inbox with daily_limit > 0
    inboxes = data.get("inboxes", [])
    if schema.valid and inboxes:
        with_limit = [r for r in inboxes if _num(r.get("daily_limit")) and _num(r.get("daily_limit")) > 0]
        if not with_limit:
            out.bloqueos.append("No inbox has daily_limit > 0")
    elif schema.valid and not inboxes:
        out.bloqueos.append("No inboxes configured")

    # At least one campaign row
    campaign = data.get("campaign", [])
    if schema.valid and not campaign:
        out.bloqueos.append("No campaign configuration (campaign tab empty)")

    # Leads count
    leads = data.get("leads", [])
    if leads:
        out.resumen.append(f"Total leads: {len(leads)}")
    out.details["schema_valid"] = schema.valid
    out.details["leads_count"] = len(leads)
    out.details["inboxes_count"] = len(inboxes)
    out.details["campaign_count"] = len(campaign)
    return out


def _num(v: Any) -> int | None:
    if v is None:
        return None
    if isinstance(v, int):
        return v
    try:
        return int(float(str(v).strip()))
    except (ValueError, TypeError):
        return None
