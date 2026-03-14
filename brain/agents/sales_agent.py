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
THINKING_BUDGET = 4500
SKILL_NAME = "sales_execution"


async def _heuristic_sales_agent(
    session: AsyncSession,
    *,
    organization_id: UUID,
    handoff: Dict[str, Any],
    skill_summary: str,
) -> Dict[str, Any]:
    context = dict(handoff.get("context") or {})
    risk_score = float(handoff.get("risk_score") or 0.0)
    lead_score = float(context.get("lead_score") or 0.0)
    deal_value = float(context.get("deal_value") or 0.0)
    if deal_value >= 50000 or risk_score >= 0.9:
        strategy = "executive_followup"
    elif lead_score >= 70:
        strategy = "close_followup"
    else:
        strategy = "qualify_lead"

    tool_result = await execute_tool(
        "execute_case_action",
        args={
            "step": {
                "recommended_action": "run_sales_execution",
                "recommended_tool": "internal_service",
                "fallback_tools": ["http", "n8n"],
                "execution_payload": {
                    "source": "sales_agent",
                    "strategy": strategy,
                    "action": {"action": "run_sales_execution", "arguments": context},
                },
            }
        },
        session=session,
        organization_id=organization_id,
    )
    return {
        "agent": "sales_agent",
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


async def run_sales_agent(
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
                "Resuelve un caso de sales execution. "
                "Decide entre qualify_lead, close_followup o executive_followup. "
                "Usa solo herramientas permitidas.\n"
                f"Goal: {handoff.get('goal')}\n"
                f"Risk: {handoff.get('risk_score')}\n"
                f"Skill: {skill_summary}\n"
                f"Context: {context}\n"
            )
            agentic = await runtime.run_tool_loop(
                system_prompt="Eres sales_agent. Decide y ejecuta dentro de ventas sin invocar otros agentes.",
                user_message=prompt,
                domain="sales_execution",
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
                    "agent": "sales_agent",
                    "model": AGENT_MODEL,
                    "thinking_budget": THINKING_BUDGET,
                    "skill": SKILL_NAME,
                    "skill_summary": skill_summary,
                    "status": "completed" if bool(tool_result.get("completed")) else "waiting",
                    "actions_taken": ["agentic_sales_strategy", f"tool:{tool_name}"],
                    "compensation_attempted": False,
                    "tool_result": tool_result,
                    "selected_tool": tool_name,
                    "agentic_note": str(agentic.get("final_answer") or ""),
                    "usage": dict(agentic.get("usage") or {}),
                }
        except Exception:
            if os.getenv("SALES_AGENT_TRACEBACK", "false").lower() in {"1", "true", "yes"}:
                print("SALES_AGENT_TRACEBACK_START")
                print(traceback.format_exc())
                print("SALES_AGENT_TRACEBACK_END")
    return await _heuristic_sales_agent(
        session,
        organization_id=organization_id,
        handoff=handoff,
        skill_summary=skill_summary,
    )
