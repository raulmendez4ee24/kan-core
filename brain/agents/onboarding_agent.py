from __future__ import annotations

from typing import Any, Dict
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from brain.anthropic_agent_runtime import AnthropicAgentRuntime, anthropic_agentic_enabled
from brain.mcp_runtime import execute_tool
from brain.skill_loader import load_skill

AGENT_MODEL = "claude-haiku-4-5-20251001"
THINKING_BUDGET = 4000
SKILL_NAME = "onboarding_execution"


async def _heuristic_onboarding_agent(
    session: AsyncSession,
    *,
    organization_id: UUID,
    handoff: Dict[str, Any],
    skill_summary: str,
) -> Dict[str, Any]:
    context = dict(handoff.get("context") or {})
    risk_score = float(handoff.get("risk_score") or 0.0)
    blockers = (
        bool(context.get("bloqueador_actual"))
        or bool(context.get("dependencias_criticas"))
        or bool(context.get("integraciones_requeridas"))
    )
    if blockers and risk_score >= 0.85:
        return {
            "agent": "onboarding_agent",
            "model": AGENT_MODEL,
            "thinking_budget": THINKING_BUDGET,
            "skill": SKILL_NAME,
            "skill_summary": skill_summary,
            "status": "waiting",
            "actions_taken": ["resolve_blockers", "handoff_back_to_master_brain"],
            "compensation_attempted": False,
        }

    strategy = "resolve_blockers" if blockers else "activate_features"
    tool_result = await execute_tool(
        "execute_case_action",
        args={
            "step": {
                "recommended_action": "process_onboarding",
                "recommended_tool": "internal_service",
                "fallback_tools": ["http", "n8n"],
                "execution_payload": {
                    "source": "onboarding_agent",
                    "strategy": strategy,
                    "action": {"action": "process_onboarding", "arguments": context},
                },
            }
        },
        session=session,
        organization_id=organization_id,
    )
    return {
        "agent": "onboarding_agent",
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


async def run_onboarding_agent(
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
                "Resuelve un caso de onboarding execution. "
                "Decide entre activate_features o resolve_blockers. "
                "Usa solo herramientas permitidas.\n"
                f"Goal: {handoff.get('goal')}\n"
                f"Risk: {handoff.get('risk_score')}\n"
                f"Skill: {skill_summary}\n"
                f"Context: {context}\n"
            )
            agentic = await runtime.run_tool_loop(
                system_prompt="Eres onboarding_agent. Decide y ejecuta dentro de onboarding sin invocar otros agentes.",
                user_message=prompt,
                domain="onboarding_execution",
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
                return {
                    "agent": "onboarding_agent",
                    "model": AGENT_MODEL,
                    "thinking_budget": THINKING_BUDGET,
                    "skill": SKILL_NAME,
                    "skill_summary": skill_summary,
                    "status": "completed" if bool(tool_result.get("completed")) else "waiting",
                    "actions_taken": ["agentic_onboarding_strategy", f"tool:{tool_name}"],
                    "compensation_attempted": False,
                    "tool_result": tool_result,
                    "selected_tool": tool_name,
                    "agentic_note": str(agentic.get("final_answer") or ""),
                    "usage": dict(agentic.get("usage") or {}),
                }
        except Exception:
            pass
    return await _heuristic_onboarding_agent(
        session,
        organization_id=organization_id,
        handoff=handoff,
        skill_summary=skill_summary,
    )
