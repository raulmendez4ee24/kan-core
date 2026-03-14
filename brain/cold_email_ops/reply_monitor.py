"""Fase 9: Reply monitor — check for replies (e.g. inbox or webhook), update lead status to replied."""
from __future__ import annotations

import os
from typing import Any, Dict, List, Tuple


def run(
    data: Dict[str, List[Dict[str, Any]]],
    log: List[str],
) -> Tuple[Dict[str, List[Dict[str, Any]]], List[str]]:
    """Placeholder: in full impl would check inbox or webhook for replies and set status=replied. No-op for now."""
    # Optional: call n8n webhook to fetch reply events, then update data['leads'] status
    webhook = os.getenv("COLD_EMAIL_REPLY_WEBHOOK_URL", "").strip()
    if webhook:
        try:
            import httpx
            r = httpx.get(webhook, timeout=10.0)
            if r.is_success:
                replies = r.json() if r.content else []
                if isinstance(replies, list):
                    replied_emails = {e for item in replies for e in [item.get("email") if isinstance(item, dict) else None] if e}
                    leads = data.get("leads", [])
                    for row in leads:
                        if (row.get("email") or "").strip().lower() in replied_emails:
                            row["status"] = "replied"
                    log.append(f"Reply monitor: marked {len(replied_emails)} as replied")
                else:
                    log.append("Reply monitor: webhook returned non-list")
            else:
                log.append("Reply monitor: webhook not available")
        except Exception as e:
            log.append(f"Reply monitor: {e}")
    else:
        log.append("Reply monitor: no COLD_EMAIL_REPLY_WEBHOOK_URL, skip")
    return data, log
