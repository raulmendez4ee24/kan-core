from __future__ import annotations

import os
from typing import Any, Dict, List
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from brain.agents.agent_governor import evaluate_handoff, evaluate_result
from brain.business_priority_model import derive_case_priority

SUPPORTED_DOMAINS = {
    "payment_followthrough",
    "collections_followthrough",
    "onboarding_execution",
    "campaign_execution",
    "sales_execution",
}

_DOMAIN_SKILL = {
    "payment_followthrough": "payment_recovery",
    "collections_followthrough": "collections_followthrough",
    "onboarding_execution": "onboarding_execution",
    "campaign_execution": "meta_ads_growth",
    "sales_execution": "sales_execution",
}

_DOMAIN_ALLOWED_TOOLS = {
    "payment_followthrough": ["execute_case_action", "run_rollback"],
    "collections_followthrough": ["execute_case_action"],
    "onboarding_execution": ["execute_case_action"],
    "campaign_execution": ["execute_case_action"],
    "sales_execution": ["execute_case_action"],
}

_DOMAIN_CONTEXT_KEYS = {
    "payment_followthrough": {
        "cliente_tipo",
        "plan",
        "monto_mensual",
        "monto_factura",
        "dias_vencido",
        "intentos_cobro_previos",
        "metodo_pago",
        "historial_pagos",
        "ultimo_error_cobro",
        "promesas_pago_incumplidas",
        "high_impact_operation",
    },
    "collections_followthrough": {
        "cliente_tipo",
        "deuda_total",
        "deuda",
        "meses_sin_pagar",
        "facturas_pendientes",
        "dias_promedio_vencimiento",
        "ultima_respuesta",
        "situacion_empresa_cliente",
        "high_impact_operation",
    },
    "onboarding_execution": {
        "cliente_tipo",
        "plan",
        "dias_desde_registro",
        "features_activadas",
        "features_pendientes",
        "integraciones_requeridas",
        "bloqueador_actual",
        "deadline_go_live",
        "dependencias_criticas",
        "high_impact_operation",
    },
    "campaign_execution": {
        "campaign_name",
        "channel",
        "budget_usd",
        "daily_spend_usd",
        "ctr_drop_pct",
        "roas",
        "audience_size",
        "creative_fatigue",
        "high_impact_operation",
    },
    "sales_execution": {
        "lead_score",
        "deal_value",
        "deal_stage",
        "industry",
        "last_contact_days",
        "decision_maker_engaged",
        "next_step_blocker",
        "high_impact_operation",
    },
}


def microagents_enabled() -> bool:
    return os.getenv("ENABLE_MICROAGENTS", "false").lower() in {"1", "true", "yes"}


def _classify_domain(*, current_goal: str, business_context: Dict[str, Any]) -> str:
    from brain.autonomous_case_manager import classify_case_type

    classified = classify_case_type(
        current_goal=current_goal,
        business_context=business_context,
        requested_case_type=None,
        allow_generic=False,
    )
    token = str(classified or "").strip().lower()
    if token in SUPPORTED_DOMAINS:
        return token
    lowered_goal = str(current_goal or "").lower()
    if "campaign" in lowered_goal or "marketing" in lowered_goal:
        return "campaign_execution"
    if "sales" in lowered_goal or "venta" in lowered_goal or "lead" in lowered_goal:
        return "sales_execution"
    if "collections" in lowered_goal or "cobranza" in lowered_goal:
        return "collections_followthrough"
    if "onboarding" in lowered_goal or "activar" in lowered_goal:
        return "onboarding_execution"
    return "payment_followthrough"


def _filtered_context(domain: str, business_context: Dict[str, Any], rollbackable: Dict[str, Any] | None) -> Dict[str, Any]:
    context = dict(business_context or {})
    allowed_keys = _DOMAIN_CONTEXT_KEYS.get(domain, set())
    filtered = {key: value for key, value in context.items() if key in allowed_keys}
    if rollbackable:
        filtered["rollback_metadata"] = dict(rollbackable.get("metadata") or rollbackable)
    return filtered


def _agent_runner_for(domain: str):
    if domain == "payment_followthrough":
        from brain.agents.payment_agent import run_payment_agent

        return run_payment_agent, "payment_agent"
    if domain == "collections_followthrough":
        from brain.agents.collections_agent import run_collections_agent

        return run_collections_agent, "collections_agent"
    if domain == "campaign_execution":
        from brain.agents.marketing_agent import run_marketing_agent

        return run_marketing_agent, "marketing_agent"
    if domain == "sales_execution":
        from brain.agents.sales_agent import run_sales_agent

        return run_sales_agent, "sales_agent"
    from brain.agents.onboarding_agent import run_onboarding_agent

    return run_onboarding_agent, "onboarding_agent"


def _normalize_microagent_result(
    *,
    agent_result: Dict[str, Any],
) -> Dict[str, Any]:
    status = str(agent_result.get("status") or "waiting").strip().lower()
    actions_taken = list(agent_result.get("actions_taken") or [])
    tool_result = dict(agent_result.get("tool_result") or {})
    execution_result = {
        **tool_result,
        "status": status,
        "completed": status == "completed",
        "selected_tool": str(agent_result.get("selected_tool") or tool_result.get("selected_tool") or "microagent"),
        "actions_taken": actions_taken,
        "agent": str(agent_result.get("agent") or "microagent"),
        "compensation_attempted": bool(agent_result.get("compensation_attempted")),
    }
    if status == "completed":
        outcome = {
            "matches_expected": True,
            "reason": "microagent_completed",
            "needs_replan": False,
            "can_rollback": False,
            "severity": "low",
        }
        supervision = {"action": "close_case", "reason": "microagent_completed"}
    elif status == "waiting":
        outcome = {
            "matches_expected": False,
            "reason": "microagent_replan",
            "needs_replan": True,
            "can_rollback": False,
            "severity": "medium",
        }
        supervision = {"action": "replan", "reason": "microagent_replan"}
    elif status == "escalated":
        outcome = {
            "matches_expected": False,
            "reason": "microagent_escalated",
            "needs_replan": False,
            "can_rollback": False,
            "severity": "high",
        }
        supervision = {"action": "escalate", "reason": "microagent_escalated"}
    elif bool(agent_result.get("compensation_attempted")):
        outcome = {
            "matches_expected": False,
            "reason": "microagent_compensation_failed",
            "needs_replan": True,
            "can_rollback": True,
            "severity": "high",
        }
        supervision = {"action": "rollback_and_compensate", "reason": "microagent_compensation_failed"}
    else:
        outcome = {
            "matches_expected": False,
            "reason": "microagent_failed",
            "needs_replan": True,
            "can_rollback": False,
            "severity": "medium",
        }
        supervision = {"action": "replan", "reason": "microagent_failed"}
    return {
        "execution_result": execution_result,
        "outcome_evaluation": outcome,
        "supervision": supervision,
    }


async def route_case_via_master_brain(
    session: AsyncSession,
    *,
    organization_id: UUID,
    case_id: UUID,
    current_goal: str,
    business_context: Dict[str, Any],
    business_state: Dict[str, Any],
    decision_hint: Dict[str, Any] | None = None,
    rollbackable: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    domain = _classify_domain(current_goal=current_goal, business_context=business_context)
    scores = derive_case_priority(
        case_type=domain,
        business_state=business_state,
        business_context=business_context,
    )
    risk_score = max(float(scores.get("risk_score") or 0.0), float((decision_hint or {}).get("risk_score") or 0.0))
    handoff = {
        "case_id": str(case_id),
        "domain": domain,
        "goal": str(current_goal or ""),
        "context": _filtered_context(domain, business_context, rollbackable),
        "risk_score": round(risk_score, 4),
        "skill": _DOMAIN_SKILL[domain],
        "allowed_tools": list(_DOMAIN_ALLOWED_TOOLS[domain]),
        "escalation_path": "master_brain",
    }
    runner, agent_name = _agent_runner_for(domain)
    governor_pre = evaluate_handoff(agent_name=agent_name, handoff=handoff)
    if not governor_pre.get("permitted", True):
        return {
            "handoff": handoff,
            "agent_name": agent_name,
            "agent_result": {
                "agent": "agent_governor",
                "status": "escalated",
                "actions_taken": ["governor_block"],
                "selected_tool": "agent_governor",
                "compensation_attempted": False,
            },
            "execution_result": {
                "status": "escalated",
                "completed": False,
                "selected_tool": "agent_governor",
                "actions_taken": ["governor_block"],
                "agent": "agent_governor",
                "compensation_attempted": False,
            },
            "outcome_evaluation": {
                "matches_expected": False,
                "reason": str(governor_pre.get("hold_reason") or "governor_block"),
                "needs_replan": False,
                "can_rollback": False,
                "severity": "high",
            },
            "supervision": {"action": "escalate", "reason": str(governor_pre.get("hold_reason") or "governor_block")},
            "governor": {"pre": governor_pre, "post": {}},
        }
    agent_result = await runner(
        session,
        organization_id=organization_id,
        handoff=handoff,
    )
    governor_post = evaluate_result(agent_name=agent_name, handoff=handoff, agent_result=agent_result)
    if governor_post.get("force_escalate"):
        agent_result = {
            **dict(agent_result or {}),
            "status": "escalated",
            "actions_taken": list(agent_result.get("actions_taken") or []) + ["governor_escalate"],
        }
    normalized = _normalize_microagent_result(agent_result=agent_result)
    return {
        "handoff": handoff,
        "agent_name": agent_name,
        "agent_result": agent_result,
        "governor": {"pre": governor_pre, "post": governor_post},
        **normalized,
    }
