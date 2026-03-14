from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional


def _get_croniter():
    try:
        from croniter import croniter  # type: ignore
    except Exception as exc:  # pragma: no cover
        raise RuntimeError("croniter is required for task scheduling") from exc
    return croniter


def _tz(tz_name: str):
    try:
        from zoneinfo import ZoneInfo
    except Exception as exc:  # pragma: no cover
        raise RuntimeError("zoneinfo is required") from exc
    name = str(tz_name or "UTC").strip() or "UTC"
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo("UTC")


@dataclass(frozen=True)
class CronDue:
    scheduled_for_utc: datetime
    scheduled_for_local: datetime


def validate_cron(expr: str) -> None:
    token = str(expr or "").strip()
    if not token:
        raise ValueError("cron is empty")
    croniter = _get_croniter()
    # croniter throws if invalid.
    _ = croniter(token, datetime.now(timezone.utc))


def last_scheduled_time(
    *,
    cron: str,
    timezone_name: str,
    now_utc: Optional[datetime] = None,
) -> CronDue:
    """Return the most recent scheduled datetime (<= now) in both local and UTC."""
    croniter = _get_croniter()
    now = now_utc or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    tz = _tz(timezone_name)
    local_now = now.astimezone(tz)
    itr = croniter(str(cron).strip(), local_now)
    prev_local = itr.get_prev(datetime)
    if prev_local.tzinfo is None:
        prev_local = prev_local.replace(tzinfo=tz)
    prev_utc = prev_local.astimezone(timezone.utc)
    return CronDue(scheduled_for_utc=prev_utc, scheduled_for_local=prev_local)

