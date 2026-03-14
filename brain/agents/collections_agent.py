from __future__ import annotations

import os
import traceback
from typing import Any, Dict
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from brain.anthropic_agent_runtime import AnthropicAgentRuntime, anthropic_agentic_enabled
from brain.mcp_runtime import execute_tool
from brain.skill_loader import load_skill

AGENT_MODEL = "claude-haiku-4-5-20251001"
THINKING_BUDGET = 5000
SKILL_NAME = "collections_followthrough"


async def _heuristic_collections_agent(
    session: AsyncSession,
    *,
    organization_id: UUID,
    handoff: Dict[str, Any],
    skill_summary: str,
) -> Dict[str, Any]:
    context = dict(handoff.get("context") or {})
    risk_score = float(handoff.get("risk_score") or 0.0)
    debt_total = float(context.get("deuda_total") or context.get("debt_total") or 0.0)
    if debt_total >= 100000 or risk_score >= 0.9:
        return {
            "agent": "collections_agent",
            "model": AGENT_MODEL,
            "thinking_budget": THINKING_BUDGET,
            "skill": SKILL_NAME,
            "skill_summary": skill_summary,
            "status": "escalated",
            "actions_taken": ["legal_escalation"],
            "compensation_attempted": False,
        }

    strategy = "payment_plan" if debt_total >= 5000 or "descuento" in str(context.get("ultima_respuesta") or "").lower() else "debt_followup"
    tool_result = await execute_tool(
        "execute_case_action",
        args={
            "step": {
                "recommended_action": "run_collections_followthrough",
                "recommended_tool": "internal_service",
                "fallback_tools": ["http", "n8n"],
                "execution_payload": {
                    "source": "collections_agent",
                    "strategy": strategy,
                    "action": {"action": "run_collections_followthrough", "arguments": context},
                },
            }
        },
        session=session,
        organization_id=organization_id,
    )
    return {
        "agent": "collections_agent",
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


async def run_collections_agent(
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
                "Resuelve un caso de collections followthrough. "
                "Decide entre debt_followup, payment_plan o legal_escalation. "
                "Usa solo herramientas permitidas.\n"
                f"Goal: {handoff.get('goal')}\n"
                f"Risk: {handoff.get('risk_score')}\n"
                f"Skill: {skill_summary}\n"
                f"Context: {context}\n"
            )
            agentic = await runtime.run_tool_loop(
                system_prompt="Eres collections_agent. Decide y ejecuta dentro de cobranza sin invocar otros agentes.",
                user_message=prompt,
                domain="collections_followthrough",
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
                    "agent": "collections_agent",
                    "model": AGENT_MODEL,
                    "thinking_budget": THINKING_BUDGET,
                    "skill": SKILL_NAME,
                    "skill_summary": skill_summary,
                    "status": "completed" if bool(tool_result.get("completed")) else "waiting",
                    "actions_taken": ["agentic_collections_strategy", f"tool:{tool_name}"],
                    "compensation_attempted": False,
                    "tool_result": tool_result,
                    "selected_tool": tool_name,
                    "agentic_note": str(agentic.get("final_answer") or ""),
                    "usage": dict(agentic.get("usage") or {}),
                }
        except Exception:
            if os.getenv("COLLECTIONS_AGENT_TRACEBACK", "false").lower() in {"1", "true", "yes"}:
                print("COLLECTIONS_AGENT_TRACEBACK_START")
                print(traceback.format_exc())
                print("COLLECTIONS_AGENT_TRACEBACK_END")
    return await _heuristic_collections_agent(
        session,
        organization_id=organization_id,
        handoff=handoff,
        skill_summary=skill_summary,
    )
