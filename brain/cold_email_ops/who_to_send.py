"""Fase 4: Who to send — select first touches + follow-ups and build a real queue."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

CE_META_RE = re.compile(r"\[ce_meta[^\]]*step=(\d+)/(\d+)[^\]]*\]", re.IGNORECASE)


def run(
    data: Dict[str, List[Dict[str, Any]]],
    log: List[str],
    cola_de_hoy: Dict[str, Any],
) -> Tuple[Dict[str, List[Dict[str, Any]]], List[str], Dict[str, Any]]:
    """Build queue using campaign strategy: segment, sequence step, spacing, and daily cap."""
    leads = data.get("leads", [])
    campaign = data.get("campaign", [])
    inboxes = data.get("inboxes", [])
    cfg = campaign[0] if campaign else {}

    max_per_day = 50
    v = cfg.get("max_new_leads_per_day")
    if v is not None:
        max_per_day = _parse_int(v, default=max_per_day)
    total_cap = sum(max(0, _parse_int(r.get("daily_limit"), default=0)) for r in inboxes)
    if total_cap and total_cap < max_per_day:
        max_per_day = total_cap

    allowed_validation = {"valid", "risky"}
    target_segments = _parse_target_segments(cfg.get("target_segment"))
    sequence_total = max(1, _parse_int(cfg.get("sequence"), default=1))
    spacing_days = max(0, _parse_int(cfg.get("spacing_days"), default=0))
    campaign_id = str(cfg.get("campaign_id") or "").strip()
    send_window = str(cfg.get("send_window_localtime") or "").strip()

    eligible: List[Tuple[Dict[str, Any], int, str]] = []
    for row in leads:
        status = str(row.get("status") or "").strip().lower()
        validation = str(row.get("email_validation_status") or "").strip().lower()
        if validation not in allowed_validation:
            continue
        if not _matches_target_segment(row, target_segments):
            continue

        next_step = _next_sequence_step(row, status, sequence_total, spacing_days)
        if next_step is None:
            continue
        eligible.append((row, next_step, status))

    selected = eligible[:max_per_day]
    queued: List[Dict[str, Any]] = []
    for row, next_step, previous_status in selected:
        row["_queued_from_status"] = previous_status
        row["status"] = "queued"
        row["sequence_step"] = next_step
        row["sequence_total"] = sequence_total
        row["notes"] = _with_campaign_meta(
            row.get("notes"),
            step=next_step,
            total=sequence_total,
            campaign_id=campaign_id,
            send_window=send_window,
        )
        if campaign_id:
            row["campaign_id"] = campaign_id
        if send_window:
            row["send_window_localtime"] = send_window
        queued.append(row)

    cola_de_hoy["leads"] = queued
    cola_de_hoy["max_per_day"] = max_per_day
    cola_de_hoy["total_eligible"] = len(eligible)
    cola_de_hoy["strategy"] = {
        "campaign_id": campaign_id or None,
        "target_segments": sorted(target_segments),
        "sequence_total": sequence_total,
        "spacing_days": spacing_days,
        "send_window_localtime": send_window or None,
    }
    log.append(
        "Who-to-send: "
        f"{len(queued)} leads in queue "
        f"(eligible={len(eligible)}, max_per_day={max_per_day}, "
        f"sequence_total={sequence_total}, spacing_days={spacing_days})"
    )
    return data, log, cola_de_hoy


def _parse_int(value: Any, default: int = 0) -> int:
    if value is None:
        return default
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if not text:
        return default
    try:
        return int(float(text))
    except (ValueError, TypeError):
        match = re.search(r"(\d+)", text)
        if match:
            return int(match.group(1))
    return default


def _parse_target_segments(value: Any) -> Set[str]:
    text = str(value or "").strip().lower()
    if not text:
        return set()
    return {
        item.strip()
        for item in re.split(r"[,;|/]+", text)
        if item.strip()
    }


def _matches_target_segment(row: Dict[str, Any], target_segments: Set[str]) -> bool:
    if not target_segments:
        return True
    segment = str(row.get("segment") or "").strip().lower()
    return segment in target_segments


def _next_sequence_step(
    row: Dict[str, Any],
    status: str,
    sequence_total: int,
    spacing_days: int,
) -> Optional[int]:
    if status == "new":
        return 1
    if status != "sent":
        return None

    last_step = _extract_last_step(row.get("notes")) or 1
    if last_step >= sequence_total:
        return None
    if not _is_follow_up_due(row.get("last_contacted_at"), spacing_days):
        return None
    return last_step + 1


def _extract_last_step(notes: Any) -> Optional[int]:
    text = str(notes or "")
    match = CE_META_RE.search(text)
    if not match:
        return None
    try:
        return int(match.group(1))
    except (ValueError, TypeError):
        return None


def _is_follow_up_due(last_contacted_at: Any, spacing_days: int) -> bool:
    if spacing_days <= 0:
        return True
    dt = _parse_timestamp(last_contacted_at)
    if dt is None:
        return False
    return datetime.now(timezone.utc) >= dt + _days_delta(spacing_days)


def _days_delta(days: int):
    from datetime import timedelta
    return timedelta(days=days)


def _parse_timestamp(value: Any) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    normalized = text.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(normalized)
    except ValueError:
        try:
            dt = datetime.strptime(text, "%Y-%m-%d")
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _with_campaign_meta(
    notes: Any,
    step: int,
    total: int,
    campaign_id: str,
    send_window: str,
) -> str:
    base = re.sub(r"\s*\[ce_meta[^\]]*\]\s*$", "", str(notes or "").strip(), flags=re.IGNORECASE)
    parts = [f"step={step}/{total}"]
    if campaign_id:
        parts.append(f"campaign={campaign_id}")
    if send_window:
        parts.append(f"window={send_window}")
    marker = "[ce_meta " + " ".join(parts) + "]"
    return f"{base} {marker}".strip()
