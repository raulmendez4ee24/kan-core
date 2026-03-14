from __future__ import annotations

import os
import traceback
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from brain.anthropic_agent_runtime import AnthropicAgentRuntime, anthropic_agentic_enabled
from brain.business_memory import get_business_memory
from brain.business_state_engine import get_latest_business_state
from brain.case_progress_store import load_case_progress, save_case_progress
from brain.decision_engine import DecisionContext, decide_next_step
from brain.execution_lease_manager import acquire_case_lease, release_case_lease
from brain.execution_rollback import execute_rollback_from_metadata
from brain.execution_router import route_execution
from brain.idempotency_store import build_idempotency_key, build_step_hash, get_idempotent_result, store_idempotent_result
from brain.operation_memory import list_operation_memory, record_operation_memory
from brain.outcome_evaluator import evaluate_outcome
from brain.self_supervisor import decide_supervision_action
from brain.skill_feedback import record_skill_feedback
from brain.world_state_store import load_world_state, save_world_state
from models import AuditLog, AutonomousCase, AutonomousCaseStep

_CASE_TYPES = {
    "customer_recovery",
    "payment_followthrough",
    "ops_anomaly_response",
    "workflow_recovery",
    "campaign_execution",
    "sales_execution",
    "support_resolution",
    "onboarding_execution",
    "collections_followthrough",
}

_PAYMENT_TOKENS = ("payment", "pago", "cobro", "invoice", "factura", "collections")
_CUSTOMER_TOKENS = ("customer", "cliente", "lead", "contact", "churn", "retention")
_WORKFLOW_TOKENS = ("workflow", "integration", "webhook", "sync", "pipeline", "recovery", "incident", "error")
_CAMPAIGN_TOKENS = ("campaign", "campana", "outreach", "sequence", "newsletter", "email blast")
_SALES_TOKENS = ("sales", "venta", "prospect", "lead scoring", "followup", "pipeline")
_SUPPORT_TOKENS = ("support", "soporte", "ticket", "sla", "escalation", "reply")
_ONBOARDING_TOKENS = ("onboarding", "implantacion", "setup", "intake", "kickoff")
_COLLECTIONS_TOKENS = ("collections", "cobranza", "saldo", "debt", "agreement", "past due")
_ACTIVE_CASE_STATUSES = {"open", "waiting", "in_progress"}


def _normalize_case_type(case_type: str) -> str:
    token = str(case_type or "").strip().lower()
    if token in _CASE_TYPES:
        return token
    return "ops_anomaly_response"


def classify_case_type(
    *,
    current_goal: str,
    business_context: Optional[Dict[str, Any]] = None,
    business_state: Optional[Dict[str, Any]] = None,
    requested_case_type: Optional[str] = None,
    allow_generic: bool = True,
) -> Optional[str]:
    requested = str(requested_case_type or "").strip().lower()
    if requested in _CASE_TYPES:
        return requested

    goal_text = str(current_goal or "").strip().lower()
    context = dict(business_context or {})
    anomaly_type = str(context.get("anomaly_type") or "").strip().lower()
    recommended_focus = str((business_state or {}).get("recommended_focus") or "").strip().lower()
    searchable = " ".join(
        token for token in [goal_text, anomaly_type, str(context.get("intent_hint") or "").lower()] if token
    )

    if any(token in searchable for token in _PAYMENT_TOKENS):
        return "payment_followthrough"
    if any(token in searchable for token in _WORKFLOW_TOKENS):
        return "workflow_recovery"
    if any(token in searchable for token in _CAMPAIGN_TOKENS):
        return "campaign_execution"
    if any(token in searchable for token in _COLLECTIONS_TOKENS):
        return "collections_followthrough"
    if any(token in searchable for token in _ONBOARDING_TOKENS):
        return "onboarding_execution"
    if any(token in searchable for token in _SUPPORT_TOKENS):
        return "support_resolution"
    if any(token in searchable for token in _SALES_TOKENS):
        return "sales_execution"
    if any(token in searchable for token in _CUSTOMER_TOKENS):
        return "customer_recovery"

    if recommended_focus in _CASE_TYPES:
        if recommended_focus != "ops_anomaly_response" or allow_generic:
            return recommended_focus

    if allow_generic and (
        anomaly_type
        or bool(context.get("high_impact_operation"))
        or str(context.get("source") or "").strip().lower() in {"proactive_monitor", "master_brain"}
    ):
        return "ops_anomaly_response"
    return "ops_anomaly_response" if allow_generic else None


def _case_read(row: AutonomousCase) -> Dict[str, Any]:
    return {
        "id": str(row.id),
        "organization_id": str(row.organization_id),
        "case_type": row.case_type,
        "status": row.status,
        "priority_score": float(row.priority_score or 0.0),
        "risk_score": float(row.risk_score or 0.0),
        "current_goal": row.current_goal,
        "business_context": dict(row.business_context or {}),
        "world_state": dict(row.world_state or {}),
        "last_decision": dict(row.last_decision or {}),
        "last_execution_result": dict(row.last_execution_result or {}),
        "resolution_summary": dict(row.resolution_summary or {}),
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _step_read(row: AutonomousCaseStep) -> Dict[str, Any]:
    return {
        "id": str(row.id),
        "case_id": str(row.case_id),
        "step_index": int(row.step_index or 0),
        "status": row.status,
        "decision_payload": dict(row.decision_payload or {}),
        "execution_payload": dict(row.execution_payload or {}),
        "execution_result": dict(row.execution_result or {}),
        "outcome_evaluation": dict(row.outcome_evaluation or {}),
        "created_at": row.created_at,
    }


def _business_context_key(business_context: Dict[str, Any]) -> str:
    if not business_context:
        return ""
    for key in ("case_key", "source_ref", "lead_id", "ticket_id", "customer_id", "session_id", "recommendation_id"):
        value = str(business_context.get(key) or "").strip()
        if value:
            return f"{key}:{value}"
    return ""


class AutonomousCaseManager:
    """Thin compatibility wrapper for scripts that need a manager-like runtime entrypoint."""

    def __init__(self, *, use_anthropic: bool = False) -> None:
        self.use_anthropic = bool(use_anthropic)

    async def run_case(
        self,
        session: AsyncSession,
        case_input: Dict[str, Any],
        *,
        domain: Optional[str] = None,
        scenario: str = "",
    ) -> Dict[str, Any]:
        previous = os.getenv("ENABLE_ANTHROPIC_AGENTIC_RUNTIME")
        os.environ["ENABLE_ANTHROPIC_AGENTIC_RUNTIME"] = "true" if self.use_anthropic else "false"
        safe_runtime = os.getenv("EVAL_HARNESS_SAFE_RUNTIME", "true").lower() in {"1", "true", "yes"}
        original_get_latest_business_state = get_latest_business_state
        try:
            payload = dict(case_input or {})
            goal = str(payload.get("objective") or payload.get("objetivo") or scenario or "").strip()
            organization_value = str(payload.get("organization_id") or goal or "autonomous-case-manager")
            organization_id = uuid5(NAMESPACE_URL, f"case-manager:{organization_value}")
            business_context = dict(payload.get("context") or {})
            case_type = {
                "payment": "payment_followthrough",
                "collections": "collections_followthrough",
                "onboarding": "onboarding_execution",
                "campaign_execution": "campaign_execution",
                "marketing": "campaign_execution",
                "sales_execution": "sales_execution",
                "sales": "sales_execution",
            }.get(str(domain or "").strip().lower(), "auto")
            if safe_runtime:
                async def _safe_business_state(*_args: Any, **_kwargs: Any) -> Dict[str, Any]:
                    return {
                        "organization_id": str(organization_id),
                        "generated_at": datetime.now(timezone.utc).isoformat(),
                        "active_objectives": 0,
                        "critical_risks": [],
                        "cash_exposure": 0.0,
                        "customer_impact_score": 10.0,
                        "ops_health_score": 0.9,
                        "sla_pressure": 0.2,
                        "current_bottlenecks": {},
                        "recommended_focus": case_type if case_type in _CASE_TYPES else "ops_anomaly_response",
                        "confidence": 0.75,
                        "metrics": {},
                        "window_minutes": 60,
                    }

                globals()["get_latest_business_state"] = _safe_business_state
            created = await create_or_update_case(
                session,
                organization_id=organization_id,
                case_type=case_type,
                current_goal=goal,
                business_context=business_context,
                run_now=False,
            )
            case_row = dict(created.get("case") or {})
            return await run_case(
                session,
                organization_id=organization_id,
                case_id=UUID(str(case_row["id"])),
            )
        finally:
            globals()["get_latest_business_state"] = original_get_latest_business_state
            if previous is None:
                os.environ.pop("ENABLE_ANTHROPIC_AGENTIC_RUNTIME", None)
            else:
                os.environ["ENABLE_ANTHROPIC_AGENTIC_RUNTIME"] = previous


def _should_use_anthropic_runtime(
    *,
    decision: Dict[str, Any],
    rollbackable: Optional[Dict[str, Any]],
) -> tuple[bool, str]:
    risk_score = float(decision.get("risk_score") or 0.0)
    requires_replan = bool(decision.get("requires_replan_if_failed"))
    compensation_involved = bool(rollbackable)
    if risk_score > 0.7:
        return True, "high_risk"
    if requires_replan:
        return True, "requires_replan"
    if compensation_involved:
        return True, "compensation_involved"
    return False, "legacy_direct"


def _apply_eval_harness_decision_overrides(
    *,
    decision: Dict[str, Any],
    business_context: Dict[str, Any],
) -> Dict[str, Any]:
    context = dict(business_context or {})
    if os.getenv("EVAL_HARNESS_SAFE_RUNTIME", "false").lower() not in {"1", "true", "yes"}:
        return decision
    overridden = dict(decision)
    forced_risk = context.get("eval_force_risk_score")
    try:
        if forced_risk is not None:
            overridden["risk_score"] = max(float(overridden.get("risk_score") or 0.0), min(float(forced_risk), 1.0))
    except (TypeError, ValueError):
        pass
    if bool(context.get("eval_force_replan")) or bool(context.get("eval_force_compensation")):
        overridden["fallback_tools"] = []
    return overridden


def _apply_eval_harness_outcome_overrides(
    *,
    outcome: Dict[str, Any],
    business_context: Dict[str, Any],
) -> Dict[str, Any]:
    context = dict(business_context or {})
    if os.getenv("EVAL_HARNESS_SAFE_RUNTIME", "false").lower() not in {"1", "true", "yes"}:
        return outcome
    overridden = dict(outcome)
    if bool(context.get("eval_force_replan")):
        overridden.update(
            {
                "matches_expected": False,
                "reason": "eval_forced_replan",
                "needs_replan": True,
                "can_rollback": False,
                "severity": "high" if float(overridden.get("ops_health_score") or 0.0) <= 0.7 else "medium",
            }
        )
    elif bool(context.get("eval_force_compensation")):
        overridden.update(
            {
                "matches_expected": False,
                "reason": "eval_forced_compensation",
                "needs_replan": True,
                "can_rollback": True,
                "severity": "high" if float(overridden.get("ops_health_score") or 0.0) <= 0.7 else "medium",
            }
        )
    return overridden


async def _find_latest_rollbackable_action(
    session: AsyncSession,
    *,
    organization_id: UUID,
    case_type: str,
) -> Optional[Dict[str, Any]]:
    preferred_operation = (
        "payment"
        if case_type == "payment_followthrough"
        else ("delete" if case_type == "customer_recovery" else ("workflow" if case_type == "workflow_recovery" else None))
    )
    stmt = (
        select(AuditLog)
        .where(
            AuditLog.organization_id == organization_id,
            AuditLog.action.in_(["execution_guard.preflight", "execution_guard.paused", "execution_guard.tripwire"]),
        )
        .order_by(desc(AuditLog.created_at))
        .limit(50)
    )
    rows = list((await session.execute(stmt)).scalars().all())
    for row in rows:
        metadata = dict(row.audit_metadata or {})
        if not isinstance(metadata.get("rollback_snapshot"), dict):
            continue
        operation_type = str(metadata.get("operation_type") or "").strip().lower()
        if preferred_operation and operation_type != preferred_operation:
            continue
        return {
            "audit_log_id": str(row.id),
            "action": row.action,
            "metadata": metadata,
            "operation_type": operation_type,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
    return None


async def create_case(
    session: AsyncSession,
    *,
    organization_id: UUID,
    case_type: str,
    current_goal: str,
    business_context: Optional[Dict[str, Any]] = None,
    run_now: bool = False,
) -> Dict[str, Any]:
    resolved_case_type = classify_case_type(
        current_goal=current_goal,
        business_context=business_context,
        requested_case_type=case_type,
        allow_generic=True,
    ) or "ops_anomaly_response"
    row = AutonomousCase(
        organization_id=organization_id,
        case_type=resolved_case_type,
        status="open",
        current_goal=str(current_goal or "").strip(),
        business_context=dict(business_context or {}),
        world_state={},
        last_decision={},
        last_execution_result={},
        resolution_summary={},
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    result = {"case": _case_read(row)}
    if run_now:
        result["run"] = await run_case(session, organization_id=organization_id, case_id=row.id)
    return result


async def create_or_update_case(
    session: AsyncSession,
    *,
    organization_id: UUID,
    case_type: str,
    current_goal: str,
    business_context: Optional[Dict[str, Any]] = None,
    run_now: bool = False,
) -> Dict[str, Any]:
    resolved_case_type = classify_case_type(
        current_goal=current_goal,
        business_context=business_context,
        requested_case_type=case_type,
        allow_generic=True,
    ) or "ops_anomaly_response"
    context = dict(business_context or {})
    context_key = _business_context_key(context)
    stmt = (
        select(AutonomousCase)
        .where(
            AutonomousCase.organization_id == organization_id,
            AutonomousCase.case_type == resolved_case_type,
            AutonomousCase.status.in_(sorted(_ACTIVE_CASE_STATUSES)),
        )
        .order_by(desc(AutonomousCase.updated_at))
        .limit(25)
    )
    rows = list((await session.execute(stmt)).scalars().all())
    for row in rows:
        row_context = dict(row.business_context or {})
        if context_key and _business_context_key(row_context) == context_key:
            row.current_goal = str(current_goal or row.current_goal or "").strip()
            row.business_context = {**row_context, **context}
            await session.commit()
            await session.refresh(row)
            result = {"case": _case_read(row), "updated_existing": True}
            if run_now:
                result["run"] = await run_case(session, organization_id=organization_id, case_id=row.id)
            return result
        if not context_key and str(row.current_goal or "").strip() == str(current_goal or "").strip():
            row.business_context = {**row_context, **context}
            await session.commit()
            await session.refresh(row)
            result = {"case": _case_read(row), "updated_existing": True}
            if run_now:
                result["run"] = await run_case(session, organization_id=organization_id, case_id=row.id)
            return result
    return await create_case(
        session,
        organization_id=organization_id,
        case_type=resolved_case_type,
        current_goal=current_goal,
        business_context=context,
        run_now=run_now,
    )


async def get_case(
    session: AsyncSession,
    *,
    organization_id: UUID,
    case_id: UUID,
) -> Optional[Dict[str, Any]]:
    row = await session.get(AutonomousCase, case_id)
    if not row or row.organization_id != organization_id:
        return None
    return _case_read(row)


async def list_case_steps(
    session: AsyncSession,
    *,
    organization_id: UUID,
    case_id: UUID,
) -> List[Dict[str, Any]]:
    stmt = (
        select(AutonomousCaseStep)
        .where(
            AutonomousCaseStep.organization_id == organization_id,
            AutonomousCaseStep.case_id == case_id,
        )
        .order_by(AutonomousCaseStep.step_index.asc(), AutonomousCaseStep.created_at.asc())
    )
    rows = list((await session.execute(stmt)).scalars().all())
    return [_step_read(row) for row in rows]


async def list_case_queue(
    session: AsyncSession,
    *,
    organization_id: UUID,
    limit: int = 25,
) -> List[Dict[str, Any]]:
    stmt = (
        select(AutonomousCase)
        .where(
            AutonomousCase.organization_id == organization_id,
            AutonomousCase.status.in_(["open", "waiting"]),
        )
        .order_by(AutonomousCase.priority_score.desc(), AutonomousCase.updated_at.asc())
        .limit(max(1, min(limit, 100)))
    )
    rows = list((await session.execute(stmt)).scalars().all())
    return [_case_read(row) for row in rows]


async def run_case_queue(
    session: AsyncSession,
    *,
    organization_id: UUID,
    limit: int = 5,
) -> Dict[str, Any]:
    queue = await list_case_queue(session, organization_id=organization_id, limit=limit)
    processed: List[Dict[str, Any]] = []
    for item in queue:
        try:
            result = await run_case(
                session,
                organization_id=organization_id,
                case_id=UUID(str(item["id"])),
            )
            processed.append(
                {
                    "case": result.get("case"),
                    "decision": result.get("decision", {}),
                    "execution_result": result.get("execution_result", {}),
                    "supervision": result.get("supervision", {}),
                }
            )
        except Exception as exc:
            processed.append(
                {
                    "case": item,
                    "decision": {},
                    "execution_result": {},
                    "supervision": {"action": "escalate", "reason": str(exc)},
                }
            )
    remaining = await list_case_queue(session, organization_id=organization_id, limit=100)
    return {
        "processed": processed,
        "processed_count": len(processed),
        "remaining_count": len(remaining),
        "remaining": remaining[: max(0, min(10, len(remaining)))],
    }


async def list_rollbackable_guardrails(
    session: AsyncSession,
    *,
    organization_id: UUID,
    limit: int = 25,
) -> List[Dict[str, Any]]:
    stmt = (
        select(AuditLog)
        .where(
            AuditLog.organization_id == organization_id,
            AuditLog.action.in_(["execution_guard.preflight", "execution_guard.paused", "execution_guard.tripwire"]),
        )
        .order_by(desc(AuditLog.created_at))
        .limit(max(1, min(limit, 100)))
    )
    rows = list((await session.execute(stmt)).scalars().all())
    items: List[Dict[str, Any]] = []
    for row in rows:
        metadata = dict(row.audit_metadata or {})
        if not isinstance(metadata.get("rollback_snapshot"), dict):
            continue
        items.append(
            {
                "audit_log_id": str(row.id),
                "action": row.action,
                "status": row.status,
                "created_at": row.created_at,
                "operation_type": metadata.get("operation_type"),
                "action_name": metadata.get("action_name"),
                "rollback_snapshot": metadata.get("rollback_snapshot"),
                "context_signature": metadata.get("context_signature"),
            }
        )
    return items


async def run_case(
    session: AsyncSession,
    *,
    organization_id: UUID,
    case_id: UUID,
) -> Dict[str, Any]:
    row = await session.get(AutonomousCase, case_id)
    if not row or row.organization_id != organization_id:
        raise ValueError("Case not found")

    lease_key = f"autonomous_case:{row.id}"
    owner = f"case-manager:{uuid4().hex[:8]}"
    if not await acquire_case_lease(
        session,
        organization_id=organization_id,
        case_id=row.id,
        lease_key=lease_key,
        owner=owner,
    ):
        return {
            "case": _case_read(row),
            "status": "waiting",
            "reason": "lease_not_available",
        }

    try:
        row.status = "in_progress"
        await session.commit()

        business_state = await get_latest_business_state(session, organization_id=organization_id)
        resolved_case_type = classify_case_type(
            current_goal=row.current_goal,
            business_context=dict(row.business_context or {}),
            business_state=business_state,
            requested_case_type=row.case_type,
            allow_generic=True,
        ) or row.case_type
        if resolved_case_type != row.case_type:
            row.case_type = resolved_case_type
            await session.commit()
        business_memory = await get_business_memory(session, organization_id=organization_id)
        recent_history = await list_operation_memory(session, organization_id=organization_id, case_id=row.id, limit=5)
        world_state = await load_world_state(session, organization_id=organization_id, case_id=row.id)
        progress = await load_case_progress(session, organization_id=organization_id, case_id=row.id)
        rollbackable = await _find_latest_rollbackable_action(
            session,
            organization_id=organization_id,
            case_type=row.case_type,
        )
        if rollbackable:
            world_state["rollbackable_action"] = rollbackable
        world_state["business_memory"] = business_memory
        world_state["case_progress"] = progress
        await save_world_state(session, organization_id=organization_id, case_id=row.id, world_state=world_state)

        decision = decide_next_step(
            DecisionContext(
                organization_id=organization_id,
                case_id=row.id,
                objective_kind=row.case_type,
                business_state=business_state,
                world_state=world_state,
                business_context=dict(row.business_context or {}),
                recent_history=recent_history,
            )
        ).model_dump()
        decision = _apply_eval_harness_decision_overrides(
            decision=decision,
            business_context=dict(row.business_context or {}),
        )
        if anthropic_agentic_enabled():
            should_use_anthropic, activation_reason = _should_use_anthropic_runtime(
                decision=decision,
                rollbackable=rollbackable,
            )
            if not should_use_anthropic:
                decision["agentic"] = {
                    "activated": False,
                    "activation_reason": activation_reason,
                }
            else:
                try:
                    runtime = AnthropicAgentRuntime()
                    agentic_meta = await runtime.refine_case_decision(
                        case_type=row.case_type,
                        current_goal=row.current_goal,
                        business_context=dict(row.business_context or {}),
                        candidate_decision=decision,
                        session=session,
                        organization_id=organization_id,
                    )
                    decision["agentic"] = {
                        **agentic_meta,
                        "activated": True,
                        "activation_reason": activation_reason,
                    }
                    if agentic_meta.get("applied_skills"):
                        decision["execution_payload"] = {
                            **dict(decision.get("execution_payload") or {}),
                            "applied_skills": list(agentic_meta.get("applied_skills") or []),
                        }
                except Exception as exc:
                    decision["agentic"] = {
                        "activated": False,
                        "activation_reason": "anthropic_failed",
                        "error": str(exc)[:240],
                    }

        step_hash = build_step_hash(decision)
        idempotency_key = build_idempotency_key(case_id=row.id, step_payload=decision)
        existing = await get_idempotent_result(
            session,
            organization_id=organization_id,
            idempotency_key=idempotency_key,
        )
        if existing:
            row.last_decision = decision
            row.last_execution_result = existing
            row.resolution_summary = {"status": "reused_idempotent_result"}
            row.status = "completed" if bool(existing.get("completed")) else "waiting"
            await session.commit()
            return {
                "case": _case_read(row),
                "decision": decision,
                "execution_result": existing,
                "outcome_evaluation": {},
                "supervision": {"action": "close_case" if row.status == "completed" else "replan", "reason": "idempotent_reuse"},
            }

        microagent_payload: Dict[str, Any] | None = None
        if row.case_type in {
            "payment_followthrough",
            "collections_followthrough",
            "onboarding_execution",
            "campaign_execution",
            "sales_execution",
        }:
            from brain.agents.master_brain_router import microagents_enabled, route_case_via_master_brain

            if microagents_enabled():
                microagent_payload = await route_case_via_master_brain(
                    session,
                    organization_id=organization_id,
                    case_id=row.id,
                    current_goal=row.current_goal,
                    business_context=dict(row.business_context or {}),
                    business_state=business_state,
                    decision_hint=decision,
                    rollbackable=rollbackable,
                )
                decision["microagent"] = {
                    "activated": True,
                    "agent": microagent_payload.get("agent_name"),
                    "handoff": microagent_payload.get("handoff"),
                }
                execution_result = dict(microagent_payload.get("execution_result") or {})
                outcome = dict(microagent_payload.get("outcome_evaluation") or {})
                supervision = dict(microagent_payload.get("supervision") or {})
            else:
                execution_result = await route_execution(
                    session,
                    organization_id=organization_id,
                    step=decision,
                )
                outcome = evaluate_outcome(
                    decision=decision,
                    execution_result=execution_result,
                    business_state=business_state,
                )
                supervision = decide_supervision_action(
                    decision=decision,
                    execution_result=execution_result,
                    outcome_evaluation=outcome,
                )
        else:
            execution_result = await route_execution(
                session,
                organization_id=organization_id,
                step=decision,
            )
            outcome = evaluate_outcome(
                decision=decision,
                execution_result=execution_result,
                business_state=business_state,
            )
            supervision = decide_supervision_action(
                decision=decision,
                execution_result=execution_result,
                outcome_evaluation=outcome,
            )
        eval_context = dict(row.business_context or {})
        if (
            os.getenv("EVAL_HARNESS_SAFE_RUNTIME", "false").lower() in {"1", "true", "yes"}
            and (bool(eval_context.get("eval_force_replan")) or bool(eval_context.get("eval_force_compensation")))
        ):
            execution_result = {
                **execution_result,
                "status": "failed",
                "completed": False,
                "confirmed": False,
                "dispatched": False,
                "forced_eval_failure": True,
            }
            outcome = evaluate_outcome(
                decision=decision,
                execution_result=execution_result,
                business_state=business_state,
            )
            supervision = decide_supervision_action(
                decision=decision,
                execution_result=execution_result,
                outcome_evaluation=outcome,
            )
        outcome = _apply_eval_harness_outcome_overrides(
            outcome=outcome,
            business_context=eval_context,
        )
        if bool(eval_context.get("eval_force_replan")) or bool(eval_context.get("eval_force_compensation")):
            supervision = decide_supervision_action(
                decision=decision,
                execution_result=execution_result,
                outcome_evaluation=outcome,
            )
        if supervision["action"] == "switch_tool" and supervision.get("next_tool"):
            decision["recommended_tool"] = supervision["next_tool"]
            decision["fallback_tools"] = [tool for tool in decision.get("fallback_tools", []) if tool != supervision["next_tool"]]
            execution_result = await route_execution(
                session,
                organization_id=organization_id,
                step=decision,
            )
            outcome = evaluate_outcome(
                decision=decision,
                execution_result=execution_result,
                business_state=business_state,
            )
            supervision = decide_supervision_action(
                decision=decision,
                execution_result=execution_result,
                outcome_evaluation=outcome,
            )
        if supervision["action"] == "rollback_and_compensate":
            rollbackable_meta = rollbackable.get("metadata") if isinstance(rollbackable, dict) else None
            if isinstance(rollbackable_meta, dict):
                try:
                    rollback_result = await execute_rollback_from_metadata(
                        session,
                        organization_id=organization_id,
                        metadata=rollbackable_meta,
                    )
                    execution_result = {
                        **execution_result,
                        "rollback_attempted": True,
                        "rollback_result": rollback_result,
                        "compensated": str(rollback_result.get("status")) in {"restored", "dispatched"},
                    }
                    outcome = evaluate_outcome(
                        decision=decision,
                        execution_result={**execution_result, "status": "completed" if execution_result.get("compensated") else "failed"},
                        business_state=business_state,
                    )
                    supervision = decide_supervision_action(
                        decision=decision,
                        execution_result=execution_result,
                        outcome_evaluation=outcome,
                    )
                except Exception:
                    if os.getenv("EVAL_COMPENSATION_TRACEBACK", "false").lower() in {"1", "true", "yes"}:
                        print("COMPENSATION_TRACEBACK_START")
                        print(traceback.format_exc())
                        print("COMPENSATION_TRACEBACK_END")
                    raise

        step_index = len(await list_case_steps(session, organization_id=organization_id, case_id=row.id)) + 1
        step_row = AutonomousCaseStep(
            case_id=row.id,
            organization_id=organization_id,
            step_index=step_index,
            status=str(supervision.get("action") or "planned"),
            decision_payload=decision,
            execution_payload=dict(decision.get("execution_payload") or {}),
            execution_result=execution_result,
            outcome_evaluation={**outcome, "supervision": supervision},
        )
        session.add(step_row)
        row.priority_score = float(decision.get("priority_score") or 0.0)
        row.risk_score = float(decision.get("risk_score") or 0.0)
        row.last_decision = decision
        row.last_execution_result = execution_result

        final_status = "in_progress"
        if supervision["action"] == "close_case":
            final_status = "completed"
        elif supervision["action"] in {"replan", "switch_tool"}:
            final_status = "waiting"
        elif supervision["action"] == "rollback_and_compensate":
            final_status = "failed"
        elif supervision["action"] == "escalate":
            final_status = "escalated"
        row.status = final_status
        row.resolution_summary = {
            "supervision": supervision,
            "outcome_reason": outcome.get("reason"),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        await session.commit()
        await session.refresh(step_row)
        await session.refresh(row)

        await save_case_progress(
            session,
            organization_id=organization_id,
            case_id=row.id,
            payload={
                "current_goal": row.current_goal,
                "active_domain": row.case_type,
                "last_completed_step": step_index,
                "next_expected_step": str(supervision.get("action") or ""),
                "last_successful_tool": str(execution_result.get("selected_tool") or ""),
                "last_failure": {} if bool(outcome.get("matches_expected")) else {"reason": outcome.get("reason"), "status": execution_result.get("status")},
                "retry_count_by_signature": {},
                "rollback_options": [rollbackable] if rollbackable else [],
                "open_questions": [] if bool(outcome.get("matches_expected")) else [str(outcome.get("reason") or "")],
                "artifacts_created": [str(step_row.id)],
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
            success=bool(outcome.get("matches_expected")),
        )

        await record_operation_memory(
            session,
            organization_id=organization_id,
            case_id=row.id,
            memory_type="case_step",
            signature=step_hash,
            payload={
                "decision": decision,
                "execution_result": execution_result,
                "outcome": outcome,
                "supervision": supervision,
            },
            success=bool(outcome.get("matches_expected")),
        )
        for skill_name in list(dict(decision.get("execution_payload") or {}).get("applied_skills") or []):
            await record_skill_feedback(
                session,
                organization_id=organization_id,
                case_id=row.id,
                skill_name=str(skill_name),
                helpful=bool(outcome.get("matches_expected")),
                note=str(outcome.get("reason") or ""),
                metadata={"case_type": row.case_type},
            )
        await store_idempotent_result(
            session,
            organization_id=organization_id,
            case_id=row.id,
            idempotency_key=idempotency_key,
            step_hash=step_hash,
            result_payload=execution_result,
        )

        return {
            "case": _case_read(row),
            "decision": decision,
            "execution_result": execution_result,
            "outcome_evaluation": outcome,
            "supervision": supervision,
            "step": _step_read(step_row),
        }
    finally:
        await release_case_lease(
            session,
            organization_id=organization_id,
            lease_key=lease_key,
            owner=owner,
        )
