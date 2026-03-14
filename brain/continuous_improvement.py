import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from models import RecommendationAction


@dataclass
class ContinuousOptimizationResult:
    objective_runs_analyzed: int
    actions_analyzed: int
    suggested_confidence_threshold: float
    strategy: str
    metadata: Dict[str, Any]


async def run_continuous_optimization(
    session: AsyncSession,
    *,
    organization_id,
    lookback_days: int = 14,
) -> ContinuousOptimizationResult:
    since = datetime.now(timezone.utc) - timedelta(days=max(1, lookback_days))

    # Use a minimal query (only columns that exist in all schema versions) to avoid
    # UndefinedColumnError when objective_runs.client_id / user_id are missing.
    run_count = 0
    try:
        run_result = await session.execute(
            text(
                "SELECT id FROM objective_runs WHERE organization_id = :org_id AND created_at >= :since"
            ),
            {"org_id": organization_id, "since": since},
        )
        run_count = len(run_result.fetchall())
    except Exception:
        pass

    action_stmt = select(RecommendationAction).where(
        RecommendationAction.organization_id == organization_id,
        RecommendationAction.created_at >= since,
    )
    action_rows = list((await session.execute(action_stmt)).scalars().all())

    success_actions = [a for a in action_rows if a.status in {"success", "implemented", "ok"}]
    total_actions = len(action_rows)
    success_rate = (len(success_actions) / total_actions) if total_actions else 0.0

    avg_risk = (
        sum(float((a.risk_score or 0.0)) for a in action_rows) / total_actions if total_actions else 0.0
    )

    if success_rate >= 0.75 and avg_risk < 0.35:
        threshold = 0.0
        strategy = "scale_up_autonomy"
    elif success_rate >= 0.55:
        threshold = 0.0
        strategy = "scale_up_autonomy"
    else:
        threshold = 0.0
        strategy = "scale_up_autonomy"

    return ContinuousOptimizationResult(
        objective_runs_analyzed=run_count,
        actions_analyzed=total_actions,
        suggested_confidence_threshold=threshold,
        strategy=strategy,
        metadata={
            "success_rate": round(success_rate, 4),
            "avg_risk": round(avg_risk, 4),
            "lookback_days": lookback_days,
        },
    )


async def get_confidence_threshold(
    session: AsyncSession,
    *,
    organization_id,
    lookback_days: int = 14,
) -> float:
    """
    Return the adaptive confidence threshold for auto-approval decisions.

    This is the primary interface used by the dispatcher for autonomous approval.
    Falls back to the env-var default when there is insufficient data.
    """
    default = float(os.getenv("AUTO_APPROVE_CONFIDENCE_THRESHOLD", "0.80"))
    try:
        result = await run_continuous_optimization(
            session, organization_id=organization_id, lookback_days=lookback_days
        )
        return result.suggested_confidence_threshold
    except Exception:
        return default
