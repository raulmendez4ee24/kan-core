"""Fase 2: Lead cleanup — normalize, dedupe by email, mark invalid/do_not_contact; write patch to Sheets or patch.csv."""
from __future__ import annotations

import csv
import os
import re
from typing import Any, Dict, List, Set, Tuple


def _normalize_email(s: str | None) -> str | None:
    if not s or not isinstance(s, str):
        return None
    s = s.strip().lower()
    if not s or "@" not in s:
        return None
    return s


def _normalize_status(s: str | None) -> str:
    if not s:
        return "new"
    v = str(s).strip().lower()
    allowed = {"new", "queued", "sent", "replied", "bounced", "invalid", "do_not_contact"}
    return v if v in allowed else "new"


def run(
    spreadsheet_id: str,
    data: Dict[str, List[Dict[str, Any]]],
    log: List[str],
    resumen: List[str],
    archivos_generados: List[str],
) -> Tuple[Dict[str, List[Dict[str, Any]]], List[str], List[str], List[str]]:
    """Normalize leads, dedupe by email (keep first), set status invalid/do_not_contact; optionally write to Sheets or patch.csv."""
    leads_raw = data.get("leads", [])
    if not leads_raw:
        return data, log, resumen, archivos_generados

    seen_emails: Set[str] = set()
    cleaned: List[Dict[str, Any]] = []
    updates_by_lead_id: Dict[str, Dict[str, Any]] = {}
    key_col = "lead_id"
    has_lead_id = bool(leads_raw and leads_raw[0] and key_col in leads_raw[0])

    for i, row in enumerate(leads_raw):
        d = dict(row)
        email = _normalize_email(d.get("email"))
        if not email:
            d["status"] = _normalize_status(d.get("status"))
            if d.get("status") != "do_not_contact":
                d["status"] = "invalid"
            cleaned.append(d)
            continue
        if email in seen_emails:
            d["status"] = "invalid"
            d["notes"] = (str(d.get("notes") or "") + " [duplicate email]").strip()
            cleaned.append(d)
            continue
        seen_emails.add(email)
        d["email"] = email
        d["status"] = _normalize_status(d.get("status"))
        cleaned.append(d)

    data = {**data, "leads": cleaned}
    log.append(f"Lead cleanup: {len(cleaned)} rows, {len(seen_emails)} unique emails")
    resumen.append(f"Leads after cleanup: {len(cleaned)}")

    # Optional: write back to Sheets (batch update status/notes) or export patch
    write_sheets = os.getenv("COLD_EMAIL_WRITE_SHEETS", "").strip().lower() in ("1", "true", "yes")
    if write_sheets and has_lead_id:
        try:
            from tools.google_sheets_client import batch_update_by_header
            for r in cleaned:
                lid = r.get("lead_id")
                if not lid:
                    continue
                updates_by_lead_id[str(lid)] = {"status": r.get("status", ""), "notes": r.get("notes", "")[:500]}
            if updates_by_lead_id:
                batch_update_by_header(spreadsheet_id, "leads", "lead_id", updates_by_lead_id)
                log.append("Lead status/notes written to Sheets")
        except Exception as e:
            log.append(f"Sheets write skipped: {e}")
    else:
        patch_path = "cold_email_patch.csv"
        try:
            if cleaned:
                headers = list(cleaned[0].keys()) if cleaned else []
                with open(patch_path, "w", newline="", encoding="utf-8") as f:
                    w = csv.DictWriter(f, fieldnames=headers or ["email", "status", "notes"], extrasaction="ignore")
                    w.writeheader()
                    w.writerows(cleaned)
                archivos_generados.append(patch_path)
                log.append(f"Patch written to {patch_path}")
        except Exception as e:
            log.append(f"Patch file write failed: {e}")

    return data, log, resumen, archivos_generados
