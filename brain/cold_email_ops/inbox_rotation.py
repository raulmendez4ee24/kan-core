"""Fase 6: Inbox rotation — assign leads to inboxes by rotation_group / daily_limit / sent_today."""
from __future__ import annotations

from typing import Any, Dict, List, Tuple


def run(
    data: Dict[str, List[Dict[str, Any]]],
    log: List[str],
) -> Tuple[Dict[str, List[Dict[str, Any]]], List[str]]:
    """Assign each lead in cola to an inbox (round-robin by capacity). Mutate data['inboxes'] sent_today if needed."""
    inboxes = list(data.get("inboxes", []))
    cola = data.get("_cola_de_hoy") or []
    if not inboxes or not cola:
        log.append("Inbox rotation: no inboxes or no queue")
        return data, log

    # Ensure sent_today numeric
    for i in inboxes:
        i["sent_today"] = _num(i.get("sent_today")) or 0
        i["daily_limit"] = max(0, _num(i.get("daily_limit")) or 0)

    assigned: List[Dict[str, Any]] = []
    deliverable: List[Dict[str, Any]] = []
    idx = 0
    for lead in cola:
        assigned_ok = False
        while True:
            inv = inboxes[idx % len(inboxes)]
            idx += 1
            limit = _num(inv.get("daily_limit")) or 0
            sent = _num(inv.get("sent_today")) or 0
            if limit and sent < limit:
                inv["sent_today"] = sent + 1
                lead["inbox_id"] = inv.get("inbox_id")
                lead["from_email"] = inv.get("from_email")
                assigned.append(
                    {
                        "lead": lead,
                        "inbox_id": inv.get("inbox_id"),
                        "from_email": inv.get("from_email"),
                    }
                )
                deliverable.append(lead)
                assigned_ok = True
                break
            if idx > len(inboxes) * 2:
                previous_status = str(lead.pop("_queued_from_status", "") or "").strip().lower()
                if previous_status:
                    lead["status"] = previous_status
                lead.pop("inbox_id", None)
                lead.pop("from_email", None)
                assigned.append({"lead": lead, "inbox_id": None, "from_email": None})
                break
        if assigned_ok:
            lead.pop("_queued_from_status", None)
    cola[:] = deliverable
    data["_cola_de_hoy"] = cola
    data["_assigned"] = assigned
    log.append(
        f"Inbox rotation: {len(deliverable)} leads assigned to inboxes"
        f" ({len(assigned) - len(deliverable)} left unassigned)"
    )
    return data, log


def _num(v: Any) -> int | None:
    if v is None:
        return None
    if isinstance(v, int):
        return v
    try:
        return int(float(str(v).strip()))
    except (ValueError, TypeError):
        return None
