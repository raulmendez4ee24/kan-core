import hashlib
import json
import os
import random
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from brain.tool_reasoner import rank_tools as _rank_tools_reasoner
from learning.strategy_memory import build_ui_context
from learning.success_patterns import prioritize_recovery_strategies
from models import GlobalLearning


def _enabled() -> bool:
    return os.getenv("ENABLE_STRATEGY_LEARNING", "true").lower() in {"1", "true", "yes"}


def _normalize_text(value: Any, default: str) -> str:
    token = str(value or "").strip().lower()
    return token[:120] if token else default


def _safe_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _deterministic_rng(seed: str) -> random.Random:
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]
    return random.Random(int(digest, 16))


def _exploration_epsilon(metadata: Dict[str, Any]) -> float:
    value = metadata.get("learning_exploration_epsilon")
    if isinstance(value, (int, float)):
        return max(0.0, min(float(value), 1.0))
    raw = os.getenv("LEARNING_EXPLORATION_EPSILON", "0.0")
    try:
        env_value = float(raw)
    except ValueError:
        env_value = 0.0
    return max(0.0, min(env_value, 1.0))


def _extract_recent_outcomes(metadata: Dict[str, Any]) -> List[Dict[str, Any]]:
    outcomes = metadata.get("recent_outcomes")
    if isinstance(outcomes, list):
        return [item for item in outcomes if isinstance(item, dict)]
    return []


def _beta_params_from_row(row: GlobalLearning) -> tuple[float, float]:
    sample_size = max(0, int(row.sample_size or 0))
    success_rate = max(0.0, min(float(row.success_rate or 0.0), 1.0))
    success = success_rate * sample_size
    failure = max(sample_size - success, 0.0)
    # Beta(1,1) prior (uniform).
    return 1.0 + success, 1.0 + failure


def _weight_row(
    *,
    row: GlobalLearning,
    organization_id: str,
    industry: str,
    ui_signature: str,
) -> float:
    meta = dict(row.learning_metadata or {})
    weight = 1.0
    if str(row.industry or "") == industry:
        weight += 0.1
    if str(meta.get("ui_signature") or "") == ui_signature:
        weight += 0.4
    scope = str(meta.get("scope") or "")
    if scope.startswith("org:") and str(meta.get("organization_id") or "") == organization_id:
        weight += 0.3
    return weight


async def rank_recovery_strategies(
    session: AsyncSession,
    *,
    organization_id: str,
    industry: str,
    action: str,
    args: Dict[str, Any],
    metadata: Dict[str, Any],
    candidates: Sequence[str],
) -> List[str]:
    """Return an ordered list of recovery strategies.

    Uses:
      1) existing heuristic prioritization (prior),
      2) bandit-style ranking (Beta-Binomial) from GlobalLearning,
      3) deterministic exploration epsilon.
    """
    normalized_candidates: List[str] = []
    for item in candidates:
        token = str(item or "").strip()
        if token and token not in normalized_candidates:
            normalized_candidates.append(token)
    if not normalized_candidates:
        return []

    prior = list(
        await prioritize_recovery_strategies(
            session,
            organization_id=str(organization_id),
            industry=str(industry or "general"),
            action=str(action or ""),
            args=dict(args or {}),
            metadata=dict(metadata or {}),
            candidate_strategies=normalized_candidates,
        )
    )
    if not _enabled():
        return prior

    action_token = _normalize_text(action, "unknown_action")
    industry_token = _normalize_text(industry, "general")
    ui_context = build_ui_context(action=action_token, args=dict(args or {}), metadata=dict(metadata or {}))
    ui_signature = str(ui_context.get("ui_signature") or "")
    if not ui_signature:
        ui_signature = hashlib.sha256(
            json.dumps(ui_context, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()[:16]

    stmt = select(GlobalLearning).where(
        GlobalLearning.function_name == f"desktop_recovery:{action_token}",
        GlobalLearning.provider_id.in_(prior),
        GlobalLearning.industry.in_([industry_token, "general"]),
    )
    rows = list((await session.execute(stmt)).scalars().all())
    if not rows:
        return prior

    # Aggregate per-strategy with contextual weights.
    by_strategy: Dict[str, Dict[str, float]] = {s: {"alpha": 1.0, "beta": 1.0, "penalty": 0.0} for s in prior}
    for row in rows:
        strategy = str(row.provider_id or "")
        if strategy not in by_strategy:
            continue
        alpha, beta = _beta_params_from_row(row)
        w = _weight_row(row=row, organization_id=str(organization_id), industry=industry_token, ui_signature=ui_signature)
        by_strategy[strategy]["alpha"] += (alpha - 1.0) * w
        by_strategy[strategy]["beta"] += (beta - 1.0) * w

        meta = dict(row.learning_metadata or {})
        recent = _extract_recent_outcomes(meta)
        if recent:
            last = recent[-6:]
            failures = sum(1 for item in last if item.get("success") is False)
            successes = sum(1 for item in last if item.get("success") is True)
            if failures > successes:
                by_strategy[strategy]["penalty"] += 0.08

    day_bucket = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    rng = _deterministic_rng(f"recovery_ranker:{organization_id}:{action_token}:{ui_signature}:{day_bucket}")

    scored: List[tuple[float, int, str]] = []
    for idx, strategy in enumerate(prior):
        params = by_strategy.get(strategy) or {"alpha": 1.0, "beta": 1.0, "penalty": 0.0}
        alpha = max(0.1, float(params.get("alpha") or 1.0))
        beta = max(0.1, float(params.get("beta") or 1.0))
        penalty = float(params.get("penalty") or 0.0)
        sample = rng.betavariate(alpha, beta)
        # Prior order as a weak tie-breaker.
        prior_bonus = (len(prior) - idx) / max(len(prior), 1) * 0.03
        score = float(sample) + float(prior_bonus) - float(penalty)
        scored.append((score, -idx, strategy))

    scored.sort(reverse=True)
    ordered = [item[2] for item in scored]

    epsilon = _exploration_epsilon(dict(metadata or {}))
    if epsilon > 0.0 and rng.random() < epsilon and len(ordered) >= 3:
        # Deterministic exploration: swap leader with a top-k alternative.
        k = min(3, len(ordered))
        j = rng.randrange(1, k)
        ordered[0], ordered[j] = ordered[j], ordered[0]

    return ordered


async def rank_tools(
    session: AsyncSession,
    *,
    organization_id: str,
    goal_context: Dict[str, Any],
    candidates: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Re-rank tool candidates (api|browser|desktop) with learning bias."""
    if not candidates:
        return []

    max_cost = _safe_float(goal_context.get("max_cost"), 1.0)
    weights = goal_context.get("weights") if isinstance(goal_context.get("weights"), dict) else {}
    reasoner_ctx = {"max_cost": max_cost, "weights": dict(weights)}
    ranked = _rank_tools_reasoner(candidates=candidates, context=reasoner_ctx)
    if not _enabled():
        return ranked

    tool_names = [str(item.get("tool") or "") for item in ranked if str(item.get("tool") or "").strip()]
    if not tool_names:
        return ranked

    stmt = select(GlobalLearning).where(
        GlobalLearning.function_name == "tool_choice",
        GlobalLearning.provider_id.in_(tool_names),
    )
    rows = list((await session.execute(stmt)).scalars().all())

    bias: Dict[str, float] = {}
    for row in rows:
        tool = str(row.provider_id or "")
        if not tool:
            continue
        meta = dict(row.learning_metadata or {})
        scope = str(meta.get("scope") or "")
        org_match = scope.startswith("org:") and str(meta.get("organization_id") or "") == str(organization_id)
        # Bias value: [-0.15..+0.15] roughly.
        base = (float(row.success_rate or 0.0) * 0.7) + (float(row.avg_impact_score or 0.0) / 100.0 * 0.3)
        sample = min(max(int(row.sample_size or 0), 0), 50) / 50.0
        delta = (base - 0.5) * 0.25 * (0.3 + 0.7 * sample)
        if org_match:
            delta *= 1.35
        bias[tool] = bias.get(tool, 0.0) + float(delta)

    day_bucket = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    rng = _deterministic_rng(f"tool_ranker:{organization_id}:{day_bucket}")
    env_epsilon = _safe_float(os.getenv("LEARNING_EXPLORATION_EPSILON", "0.0"), 0.0)
    epsilon = _safe_float(goal_context.get("exploration_epsilon"), env_epsilon)
    epsilon = max(0.0, min(epsilon, 1.0))

    enriched: List[Dict[str, Any]] = []
    for item in ranked:
        tool = str(item.get("tool") or "")
        base_score = float(item.get("reasoner_score") or 0.0)
        learned_delta = float(bias.get(tool, 0.0))
        learned_score = base_score + learned_delta
        enriched.append(
            {
                **item,
                "learned_score": round(float(learned_score), 6),
                "learned_reason": (
                    f"bias={learned_delta:+.4f} from GlobalLearning(tool_choice)"
                    if learned_delta
                    else "no learning bias yet"
                ),
            }
        )

    enriched.sort(key=lambda x: float(x.get("learned_score") or 0.0), reverse=True)

    if epsilon > 0.0 and rng.random() < epsilon and len(enriched) >= 2:
        # Deterministic exploration: flip top-2 sometimes.
        enriched[0], enriched[1] = enriched[1], enriched[0]

    return enriched
