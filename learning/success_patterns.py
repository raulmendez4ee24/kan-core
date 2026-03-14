import hashlib
import json
import os
from typing import Any, Dict, List, Literal, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import GlobalLearning

from learning.strategy_memory import build_ui_context

RecoveryStrategy = Literal[
    "adjust_coordinates",
    "scroll_and_retry",
    "search_again",
    "wait_and_retry",
    "fallback_click_center",
    "retry_with_alternate_selector",
]


_DEFAULT_RECOVERY_ORDER: List[RecoveryStrategy] = [
    "search_again",
    "adjust_coordinates",
    "retry_with_alternate_selector",
    "scroll_and_retry",
    "wait_and_retry",
    "fallback_click_center",
]


def _enabled() -> bool:
    return os.getenv("ENABLE_STRATEGY_LEARNING", "true").lower() in {"1", "true", "yes"}


def _normalize_candidates(strategies: Sequence[str]) -> List[RecoveryStrategy]:
    allowed: set[str] = set(_DEFAULT_RECOVERY_ORDER)
    ordered: List[RecoveryStrategy] = []
    for item in strategies:
        token = str(item).strip()
        if token in allowed and token not in ordered:
            ordered.append(token)  # type: ignore[arg-type]
    if ordered:
        return ordered
    return list(_DEFAULT_RECOVERY_ORDER)


def _score_pattern(
    *,
    row: GlobalLearning,
    strategy: str,
    organization_id: str,
    industry: str,
    ui_signature: str,
) -> float:
    if str(row.provider_id or "") != strategy:
        return -1.0

    score = (float(row.success_rate or 0.0) * 0.65) + (
        min(int(row.sample_size or 0), 50) / 50.0 * 0.2
    ) + (float(row.avg_impact_score or 0.0) / 100.0 * 0.15)

    if str(row.industry or "") == industry:
        score += 0.1

    metadata = dict(row.learning_metadata or {})
    if str(metadata.get("ui_signature") or "") == ui_signature:
        score += 0.2

    scope = str(metadata.get("scope") or "")
    if scope.startswith("org:") and str(metadata.get("organization_id") or "") == organization_id:
        score += 0.2
    return score


async def prioritize_recovery_strategies(
    session: AsyncSession,
    *,
    organization_id: str,
    industry: str,
    action: str,
    args: Dict[str, Any],
    metadata: Dict[str, Any],
    candidate_strategies: Sequence[str],
) -> List[RecoveryStrategy]:
    candidates = _normalize_candidates(candidate_strategies)
    if not _enabled():
        return candidates

    action_token = str(action or "").strip().lower()[:120] or "unknown_action"
    industry_token = str(industry or "").strip().lower()[:120] or "general"
    function_name = f"desktop_recovery:{action_token}"
    ui_context = build_ui_context(action=action_token, args=args, metadata=metadata)
    ui_signature = ""
    if isinstance(ui_context, dict):
        ui_signature = str(ui_context.get("ui_signature") or "")
    if not ui_signature:
        ui_signature = hashlib.sha256(
            json.dumps(ui_context, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()[:16]

    stmt = select(GlobalLearning).where(
        GlobalLearning.function_name == function_name,
        GlobalLearning.provider_id.in_(list(candidates)),
        GlobalLearning.industry.in_([industry_token, "general"]),
    )
    rows = list((await session.execute(stmt)).scalars().all())
    if not rows:
        return candidates

    scored: List[tuple[float, int, RecoveryStrategy]] = []
    for index, strategy in enumerate(candidates):
        best = -1.0
        for row in rows:
            best = max(
                best,
                _score_pattern(
                    row=row,
                    strategy=strategy,
                    organization_id=organization_id,
                    industry=industry_token,
                    ui_signature=ui_signature,
                ),
            )
        scored.append((best, -index, strategy))

    scored.sort(reverse=True)
    ordered: List[RecoveryStrategy] = [item[2] for item in scored]
    return ordered
