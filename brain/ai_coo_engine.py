import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from brain.audit import write_audit_log
from brain.business_context_engine import analyze_business_context, propose_context_actions
from brain.business_monitor import build_metrics_snapshot
from brain.guardrails import evaluate_circuit_breaker
from brain.policy_engine import load_effective_policy
from brain.tool_selection import build_tool_selection_snapshot
from brain.agent_orchestrator import orchestrate_agents
from brain.crm_engine import run_followups
from brain.sales_autopilot import run_sales_autopilot_cycle
from brain.collections_engine import run_collections_cycle
from learning.tool_memory import record_tool_outcome
from routes.integrations import run_integration_autopilot
from schemas.integration import IntegrationAutopilotRequest

logger = logging.getLogger("kan_core.ai_coo")


def _enabled() -> bool:
    return os.getenv("ENABLE_AI_COO", "false").lower() in {"1", "true", "yes"}


def _clamp_execution_mode(value: Optional[str]) -> str:
    token = (value or "").strip().lower()
    return token if token in {"safe", "semi_auto", "full_auto"} else ""


def _integration_success(results: List[Any]) -> bool:
    ok_statuses = {"integrated", "saved_without_workflow", "already_covered"}
    if not results:
        return False
    statuses = {str(getattr(item, "status", "") or "") for item in results}
    return bool(statuses.intersection(ok_statuses))


async def run_ai_coo_cycle(
    session: AsyncSession,
    *,
    organization_id: UUID,
    window_hours: int,
    dry_run: bool,
    max_actions: int,
    execution_mode_override: Optional[str] = None,
) -> Dict[str, Any]:
    if not _enabled():
        return {
            "organization_id": str(organization_id),
            "computed_at": datetime.now(timezone.utc),
            "execution_mode": "safe",
            "circuit_breaker_open": False,
            "signals": {},
            "insights": [],
            "tool_snapshot": {},
            "action_plan": [],
            "executed": False,
            "results": [],
            "reason": "disabled",
        }

    policy = await load_effective_policy(session, organization_id=organization_id)
    execution_mode = policy.autonomy_execution_mode()
    override = _clamp_execution_mode(execution_mode_override)
    if override:
        execution_mode = override

    cb = await evaluate_circuit_breaker(
        session,
        organization_id=organization_id,
        policy=policy,
        source="ai_coo",
    )
    if cb.open:
        execution_mode = "safe"

    # Signals.
    snapshot = await build_metrics_snapshot(session, organization_id=organization_id, window_minutes=5)
    tool_snapshot = await build_tool_selection_snapshot(
        session,
        organization_id=organization_id,
        window_days=int(os.getenv("AI_COO_TOOL_SELECTOR_WINDOW_DAYS", "30") or 30),
        max_samples=int(os.getenv("AI_COO_TOOL_SELECTOR_MAX_SAMPLES", "200") or 200),
    )

    analysis = await analyze_business_context(
        session,
        organization_id=organization_id,
        window_hours=max(1, min(int(window_hours), 24 * 14)),
        min_prev_sales=int(os.getenv("BUSINESS_CONTEXT_MIN_PREV_SALES", "3") or 3),
        sales_drop_threshold=float(os.getenv("BUSINESS_CONTEXT_SALES_DROP_THRESHOLD", "0.3") or 0.3),
    )
    insights = analysis.get("insights") if isinstance(analysis.get("insights"), list) else []

    proposals = propose_context_actions(organization_id=organization_id, analysis=analysis)

    action_plan: List[Dict[str, Any]] = []

    # Convert proposals into concrete actions.
    for proposal in proposals:
        desired = proposal.get("desired_functions") or {}
        desired_functions = []
        if isinstance(desired, dict):
            desired_functions = [str(x) for x in desired.get("items", []) if str(x).strip()]
        elif isinstance(desired, list):
            desired_functions = [str(x) for x in desired if str(x).strip()]

        action_plan.append(
            {
                "action_type": "integration_autopilot",
                "tool": "api",
                "risk": "medium",
                "requires_approval": False,
                "reason": str(proposal.get("proposal") or "Aplicar integraciones/automatizaciones"),
                "payload": {
                    "request_text": str(proposal.get("request_text") or ""),
                    "desired_functions": desired_functions,
                },
            }
        )

    # If sales_drop: add predictive followups action.
    if any(str(item.get("key")) == "sales_drop" for item in insights):
        action_plan.insert(
            0,
            {
                "action_type": "predictive_followups",
                "tool": "api",
                "risk": "medium",
                "requires_approval": False,
                "reason": "Ventas bajaron: priorizar leads y ejecutar followups con predicciones.",
                "payload": {
                    "max_jobs": int(os.getenv("AI_COO_FOLLOWUPS_MAX_JOBS", "30") or 30),
                },
            },
        )
        action_plan.insert(
            1,
            {
                "action_type": "sales_autopilot",
                "tool": "api",
                "risk": "medium",
                "requires_approval": False,
                "reason": "Ventas bajaron: activar ciclo completo de ventas (lead->seguimiento->cita).",
                "payload": {
                    "max_actions": int(os.getenv("AI_COO_SALES_MAX_ACTIONS", "40") or 40),
                },
            },
        )

    # Always keep a low-cost sales autopilot pulse.
    action_plan.append(
        {
            "action_type": "sales_autopilot",
            "tool": "api",
            "risk": "low",
            "requires_approval": False,
            "reason": "Mantener pipeline activo y actualizado automáticamente.",
            "payload": {
                "max_actions": int(os.getenv("AI_COO_SALES_MAX_ACTIONS", "20") or 20),
            },
        }
    )

    # Optional collections cycle when enabled.
    if os.getenv("ENABLE_COLLECTIONS_AI", "true").lower() in {"1", "true", "yes"}:
        action_plan.append(
            {
                "action_type": "collections",
                "tool": "api",
                "risk": "medium",
                "requires_approval": False,
                "reason": "Recuperar cartera pendiente con seguimiento automatizado.",
                "payload": {
                    "max_cases": int(os.getenv("AI_COO_COLLECTIONS_MAX_CASES", "50") or 50),
                    "min_amount_due": float(os.getenv("AI_COO_COLLECTIONS_MIN_AMOUNT", "50") or 50),
                },
            }
        )

    # Optional: multi-agent coordination.
    if os.getenv("ENABLE_MULTI_AGENT", "false").lower() in {"1", "true", "yes"}:
        action_plan.append(
            {
                "action_type": "multi_agent_orchestration",
                "tool": "api",
                "risk": "low",
                "requires_approval": False,
                "reason": "Coordinar agentes (ventas/soporte/operaciones) para ejecutar plan.",
                "payload": {
                    "agents": ["sales", "support", "operations"],
                    "goal": "Mejorar ventas y estabilidad operativa en base a insights recientes.",
                },
            }
        )

    # Cap actions.
    action_plan = action_plan[: max(1, min(int(max_actions), 200))]

    executed = False
    results: List[Dict[str, Any]] = []

    can_execute_campaigns = bool(policy.allow_execute_campaigns())
    # Full automation is only allowed when enterprise policies are explicitly enforced.
    allow_execute = (not dry_run) and bool(policy.enforced) and execution_mode in {"semi_auto", "full_auto"}

    for item in action_plan:
        action_type = str(item.get("action_type") or "")
        risk = str(item.get("risk") or "medium")
        if not allow_execute:
            break

        if execution_mode == "semi_auto" and risk == "high":
            results.append({"action_type": action_type, "status": "skipped", "reason": "semi_auto blocks high risk"})
            continue

        # Campaign-like actions require explicit policy.
        if action_type in {"predictive_followups"} and not can_execute_campaigns:
            results.append({"action_type": action_type, "status": "skipped", "reason": "blocked_by_policy.allow_execute_campaigns"})
            continue

        if action_type == "predictive_followups":
            payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
            max_jobs = int(payload.get("max_jobs") or 30)
            try:
                out = await run_followups(
                    session,
                    organization_id=organization_id,
                    max_jobs=max(1, min(max_jobs, 200)),
                    dry_run=False,
                    predictive=True,
                )
                executed = True
                results.append({"action_type": action_type, "status": "ok", "result": {"executed": out.get("executed"), "skipped": out.get("skipped")}})
                await record_tool_outcome(
                    session,
                    organization_id=str(organization_id),
                    tool="api",
                    success=True,
                    extra={"source": "ai_coo", "action_type": action_type},
                )
            except Exception as exc:
                results.append({"action_type": action_type, "status": "failed", "error": str(exc)})
                await record_tool_outcome(
                    session,
                    organization_id=str(organization_id),
                    tool="api",
                    success=False,
                    extra={"source": "ai_coo", "action_type": action_type, "error": str(exc)[:300]},
                )
            continue

        if action_type == "sales_autopilot":
            payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
            max_actions = int(payload.get("max_actions") or 20)
            try:
                out = await run_sales_autopilot_cycle(
                    session,
                    organization_id=organization_id,
                    dry_run=False,
                    max_actions=max(1, min(max_actions, 300)),
                    create_followups=True,
                )
                executed = True
                results.append(
                    {
                        "action_type": action_type,
                        "status": "ok",
                        "result": {
                            "scanned_leads": int(out.get("scanned_leads", 0)),
                            "action_count": int(out.get("action_count", 0)),
                        },
                    }
                )
                await record_tool_outcome(
                    session,
                    organization_id=str(organization_id),
                    tool="api",
                    success=True,
                    extra={"source": "ai_coo", "action_type": action_type},
                )
            except Exception as exc:
                results.append({"action_type": action_type, "status": "failed", "error": str(exc)})
                await record_tool_outcome(
                    session,
                    organization_id=str(organization_id),
                    tool="api",
                    success=False,
                    extra={"source": "ai_coo", "action_type": action_type, "error": str(exc)[:300]},
                )
            continue

        if action_type == "collections":
            payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
            max_cases = int(payload.get("max_cases") or 50)
            min_amount_due = float(payload.get("min_amount_due") or 50)
            try:
                out = await run_collections_cycle(
                    session,
                    organization_id=organization_id,
                    dry_run=False,
                    max_cases=max(1, min(max_cases, 500)),
                    min_amount_due=max(0.0, min_amount_due),
                )
                executed = True
                results.append(
                    {
                        "action_type": action_type,
                        "status": "ok",
                        "result": {
                            "created_cases": int(out.get("created_cases", 0)),
                            "scheduled_actions": int(out.get("scheduled_actions", 0)),
                        },
                    }
                )
                await record_tool_outcome(
                    session,
                    organization_id=str(organization_id),
                    tool="api",
                    success=True,
                    extra={"source": "ai_coo", "action_type": action_type},
                )
            except Exception as exc:
                results.append({"action_type": action_type, "status": "failed", "error": str(exc)})
                await record_tool_outcome(
                    session,
                    organization_id=str(organization_id),
                    tool="api",
                    success=False,
                    extra={"source": "ai_coo", "action_type": action_type, "error": str(exc)[:300]},
                )
            continue

        if action_type == "integration_autopilot":
            payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
            req = IntegrationAutopilotRequest(
                request_text=str(payload.get("request_text") or ""),
                desired_functions=[str(x) for x in (payload.get("desired_functions") or [])],
                dry_run=False,
                auto_generate_workflow=True,
                continue_on_error=True,
                enable_provider_fallback=True,
                auto_repair_max_attempts=2,
            )
            try:
                out = await run_integration_autopilot(req=req, client_id=str(organization_id), session=session)
                ok = _integration_success(list(out.results))
                executed = True
                results.append({"action_type": action_type, "status": "ok" if ok else "failed", "result": out.model_dump()})
                await record_tool_outcome(
                    session,
                    organization_id=str(organization_id),
                    tool="api",
                    success=bool(ok),
                    extra={"source": "ai_coo", "action_type": action_type},
                )
            except Exception as exc:
                results.append({"action_type": action_type, "status": "failed", "error": str(exc)})
                await record_tool_outcome(
                    session,
                    organization_id=str(organization_id),
                    tool="api",
                    success=False,
                    extra={"source": "ai_coo", "action_type": action_type, "error": str(exc)[:300]},
                )
            continue

        if action_type == "multi_agent_orchestration":
            payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
            try:
                out = await orchestrate_agents(
                    organization_id=str(organization_id),
                    goal=str(payload.get("goal") or "Mejorar ventas"),
                    context={"max_cost": float(os.getenv("TOOL_SELECTOR_MAX_COST", "1.0") or 1.0)},
                    agents=[str(x) for x in (payload.get("agents") or [])],
                    dry_run=False,
                )
                executed = True
                results.append({"action_type": action_type, "status": "ok", "result": out})
                await record_tool_outcome(
                    session,
                    organization_id=str(organization_id),
                    tool="api",
                    success=bool(out.get("execution_result", {}).get("dispatched", False)),
                    extra={"source": "ai_coo", "action_type": action_type},
                )
            except Exception as exc:
                results.append({"action_type": action_type, "status": "failed", "error": str(exc)})
                await record_tool_outcome(
                    session,
                    organization_id=str(organization_id),
                    tool="api",
                    success=False,
                    extra={"source": "ai_coo", "action_type": action_type, "error": str(exc)[:300]},
                )
            continue

        results.append({"action_type": action_type, "status": "skipped", "reason": "unsupported_action_type"})

    try:
        await write_audit_log(
            session,
            organization_id=organization_id,
            actor_user_id=None,
            action="ai_coo.cycle",
            resource="organization",
            resource_id=str(organization_id),
            status="ok",
            metadata={
                "execution_mode": execution_mode,
                "dry_run": bool(dry_run),
                "circuit_breaker_open": bool(cb.open),
                "circuit_breaker_reason": cb.reason,
                "error_rate": round(float(cb.error_rate), 6),
                "insights": insights,
                "action_plan_count": len(action_plan),
                "executed": bool(executed),
                "results_count": len(results),
            },
        )
    except Exception:
        logger.exception("ai_coo_audit_failed org=%s", organization_id)

    return {
        "organization_id": str(organization_id),
        "computed_at": datetime.now(timezone.utc),
        "execution_mode": execution_mode,
        "circuit_breaker_open": bool(cb.open),
        "signals": {"metrics_snapshot": snapshot.metrics or {}},
        "insights": insights,
        "tool_snapshot": tool_snapshot,
        "action_plan": action_plan,
        "executed": bool(executed),
        "results": results,
    }
