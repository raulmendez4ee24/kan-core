from __future__ import annotations

from typing import Any, Dict
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from brain.anthropic_agent_runtime import AnthropicAgentRuntime, anthropic_agentic_enabled
from brain.mcp_runtime import execute_tool
from brain.skill_loader import load_skill

AGENT_MODEL = "claude-haiku-4-5-20251001"
THINKING_BUDGET = 6000
SKILL_NAME = "payment_recovery"


def _allowed(handoff: Dict[str, Any], tool_name: str) -> bool:
    return tool_name in set(handoff.get("allowed_tools") or [])


async def _heuristic_payment_agent(
    session: AsyncSession,
    *,
    organization_id: UUID,
    handoff: Dict[str, Any],
    skill_summary: str,
) -> Dict[str, Any]:
    context = dict(handoff.get("context") or {})
    risk_score = float(handoff.get("risk_score") or 0.0)
    rollback_metadata = context.get("rollback_metadata")
    if isinstance(rollback_metadata, dict):
        if _allowed(handoff, "run_rollback"):
            rollback_result = await execute_tool(
                "run_rollback",
                args={"metadata": rollback_metadata},
                session=session,
                organization_id=organization_id,
            )
            rollback_status = str(rollback_result.get("status") or "").strip().lower()
            return {
                "agent": "payment_agent",
                "model": AGENT_MODEL,
                "thinking_budget": THINKING_BUDGET,
                "skill": SKILL_NAME,
                "skill_summary": skill_summary,
                "status": "completed" if rollback_status in {"restored", "dispatched"} else "failed",
                "actions_taken": ["compensation", "tool:run_rollback"],
                "compensation_attempted": True,
                "tool_result": rollback_result,
                "selected_tool": "run_rollback",
            }
        return {
            "agent": "payment_agent",
            "model": AGENT_MODEL,
            "thinking_budget": THINKING_BUDGET,
            "skill": SKILL_NAME,
            "skill_summary": skill_summary,
            "status": "escalated",
            "actions_taken": ["compensation_unavailable"],
            "compensation_attempted": False,
        }

    strategy = "negotiation" if risk_score > 0.8 or float(context.get("dias_vencido") or 0) >= 30 else "retry_charge"
    tool_result = await execute_tool(
        "execute_case_action",
        args={
            "step": {
                "recommended_action": "payment_followup",
                "recommended_tool": "internal_service",
                "fallback_tools": ["http", "n8n"],
                "execution_payload": {
                    "source": "payment_agent",
                    "strategy": strategy,
                    "action": {"action": "payment_followup", "arguments": context},
                },
            }
        },
        session=session,
        organization_id=organization_id,
    )
    return {
        "agent": "payment_agent",
        "model": AGENT_MODEL,
        "thinking_budget": THINKING_BUDGET,
        "skill": SKILL_NAME,
        "skill_summary": skill_summary,
        "status": "completed" if bool(tool_result.get("completed")) else "waiting",
        "actions_taken": [strategy, "tool:execute_case_action"],
        "compensation_attempted": False,
        "tool_result": tool_result,
        "selected_tool": str(tool_result.get("selected_tool") or "execute_case_action"),
    }


async def run_payment_agent(
    session: AsyncSession,
    *,
    organization_id: UUID,
    handoff: Dict[str, Any],
) -> Dict[str, Any]:
    context = dict(handoff.get("context") or {})
    risk_score = float(handoff.get("risk_score") or 0.0)
    loaded_skill = load_skill(SKILL_NAME) or {"summary": ""}
    skill_summary = str(loaded_skill.get("summary") or "")
    should_use_agentic = anthropic_agentic_enabled() and risk_score >= 0.7
    if should_use_agentic:
        runtime = AnthropicAgentRuntime()
        try:
            prompt = (
                "Resuelve un caso de payment followthrough. "
                "Usa solo herramientas permitidas. "
                "Si hay rollback_metadata, prioriza compensacion. "
                "Si no, decide entre retry_charge y negotiation.\n"
                f"Goal: {handoff.get('goal')}\n"
                f"Risk: {handoff.get('risk_score')}\n"
                f"Skill: {skill_summary}\n"
                f"Context: {context}\n"
            )
            agentic = await runtime.run_tool_loop(
                system_prompt="Eres payment_agent. Decide y ejecuta dentro de tu dominio sin invocar otros agentes.",
                user_message=prompt,
                domain="payment_followthrough",
                session=session,
                organization_id=organization_id,
                tool_names=list(handoff.get("allowed_tools") or []),
                max_rounds=2,
                thinking_budget_override=THINKING_BUDGET,
            )
            executed_tools = list(agentic.get("executed_tools") or [])
            if executed_tools:
                last = dict(executed_tools[-1] or {})
                tool_name = str(last.get("tool") or "unknown")
                tool_result = dict(last.get("result") or {})
                if tool_name == "run_rollback":
                    rollback_status = str(tool_result.get("status") or "").strip().lower()
                    return {
                        "agent": "payment_agent",
                        "model": AGENT_MODEL,
                        "thinking_budget": THINKING_BUDGET,
                        "skill": SKILL_NAME,
                        "skill_summary": skill_summary,
                        "status": "completed" if rollback_status in {"restored", "dispatched"} else "failed",
                        "actions_taken": ["agentic_compensation", "tool:run_rollback"],
                        "compensation_attempted": True,
                        "tool_result": tool_result,
                        "selected_tool": "run_rollback",
                        "agentic_note": str(agentic.get("final_answer") or ""),
                        "usage": dict(agentic.get("usage") or {}),
                    }
                return {
                    "agent": "payment_agent",
                    "model": AGENT_MODEL,
                    "thinking_budget": THINKING_BUDGET,
                    "skill": SKILL_NAME,
                    "skill_summary": skill_summary,
                    "status": "completed" if bool(tool_result.get("completed")) else "waiting",
                    "actions_taken": ["agentic_payment_strategy", f"tool:{tool_name}"],
                    "compensation_attempted": False,
                    "tool_result": tool_result,
                    "selected_tool": tool_name,
                    "agentic_note": str(agentic.get("final_answer") or ""),
                    "usage": dict(agentic.get("usage") or {}),
                }
        except Exception:
            pass
    return await _heuristic_payment_agent(
        session,
        organization_id=organization_id,
        handoff=handoff,
        skill_summary=skill_summary,
    )
