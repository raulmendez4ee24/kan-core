from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List
from uuid import UUID

from brain.business_priority_model import derive_case_priority
from brain.skill_registry import match_skills


@dataclass
class DecisionStep:
    action: str
    tool: str
    fallback_tools: List[str] = field(default_factory=list)
    expected_outcome: str = ""
    execution_payload: Dict[str, Any] = field(default_factory=dict)
    requires_replan_if_failed: bool = True


@dataclass
class DecisionContext:
    organization_id: UUID
    case_id: UUID
    objective_kind: str
    business_state: Dict[str, Any]
    world_state: Dict[str, Any]
    business_context: Dict[str, Any]
    recent_history: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class DecisionEnvelope:
    organization_id: str
    case_id: str
    objective_kind: str
    priority_score: float
    risk_score: float
    recommended_action: str
    recommended_tool: str
    fallback_tools: List[str]
    reasoning_summary: str
    expected_outcome: str
    requires_replan_if_failed: bool
    execution_payload: Dict[str, Any] = field(default_factory=dict)

    def model_dump(self) -> Dict[str, Any]:
        return asdict(self)


def _rollback_world_state(world_state: Dict[str, Any]) -> Dict[str, Any]:
    candidate = world_state.get("rollbackable_action")
    return dict(candidate) if isinstance(candidate, dict) else {}


def _prefer_non_n8n(business_state: Dict[str, Any]) -> bool:
    critical_risks = {str(item).strip().lower() for item in (business_state.get("critical_risks") or [])}
    ops_health = float(business_state.get("ops_health_score") or 0.0)
    return bool({"integration_error_rate", "integration_failures", "workflow_degradation"} & critical_risks) or ops_health < 0.55


def _resolve_case_type(ctx: DecisionContext) -> str:
    case_type = str(ctx.objective_kind or "").strip().lower()
    recommended_focus = str(ctx.business_state.get("recommended_focus") or "").strip().lower()
    if case_type == "ops_anomaly_response" and recommended_focus in {
        "payment_followthrough",
        "customer_recovery",
        "workflow_recovery",
        "campaign_execution",
    }:
        return recommended_focus
    return case_type


def decide_next_step(ctx: DecisionContext) -> DecisionEnvelope:
    case_type = _resolve_case_type(ctx)
    scores = derive_case_priority(
        case_type=case_type,
        business_state=ctx.business_state,
        business_context=ctx.business_context,
    )
    rollbackable = _rollback_world_state(ctx.world_state)
    prefer_non_n8n = _prefer_non_n8n(ctx.business_state)

    if case_type == "customer_recovery" and rollbackable:
        step = DecisionStep(
            action="restore_customer",
            tool="db_internal",
            fallback_tools=["n8n"],
            expected_outcome="customer_restored",
            execution_payload={"rollback_metadata": rollbackable},
        )
        reasoning = "Se detecto una accion reversible reciente; se prioriza restaurar al cliente."
    elif case_type == "payment_followthrough" and rollbackable and str(rollbackable.get("operation_type") or "") == "payment":
        step = DecisionStep(
            action="refund_payment",
            tool="internal_service" if prefer_non_n8n else "n8n",
            fallback_tools=(["http", "n8n"] if prefer_non_n8n else ["internal_service", "http"]),
            expected_outcome="payment_compensated",
            execution_payload={"rollback_metadata": rollbackable},
        )
        reasoning = "Existe una accion de pago rollbackable; se prioriza compensacion."
    elif case_type == "payment_followthrough":
        step = DecisionStep(
            action="payment_followup",
            tool="internal_service" if prefer_non_n8n else "n8n",
            fallback_tools=(["http", "n8n"] if prefer_non_n8n else ["internal_service", "http"]),
            expected_outcome="payment_followup_dispatched",
            execution_payload={
                "source": "autonomous_case_manager",
                "action": {"action": "payment_followup", "arguments": ctx.business_context},
            },
        )
        reasoning = "Se prioriza seguimiento de cobro segun exposicion financiera actual."
    elif case_type == "workflow_recovery":
        step = DecisionStep(
            action="recover_workflow",
            tool="internal_service",
            fallback_tools=["http", "n8n"],
            expected_outcome="workflow_recovered",
            execution_payload={
                "source": "autonomous_case_manager",
                "action": {"action": "workflow_recovery", "arguments": ctx.business_context},
                "business_state": ctx.business_state,
            },
        )
        reasoning = "La salud operativa sugiere degradacion de workflows; se prioriza recuperacion del pipeline."
    elif case_type == "campaign_execution":
        step = DecisionStep(
            action="execute_campaign",
            tool="internal_service" if prefer_non_n8n else "n8n",
            fallback_tools=(["http", "n8n"] if prefer_non_n8n else ["internal_service", "http"]),
            expected_outcome="campaign_queued",
            execution_payload={
                "source": "autonomous_case_manager",
                "action": {"action": "execute_campaign", "arguments": ctx.business_context},
                "business_state": ctx.business_state,
            },
        )
        reasoning = "La prioridad del negocio permite avanzar una campana sin esperar intervencion manual."
    elif case_type == "sales_execution":
        step = DecisionStep(
            action="advance_sales_pipeline",
            tool="internal_service",
            fallback_tools=["http", "n8n"],
            expected_outcome="sales_progressed",
            execution_payload={
                "source": "autonomous_case_manager",
                "action": {"action": "advance_sales_pipeline", "arguments": ctx.business_context},
                "business_state": ctx.business_state,
            },
        )
        reasoning = "Se prioriza avanzar pipeline comercial con base en contexto y oportunidad activa."
    elif case_type == "support_resolution":
        step = DecisionStep(
            action="resolve_support_case",
            tool="internal_service",
            fallback_tools=["http", "n8n"],
            expected_outcome="support_case_updated",
            execution_payload={
                "source": "autonomous_case_manager",
                "action": {"action": "resolve_support_case", "arguments": ctx.business_context},
                "business_state": ctx.business_state,
            },
        )
        reasoning = "Se detecta un flujo de soporte; se actualiza el caso y se prepara siguiente accion."
    elif case_type == "onboarding_execution":
        step = DecisionStep(
            action="process_onboarding",
            tool="internal_service" if prefer_non_n8n else "n8n",
            fallback_tools=(["http", "n8n"] if prefer_non_n8n else ["internal_service", "http"]),
            expected_outcome="onboarding_queued",
            execution_payload={
                "source": "autonomous_case_manager",
                "action": {"action": "process_onboarding", "arguments": ctx.business_context},
                "business_state": ctx.business_state,
            },
        )
        reasoning = "Se prioriza intake y preparacion de onboarding como caso persistente."
    elif case_type == "collections_followthrough":
        step = DecisionStep(
            action="run_collections_followthrough",
            tool="internal_service",
            fallback_tools=["http", "n8n"],
            expected_outcome="collections_action_queued",
            execution_payload={
                "source": "autonomous_case_manager",
                "action": {"action": "run_collections_followthrough", "arguments": ctx.business_context},
                "business_state": ctx.business_state,
            },
        )
        reasoning = "Se detecta cobranza pendiente; se agenda seguimiento de collections."
    elif case_type == "ops_anomaly_response":
        step = DecisionStep(
            action="stabilize_ops",
            tool="internal_service",
            fallback_tools=["http", "n8n"],
            expected_outcome="ops_response_recorded",
            execution_payload={"business_state": ctx.business_state, "world_state": ctx.world_state},
        )
        reasoning = "Se detecta una anomalia operativa; se prioriza estabilizacion y trazabilidad."
    else:
        step = DecisionStep(
            action="observe_case",
            tool="internal_service",
            fallback_tools=["db_internal"],
            expected_outcome="case_observed",
            execution_payload={"business_context": ctx.business_context, "world_state": ctx.world_state},
            requires_replan_if_failed=False,
        )
        reasoning = "No hay accion especializada; se registra observacion y se conserva el caso."

    if os.getenv("ENABLE_LOCAL_AGENT_SKILLS", "true").lower() in {"1", "true", "yes"}:
        applied_skills = match_skills(case_type=case_type, business_context=ctx.business_context)
        if applied_skills:
            skill_names = [item["name"] for item in applied_skills]
            step.execution_payload = {
                **step.execution_payload,
                "applied_skills": skill_names,
                "skill_summaries": {
                    item["name"]: item["summary"]
                    for item in applied_skills
                },
            }
            reasoning = f"{reasoning} Skills aplicados: {', '.join(skill_names)}."

    return DecisionEnvelope(
        organization_id=str(ctx.organization_id),
        case_id=str(ctx.case_id),
        objective_kind=case_type,
        priority_score=scores["priority_score"],
        risk_score=scores["risk_score"],
        recommended_action=step.action,
        recommended_tool=step.tool,
        fallback_tools=step.fallback_tools,
        reasoning_summary=reasoning,
        expected_outcome=step.expected_outcome,
        requires_replan_if_failed=step.requires_replan_if_failed,
        execution_payload=step.execution_payload,
    )
