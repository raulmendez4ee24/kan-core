import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from brain.tool_reasoner import choose_tool, rank_tools
from brain.policy_engine import load_effective_policy
from models import (
    ApiIntegration,
    DesktopAgent,
    DesktopCommand,
    DesktopCommandResult,
    IntegrationRunLog,
)


from config.env_helpers import env_bool

_INTEGRATION_SUCCESS_STATUSES = {"integrated", "saved_without_workflow", "already_covered"}


def _clamp01(value: float) -> float:
    return max(0.0, min(float(value), 1.0))


def _bayes_success_prob(*, success: int, total: int, prior: float, weight: int = 10) -> float:
    # Smooth small samples so we don't overfit early history.
    denom = max(total + max(weight, 1), 1)
    return _clamp01((success + (prior * max(weight, 1))) / denom)


def _speed_score_from_duration_ms(*, avg_duration_ms: Optional[float], default: float) -> float:
    if avg_duration_ms is None:
        return _clamp01(default)
    # Normalize: 0ms -> 1.0, 30s+ -> 0.0
    return _clamp01(1.0 - (float(avg_duration_ms) / 30000.0))


def _safe_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> Optional[int]:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _browser_allowlist() -> List[str]:
    raw = os.getenv("DESKTOP_AGENT_BROWSER_DOMAIN_ALLOWLIST_JSON", "[]")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    out: List[str] = []
    for item in data:
        token = str(item).strip().lower()
        if token and token not in out:
            out.append(token)
    return out


def _n8n_available() -> bool:
    # Chat dispatch uses webhook; backend workflow generation uses API url+key.
    if os.getenv("N8N_WEBHOOK_URL"):
        return True
    return bool(os.getenv("N8N_API_URL") and os.getenv("N8N_API_KEY"))


@dataclass(frozen=True)
class ToolCandidateSnapshot:
    tool: str  # api|browser|desktop
    available: bool
    score: float
    success_prob: float
    avg_duration_ms: Optional[float]
    avg_cost_usd: Optional[float]
    sample_size: int
    reasoning: str
    evidence: Dict[str, Any]


async def build_tool_selection_snapshot(
    session: AsyncSession,
    *,
    organization_id: UUID,
    window_days: int = 30,
    max_samples: int = 200,
) -> Dict[str, Any]:
    """Build an org-level execution-tool snapshot for meta-reasoning.

    The agent (LLM) still decides which tool to use per task, but this snapshot provides:
      - cost / speed estimates,
      - historical success rates,
      - availability signals.
    """
    since = datetime.now(timezone.utc) - timedelta(days=max(1, int(window_days)))

    desktop_feature_on = env_bool("ENABLE_DESKTOP_AGENT", "false")
    desktop_allowlist = _browser_allowlist()
    api_available = _n8n_available()

    # Availability signals.
    online_agents_stmt = select(DesktopAgent).where(
        DesktopAgent.organization_id == organization_id,
        DesktopAgent.status == "online",
    )
    online_agents = list((await session.execute(online_agents_stmt)).scalars().all())
    online_agent_count = len(online_agents)

    desktop_available = bool(desktop_feature_on and online_agent_count > 0)
    browser_available = bool(desktop_available and len(desktop_allowlist) > 0)

    # Integration history (API tool).
    integration_stmt = (
        select(IntegrationRunLog)
        .where(
            IntegrationRunLog.client_id == organization_id,
            IntegrationRunLog.created_at >= since,
        )
        .order_by(desc(IntegrationRunLog.created_at))
        .limit(max(1, int(max_samples)))
    )
    integration_rows = list((await session.execute(integration_stmt)).scalars().all())
    api_total = len(integration_rows)
    api_success = sum(1 for row in integration_rows if row.status in _INTEGRATION_SUCCESS_STATUSES)
    api_avg_duration_ms: Optional[float] = None
    api_avg_cost_usd: Optional[float] = None
    if api_total:
        durations = [int(row.duration_ms) for row in integration_rows if _safe_int(row.duration_ms) is not None]
        costs = [float(row.cost_usd) for row in integration_rows if _safe_float(row.cost_usd) is not None]
        if durations:
            api_avg_duration_ms = sum(durations) / max(len(durations), 1)
        if costs:
            api_avg_cost_usd = sum(costs) / max(len(costs), 1)

    # Desktop/browser history (DesktopAgent tool).
    result_stmt = (
        select(DesktopCommandResult)
        .where(
            DesktopCommandResult.organization_id == organization_id,
            DesktopCommandResult.received_at >= since,
        )
        .order_by(desc(DesktopCommandResult.received_at))
        .limit(max(1, int(max_samples)))
    )
    result_rows = list((await session.execute(result_stmt)).scalars().all())
    command_ids = [row.command_id for row in result_rows if getattr(row, "command_id", None) is not None]

    commands_by_id: Dict[UUID, DesktopCommand] = {}
    if command_ids:
        commands_stmt = select(DesktopCommand).where(
            DesktopCommand.organization_id == organization_id,
            DesktopCommand.id.in_(command_ids),
        )
        command_rows = list((await session.execute(commands_stmt)).scalars().all())
        commands_by_id = {row.id: row for row in command_rows}

    browser_total = 0
    browser_success = 0
    browser_durations: List[int] = []
    desktop_total = 0
    desktop_success = 0
    desktop_durations: List[int] = []

    for row in result_rows:
        command = commands_by_id.get(row.command_id)
        action = str(getattr(command, "action", "") or "")
        is_browser = action.startswith("browser_")
        bucket = "browser" if is_browser else "desktop"

        ok = str(row.status or "").strip().lower() == "success"
        duration = _safe_int(row.duration_ms)
        if bucket == "browser":
            browser_total += 1
            browser_success += 1 if ok else 0
            if duration is not None:
                browser_durations.append(duration)
        else:
            desktop_total += 1
            desktop_success += 1 if ok else 0
            if duration is not None:
                desktop_durations.append(duration)

    browser_avg_duration_ms = (sum(browser_durations) / len(browser_durations)) if browser_durations else None
    desktop_avg_duration_ms = (sum(desktop_durations) / len(desktop_durations)) if desktop_durations else None

    # Integration coverage signal influences API compatibility.
    active_integrations_stmt = select(ApiIntegration).where(
        ApiIntegration.client_id == organization_id,
        ApiIntegration.is_active.is_(True),
    )
    active_integrations = list((await session.execute(active_integrations_stmt)).scalars().all())
    active_integration_count = len(active_integrations)

    # Build candidates for the reasoner.
    candidates: List[Dict[str, Any]] = []

    api_success_prob = _bayes_success_prob(
        success=api_success,
        total=api_total,
        prior=0.78,
        weight=10,
    )
    api_speed = _speed_score_from_duration_ms(avg_duration_ms=api_avg_duration_ms, default=0.7)
    api_history = _clamp01(min(api_total, 50) / 50.0)
    api_compat = 0.85 if active_integration_count else 0.65
    api_health = _clamp01(api_success_prob)  # simple proxy for now
    candidates.append(
        {
            "tool": "api",
            "available": bool(api_available),
            "confidence": api_success_prob,
            "health": api_health,
            "speed": api_speed,
            "history": api_history,
            "cost": float(api_avg_cost_usd or 0.02),
            "compatibility": api_compat,
            "evidence": {
                "sample_size": api_total,
                "success": api_success,
                "avg_duration_ms": api_avg_duration_ms,
                "avg_cost_usd": api_avg_cost_usd,
                "active_integrations": active_integration_count,
                "n8n_available": api_available,
            },
            "reason": (
                "API (integrations/autopilot) is best for structured operations and SaaS integrations."
                if api_available
                else "API tool is unavailable (n8n is not configured)."
            ),
        }
    )

    browser_success_prob = _bayes_success_prob(
        success=browser_success,
        total=browser_total,
        prior=0.62,
        weight=10,
    )
    browser_speed = _speed_score_from_duration_ms(avg_duration_ms=browser_avg_duration_ms, default=0.55)
    browser_history = _clamp01(min(browser_total, 50) / 50.0)
    browser_health = _clamp01(browser_success_prob) if browser_available else 0.0
    browser_compat = 0.82 if desktop_allowlist else 0.25
    candidates.append(
        {
            "tool": "browser",
            "available": bool(browser_available),
            "confidence": browser_success_prob,
            "health": browser_health,
            "speed": browser_speed,
            "history": browser_history,
            "cost": 0.0,
            "compatibility": browser_compat,
            "evidence": {
                "sample_size": browser_total,
                "success": browser_success,
                "avg_duration_ms": browser_avg_duration_ms,
                "online_agents": online_agent_count,
                "allowlist_size": len(desktop_allowlist),
            },
            "reason": (
                "Browser (Playwright on desktop_agent) is best for web UIs when there is no reliable API."
                if browser_available
                else "Browser tool requires an online desktop agent and a non-empty domain allowlist."
            ),
        }
    )

    desktop_success_prob = _bayes_success_prob(
        success=desktop_success,
        total=desktop_total,
        prior=0.55,
        weight=10,
    )
    desktop_speed = _speed_score_from_duration_ms(avg_duration_ms=desktop_avg_duration_ms, default=0.5)
    desktop_history = _clamp01(min(desktop_total, 50) / 50.0)
    desktop_health = _clamp01(desktop_success_prob) if desktop_available else 0.0
    candidates.append(
        {
            "tool": "desktop",
            "available": bool(desktop_available),
            "confidence": desktop_success_prob,
            "health": desktop_health,
            "speed": desktop_speed,
            "history": desktop_history,
            "cost": 0.0,
            "compatibility": 0.72 if desktop_available else 0.25,
            "evidence": {
                "sample_size": desktop_total,
                "success": desktop_success,
                "avg_duration_ms": desktop_avg_duration_ms,
                "online_agents": online_agent_count,
            },
            "reason": (
                "Desktop (mouse/keyboard + vision) is best for native apps and non-automatable UIs."
                if desktop_available
                else "Desktop tool requires an online desktop agent."
            ),
        }
    )

    reasoner_ctx = {
        "max_cost": float(os.getenv("TOOL_SELECTOR_MAX_COST", "1.0") or 1.0),
    }
    try:
        policy = await load_effective_policy(session, organization_id=organization_id)
        weights = policy.tool_weights()
        if weights:
            reasoner_ctx["weights"] = weights
    except Exception:
        # Policy-based tool weights are best-effort and should never break tool selection snapshotting.
        pass
    ranked = rank_tools(candidates=candidates, context=reasoner_ctx)
    selected = choose_tool(candidates=candidates, context=reasoner_ctx, min_score=0.2)

    snapshots: List[ToolCandidateSnapshot] = []
    for item in ranked:
        evidence = item.get("evidence") if isinstance(item.get("evidence"), dict) else {}
        snapshots.append(
            ToolCandidateSnapshot(
                tool=str(item.get("tool") or ""),
                available=bool(item.get("available", True)),
                score=float(item.get("reasoner_score") or 0.0),
                success_prob=float(item.get("confidence") or 0.0),
                avg_duration_ms=_safe_float(evidence.get("avg_duration_ms")),
                avg_cost_usd=_safe_float(evidence.get("avg_cost_usd")),
                sample_size=int(evidence.get("sample_size") or 0),
                reasoning=str(item.get("reason") or ""),
                evidence=dict(evidence),
            )
        )

    selected_tool = str(selected.get("tool")) if isinstance(selected, dict) and selected.get("tool") else None

    return {
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "window_days": int(window_days),
        "selected_tool": selected_tool,
        "tools": [
            {
                "tool": snap.tool,
                "available": snap.available,
                "score": round(snap.score, 4),
                "success_prob": round(snap.success_prob, 4),
                "avg_duration_ms": (round(float(snap.avg_duration_ms), 2) if snap.avg_duration_ms is not None else None),
                "avg_cost_usd": (round(float(snap.avg_cost_usd), 6) if snap.avg_cost_usd is not None else None),
                "sample_size": snap.sample_size,
                "reasoning": snap.reasoning,
                "evidence": snap.evidence,
            }
            for snap in snapshots
        ],
        "availability": {
            "desktop_feature_enabled": bool(desktop_feature_on),
            "n8n_available": bool(api_available),
            "online_desktop_agents": online_agent_count,
            "browser_domain_allowlist_size": len(desktop_allowlist),
        },
    }
