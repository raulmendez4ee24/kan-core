import math
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from brain.global_learning import refresh_global_learnings
from learning.tool_memory import refresh_tool_learning_from_logs
from models import GlobalLearning, OrgPolicy


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(float(value), hi))


def _policy_enabled() -> bool:
    return os.getenv("ENABLE_ENTERPRISE_POLICIES", "false").lower() in {"1", "true", "yes"}


async def refresh_learning_from_sources(session: AsyncSession) -> Dict[str, Any]:
    """Consolidate learning sources (integration_memory -> global_learnings, plus tool learning backfills).

    Safe to run frequently; it is best-effort and will not raise on partial failures.
    """
    result: Dict[str, Any] = {"refreshed": True, "integration_memory_patterns": 0, "pruned": 0}

    try:
        rows = await refresh_global_learnings(session, min_samples=int(os.getenv("GLOBAL_LEARNING_MIN_SAMPLES", "3") or 3))
        result["integration_memory_patterns"] = len(rows)
    except Exception as exc:
        result["integration_memory_error"] = str(exc)

    # Optional pruning of stale, low-signal patterns.
    retention_days = int(os.getenv("LEARNING_RETENTION_DAYS", "180") or 180)
    if retention_days > 0:
        cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, retention_days))
        try:
            stmt = delete(GlobalLearning).where(
                GlobalLearning.updated_at < cutoff,
                GlobalLearning.sample_size <= 1,
            )
            res = await session.execute(stmt)
            await session.commit()
            result["pruned"] = int(getattr(res, "rowcount", 0) or 0)
        except Exception as exc:
            result["prune_error"] = str(exc)

    return result


async def calibrate_org_learning_policy(
    session: AsyncSession,
    *,
    organization_id: UUID,
) -> Dict[str, Any]:
    """Calibrate per-org learning knobs and persist them into OrgPolicy.policy['learning'].

    This only writes if enterprise policies are enabled. Otherwise it returns a no-op response.
    """
    if not _policy_enabled():
        return {"updated": False, "reason": "enterprise_policies_disabled"}

    stmt = select(OrgPolicy).where(OrgPolicy.organization_id == organization_id).limit(1)
    row = (await session.execute(stmt)).scalars().first()
    if not row:
        row = OrgPolicy(organization_id=organization_id, policy={}, updated_by_user_id=None)
        session.add(row)
        await session.commit()
        await session.refresh(row)

    org_id_str = str(organization_id)

    # Backfill tool learning from recent logs so calibrations have data.
    try:
        await refresh_tool_learning_from_logs(session, organization_id=org_id_str, window_days=30, max_samples=200)
    except Exception:
        # Ignore; tool learning is best-effort.
        pass

    # Summarize tool_choice samples for epsilon calibration.
    tool_stmt = select(GlobalLearning).where(
        GlobalLearning.function_name == "tool_choice",
        GlobalLearning.pattern_key.like(f"tool_choice:org:{org_id_str}:%"),
    )
    tool_rows = list((await session.execute(tool_stmt)).scalars().all())
    total_samples = sum(max(int(item.sample_size or 0), 0) for item in tool_rows)
    avg_success = (
        sum(float(item.success_rate or 0.0) for item in tool_rows) / len(tool_rows)
        if tool_rows
        else 0.0
    )

    # Exploration epsilon decreases with evidence. Clamp to keep some exploration.
    epsilon = 0.15 if total_samples < 10 else _clamp(0.12 / math.sqrt(total_samples), 0.02, 0.12)
    if avg_success >= 0.75:
        epsilon = max(0.02, epsilon - 0.02)

    # Tool weights: start from current reasoner defaults.
    tool_weights: Dict[str, float] = {
        "confidence": 0.35,
        "health": 0.2,
        "compatibility": 0.2,
        "speed": 0.15,
        "history": 0.1,
        "cost": 0.1,
        "friction": 0.02,  # full-auto friendly default
    }

    # If success is low, bias toward confidence/health over speed.
    if tool_rows and avg_success < 0.6:
        tool_weights["confidence"] = 0.42
        tool_weights["health"] = 0.22
        tool_weights["speed"] = 0.10

    # If API is expensive recently, weight cost higher.
    api_stmt = select(
        func.avg(GlobalLearning.avg_impact_score),
        func.avg(GlobalLearning.success_rate),
        func.sum(GlobalLearning.sample_size),
    ).where(
        GlobalLearning.function_name == "tool_choice",
        GlobalLearning.pattern_key == f"tool_choice:org:{org_id_str}:api",
    )
    api_row = (await session.execute(api_stmt)).first()
    if api_row:
        _avg_impact, _avg_success, _samples = api_row
        # Heuristic: if API success is low, value reliability even more.
        if _avg_success is not None and float(_avg_success) < 0.55:
            tool_weights["confidence"] = max(tool_weights["confidence"], 0.45)
            tool_weights["compatibility"] = max(tool_weights["compatibility"], 0.22)

    # Persist into policy.
    policy = dict(row.policy or {})
    learning = policy.get("learning") if isinstance(policy.get("learning"), dict) else {}
    learning = dict(learning)
    learning.update(
        {
            "exploration_epsilon": round(float(epsilon), 4),
            "tool_weights": {k: round(float(v), 4) for k, v in tool_weights.items()},
            "calibrated_at": datetime.now(timezone.utc).isoformat(),
            "evidence": {
                "tool_choice_total_samples": int(total_samples),
                "tool_choice_avg_success": round(float(avg_success), 6),
            },
        }
    )
    policy["learning"] = learning
    row.policy = policy
    await session.commit()
    await session.refresh(row)

    return {
        "updated": True,
        "organization_id": org_id_str,
        "learning": learning,
    }

