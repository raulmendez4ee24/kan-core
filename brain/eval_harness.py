from __future__ import annotations

import os
import time
from contextlib import contextmanager
from statistics import mean
from typing import Any, Awaitable, Callable, Dict, List, Optional
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from brain.autonomous_case_manager import create_or_update_case

RuntimeRunner = Callable[..., Awaitable[Dict[str, Any]]]
_METRIC_NAMES = (
    "case_completion_rate",
    "tool_switch_rate",
    "rollback_success_rate",
    "mean_time_to_resolution",
    "token_cost_per_case",
    "anthropic_tool_rounds_per_case",
    "outcome_mismatch_rate",
)


@contextmanager
def _temporary_env(name: str, value: str) -> Any:
    previous = os.getenv(name)
    os.environ[name] = value
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = previous


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _bool_case_completed(result: Dict[str, Any]) -> bool:
    case = dict(result.get("case") or {})
    supervision = dict(result.get("supervision") or {})
    if str(case.get("status") or "").strip().lower() == "completed":
        return True
    return str(supervision.get("action") or "").strip().lower() == "close_case"


def _bool_tool_switch(result: Dict[str, Any]) -> bool:
    supervision = dict(result.get("supervision") or {})
    if str(supervision.get("action") or "").strip().lower() == "switch_tool":
        return True
    execution_result = dict(result.get("execution_result") or {})
    attempts = list(execution_result.get("attempts") or [])
    return len(attempts) > 1


def _rollback_success_value(result: Dict[str, Any]) -> Optional[float]:
    execution_result = dict(result.get("execution_result") or {})
    rollback_attempted = bool(execution_result.get("rollback_attempted"))
    if not rollback_attempted:
        return None
    rollback_result = dict(execution_result.get("rollback_result") or {})
    status = str(rollback_result.get("status") or "").strip().lower()
    success = bool(execution_result.get("compensated")) or status in {"restored", "dispatched"}
    return 1.0 if success else 0.0


def extract_case_metrics(
    *,
    domain: str,
    runtime: str,
    result: Dict[str, Any],
    elapsed_seconds: float,
) -> Dict[str, Any]:
    execution_result = dict(result.get("execution_result") or {})
    outcome = dict(result.get("outcome_evaluation") or {})
    decision = dict(result.get("decision") or {})
    usage = dict(execution_result.get("usage") or {})
    agentic = dict(decision.get("agentic") or {})
    case = dict(result.get("case") or {})
    token_cost = _safe_float(
        usage.get("cost_usd"),
        _safe_float(agentic.get("token_cost_usd"), _safe_float(execution_result.get("token_cost_usd"))),
    )
    anthropic_rounds = int(agentic.get("anthropic_tool_rounds") or execution_result.get("anthropic_tool_rounds") or 0)
    rollback_success = _rollback_success_value(result)
    mismatch = not bool(outcome.get("matches_expected", True))
    return {
        "domain": str(domain),
        "runtime": str(runtime),
        "case_id": str(case.get("id") or ""),
        "case_completion": 1.0 if _bool_case_completed(result) else 0.0,
        "tool_switch": 1.0 if _bool_tool_switch(result) else 0.0,
        "rollback_success": rollback_success,
        "time_to_resolution_seconds": round(float(elapsed_seconds), 6),
        "token_cost_per_case": round(float(token_cost), 6),
        "anthropic_tool_rounds_per_case": anthropic_rounds,
        "outcome_mismatch": 1.0 if mismatch else 0.0,
    }


def aggregate_metrics(case_metrics: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not case_metrics:
        return {
            "case_count": 0,
            "case_completion_rate": 0.0,
            "tool_switch_rate": 0.0,
            "rollback_success_rate": 0.0,
            "mean_time_to_resolution": 0.0,
            "token_cost_per_case": 0.0,
            "anthropic_tool_rounds_per_case": 0.0,
            "outcome_mismatch_rate": 0.0,
        }
    rollback_values = [float(item["rollback_success"]) for item in case_metrics if item.get("rollback_success") is not None]
    return {
        "case_count": len(case_metrics),
        "case_completion_rate": round(mean(item["case_completion"] for item in case_metrics), 4),
        "tool_switch_rate": round(mean(item["tool_switch"] for item in case_metrics), 4),
        "rollback_success_rate": round(mean(rollback_values), 4) if rollback_values else 0.0,
        "mean_time_to_resolution": round(mean(item["time_to_resolution_seconds"] for item in case_metrics), 6),
        "token_cost_per_case": round(mean(item["token_cost_per_case"] for item in case_metrics), 6),
        "anthropic_tool_rounds_per_case": round(mean(item["anthropic_tool_rounds_per_case"] for item in case_metrics), 4),
        "outcome_mismatch_rate": round(mean(item["outcome_mismatch"] for item in case_metrics), 4),
    }


async def _default_runner(
    *,
    session: AsyncSession,
    organization_id: UUID,
    scenario: Dict[str, Any],
) -> Dict[str, Any]:
    created = await create_or_update_case(
        session,
        organization_id=organization_id,
        case_type=str(scenario.get("case_type") or "auto"),
        current_goal=str(scenario.get("goal") or ""),
        business_context=dict(scenario.get("business_context") or {}),
        run_now=True,
    )
    return dict(created.get("run") or {})


async def run_runtime_for_scenarios(
    *,
    session: AsyncSession,
    organization_id: UUID,
    domain: str,
    scenarios: List[Dict[str, Any]],
    anthropic_enabled: bool,
    runner: Optional[RuntimeRunner] = None,
) -> Dict[str, Any]:
    effective_runner = runner or _default_runner
    runtime_name = "anthropic" if anthropic_enabled else "legacy"
    case_metrics: List[Dict[str, Any]] = []
    with _temporary_env("ENABLE_ANTHROPIC_AGENTIC_RUNTIME", "true" if anthropic_enabled else "false"):
        for scenario in scenarios:
            started = time.perf_counter()
            result = await effective_runner(
                session=session,
                organization_id=organization_id,
                scenario=dict(scenario),
            )
            elapsed = time.perf_counter() - started
            case_metrics.append(
                extract_case_metrics(
                    domain=domain,
                    runtime=runtime_name,
                    result=result,
                    elapsed_seconds=elapsed,
                )
            )
    return {
        "runtime": runtime_name,
        "cases": case_metrics,
        "metrics": aggregate_metrics(case_metrics),
    }


async def compare_domain_runtimes(
    *,
    session: AsyncSession,
    organization_id: UUID,
    domain: str,
    scenarios: List[Dict[str, Any]],
    runner: Optional[RuntimeRunner] = None,
) -> Dict[str, Any]:
    legacy = await run_runtime_for_scenarios(
        session=session,
        organization_id=organization_id,
        domain=domain,
        scenarios=scenarios,
        anthropic_enabled=False,
        runner=runner,
    )
    anthropic = await run_runtime_for_scenarios(
        session=session,
        organization_id=organization_id,
        domain=domain,
        scenarios=scenarios,
        anthropic_enabled=True,
        runner=runner,
    )
    return {
        "domain": domain,
        "scenario_count": len(scenarios),
        "legacy": legacy,
        "anthropic": anthropic,
        "delta": {
            "case_completion_rate": round(
                float(anthropic["metrics"]["case_completion_rate"]) - float(legacy["metrics"]["case_completion_rate"]),
                4,
            ),
            "tool_switch_rate": round(
                float(anthropic["metrics"]["tool_switch_rate"]) - float(legacy["metrics"]["tool_switch_rate"]),
                4,
            ),
            "rollback_success_rate": round(
                float(anthropic["metrics"]["rollback_success_rate"]) - float(legacy["metrics"]["rollback_success_rate"]),
                4,
            ),
            "mean_time_to_resolution": round(
                float(anthropic["metrics"]["mean_time_to_resolution"]) - float(legacy["metrics"]["mean_time_to_resolution"]),
                6,
            ),
            "token_cost_per_case": round(
                float(anthropic["metrics"]["token_cost_per_case"]) - float(legacy["metrics"]["token_cost_per_case"]),
                6,
            ),
            "anthropic_tool_rounds_per_case": round(
                float(anthropic["metrics"]["anthropic_tool_rounds_per_case"]) - float(legacy["metrics"]["anthropic_tool_rounds_per_case"]),
                4,
            ),
            "outcome_mismatch_rate": round(
                float(anthropic["metrics"]["outcome_mismatch_rate"]) - float(legacy["metrics"]["outcome_mismatch_rate"]),
                4,
            ),
        },
    }


async def build_comparative_report(
    *,
    session: AsyncSession,
    organization_id: UUID,
    domains: Dict[str, List[Dict[str, Any]]],
    runner: Optional[RuntimeRunner] = None,
) -> Dict[str, Any]:
    domain_reports: List[Dict[str, Any]] = []
    all_legacy: List[Dict[str, Any]] = []
    all_anthropic: List[Dict[str, Any]] = []
    for domain, scenarios in domains.items():
        report = await compare_domain_runtimes(
            session=session,
            organization_id=organization_id,
            domain=domain,
            scenarios=list(scenarios),
            runner=runner,
        )
        domain_reports.append(report)
        all_legacy.extend(list(report["legacy"]["cases"]))
        all_anthropic.extend(list(report["anthropic"]["cases"]))
    return {
        "domains": domain_reports,
        "overall": {
            "legacy": aggregate_metrics(all_legacy),
            "anthropic": aggregate_metrics(all_anthropic),
        },
        "report_text": render_comparative_report(
            {
                "domains": domain_reports,
                "overall": {
                    "legacy": aggregate_metrics(all_legacy),
                    "anthropic": aggregate_metrics(all_anthropic),
                },
            }
        ),
    }


def render_comparative_report(report: Dict[str, Any]) -> str:
    lines = ["Anthropic Runtime vs Legacy Comparative Report", ""]
    for domain_report in list(report.get("domains") or []):
        domain = str(domain_report.get("domain") or "unknown")
        lines.append(f"[{domain}]")
        legacy = dict(domain_report.get("legacy", {}).get("metrics") or {})
        anthropic = dict(domain_report.get("anthropic", {}).get("metrics") or {})
        delta = dict(domain_report.get("delta") or {})
        for metric_name in _METRIC_NAMES:
            lines.append(
                f"- {metric_name}: legacy={legacy.get(metric_name, 0.0)} "
                f"anthropic={anthropic.get(metric_name, 0.0)} delta={delta.get(metric_name, 0.0)}"
            )
        lines.append("")
    overall = dict(report.get("overall") or {})
    lines.append("[overall]")
    legacy_overall = dict(overall.get("legacy") or {})
    anthropic_overall = dict(overall.get("anthropic") or {})
    for metric_name in _METRIC_NAMES:
        overall_delta = round(
            float(anthropic_overall.get(metric_name, 0.0)) - float(legacy_overall.get(metric_name, 0.0)),
            6 if metric_name in {"mean_time_to_resolution", "token_cost_per_case"} else 4,
        )
        lines.append(
            f"- {metric_name}: legacy={legacy_overall.get(metric_name, 0.0)} "
            f"anthropic={anthropic_overall.get(metric_name, 0.0)} delta={overall_delta}"
        )
    return "\n".join(lines)
