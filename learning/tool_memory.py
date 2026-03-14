import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import DesktopCommand, DesktopCommandResult, GlobalLearning, IntegrationRunLog

_INTEGRATION_SUCCESS_STATUSES = {"integrated", "saved_without_workflow", "already_covered"}


def _enabled() -> bool:
    return os.getenv("ENABLE_TOOL_LEARNING", "true").lower() in {"1", "true", "yes"}


def _clamp01(value: float) -> float:
    return max(0.0, min(float(value), 1.0))


def _impact_score(*, success: bool, duration_ms: Optional[int], cost_usd: Optional[float]) -> float:
    # Simple proxy: success dominates, then speed. Cost penalizes lightly.
    duration = 0 if duration_ms is None else max(0, min(int(duration_ms), 120000))
    speed = max(0.0, 1.0 - (duration / 30000.0))
    cost = 0.0 if cost_usd is None else max(0.0, float(cost_usd))
    cost_penalty = min(cost / 1.0, 2.0) * 0.08
    base = 0.8 if success else 0.0
    return _clamp01(base + (0.2 * speed) - cost_penalty) * 100.0


async def _upsert_tool_pattern(
    session: AsyncSession,
    *,
    pattern_key: str,
    organization_id: str,
    tool: str,
    success: bool,
    duration_ms: Optional[int],
    cost_usd: Optional[float],
    extra: Optional[Dict[str, Any]],
) -> None:
    stmt = select(GlobalLearning).where(GlobalLearning.pattern_key == pattern_key).limit(1)
    row = (await session.execute(stmt)).scalars().first()
    now = datetime.now(timezone.utc)
    success_value = 1.0 if success else 0.0
    impact = _impact_score(success=success, duration_ms=duration_ms, cost_usd=cost_usd)

    if row:
        prev_samples = int(row.sample_size or 0)
        new_samples = prev_samples + 1
        prev_success = float(row.success_rate or 0.0)
        prev_impact = float(row.avg_impact_score or 0.0)
        row.sample_size = new_samples
        row.success_rate = ((prev_success * prev_samples) + success_value) / new_samples
        row.avg_impact_score = ((prev_impact * prev_samples) + impact) / new_samples

        meta = dict(row.learning_metadata or {})
        meta.update(
            {
                "scope": "org:" + organization_id,
                "organization_id": organization_id,
                "tool": tool,
                "updated_at": now.isoformat(),
                "last_result": {
                    "at": now.isoformat(),
                    "success": success,
                    "duration_ms": duration_ms,
                    "cost_usd": cost_usd,
                },
                "extra": extra or {},
            }
        )
        row.learning_metadata = meta
        return

    row = GlobalLearning(
        pattern_key=pattern_key,
        industry="general",
        function_name="tool_choice",
        provider_id=tool,
        sample_size=1,
        success_rate=success_value,
        avg_impact_score=impact,
        learning_metadata={
            "scope": "org:" + organization_id,
            "organization_id": organization_id,
            "tool": tool,
            "created_at": now.isoformat(),
            "extra": extra or {},
        },
    )
    session.add(row)


async def record_tool_outcome(
    session: AsyncSession,
    *,
    organization_id: str,
    tool: str,
    success: bool,
    duration_ms: Optional[int] = None,
    cost_usd: Optional[float] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    if not _enabled():
        return
    tool_token = str(tool or "").strip().lower()
    if tool_token not in {"api", "browser", "desktop"}:
        return
    pattern_key = f"tool_choice:org:{organization_id}:{tool_token}"
    await _upsert_tool_pattern(
        session,
        pattern_key=pattern_key,
        organization_id=organization_id,
        tool=tool_token,
        success=bool(success),
        duration_ms=duration_ms,
        cost_usd=cost_usd,
        extra=extra,
    )
    await session.commit()


async def refresh_tool_learning_from_logs(
    session: AsyncSession,
    *,
    organization_id: str,
    window_days: int = 30,
    max_samples: int = 250,
) -> Dict[str, Any]:
    """Backfill tool_choice patterns from recent logs (best-effort)."""
    if not _enabled():
        return {"updated": 0}

    try:
        org_uuid = __import__("uuid").UUID(str(organization_id))
    except Exception:
        return {"updated": 0, "reason": "invalid_org_id"}

    since = datetime.now(timezone.utc) - timedelta(days=max(1, int(window_days)))

    # API tool: integration run logs.
    run_stmt = (
        select(IntegrationRunLog)
        .where(
            IntegrationRunLog.client_id == org_uuid,
            IntegrationRunLog.created_at >= since,
        )
        .order_by(IntegrationRunLog.created_at.desc())
        .limit(max(1, int(max_samples)))
    )
    runs = list((await session.execute(run_stmt)).scalars().all())
    for row in runs:
        success = str(row.status or "") in _INTEGRATION_SUCCESS_STATUSES
        await _upsert_tool_pattern(
            session,
            pattern_key=f"tool_choice:org:{organization_id}:api",
            organization_id=organization_id,
            tool="api",
            success=success,
            duration_ms=int(row.duration_ms) if row.duration_ms is not None else None,
            cost_usd=float(row.cost_usd) if row.cost_usd is not None else None,
            extra={"source": "integration_run_logs"},
        )

    # Desktop/browser tool: desktop command results.
    res_stmt = (
        select(DesktopCommandResult, DesktopCommand)
        .join(DesktopCommand, DesktopCommand.id == DesktopCommandResult.command_id)
        .where(
            DesktopCommandResult.organization_id == org_uuid,
            DesktopCommandResult.received_at >= since,
        )
        .order_by(DesktopCommandResult.received_at.desc())
        .limit(max(1, int(max_samples)))
    )
    rows = list((await session.execute(res_stmt)).all())
    for result, command in rows:
        action = str(getattr(command, "action", "") or "")
        tool = "browser" if action.startswith("browser_") else "desktop"
        success = str(getattr(result, "status", "") or "").lower() == "success"
        await _upsert_tool_pattern(
            session,
            pattern_key=f"tool_choice:org:{organization_id}:{tool}",
            organization_id=organization_id,
            tool=tool,
            success=success,
            duration_ms=int(getattr(result, "duration_ms", 0)) if getattr(result, "duration_ms", None) is not None else None,
            cost_usd=None,
            extra={"source": "desktop_command_results"},
        )

    await session.commit()
    return {"updated": 1, "window_days": int(window_days)}

