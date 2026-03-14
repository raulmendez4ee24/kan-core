from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional
from uuid import UUID

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from brain.core import CognitiveEngine
from brain.mcp_runtime import get_tool_catalog, execute_tool
from brain.skill_registry import match_skills


def anthropic_agentic_enabled() -> bool:
    return os.getenv("ENABLE_ANTHROPIC_AGENTIC_RUNTIME", "false").lower() in {"1", "true", "yes"}


def _thinking_enabled() -> bool:
    return os.getenv("ANTHROPIC_AGENTIC_ENABLE_THINKING", "true").lower() in {"1", "true", "yes"}


def _safe_eval_mode() -> bool:
    return os.getenv("EVAL_HARNESS_SAFE_RUNTIME", "false").lower() in {"1", "true", "yes"}


def _budget_for(domain: str) -> int:
    token = str(domain or "").strip().lower()
    if "payment" in token:
        return int(os.getenv("ANTHROPIC_AGENTIC_THINKING_BUDGET_PAYMENT", "8000"))
    if "collections" in token:
        return int(os.getenv("ANTHROPIC_AGENTIC_THINKING_BUDGET_COLLECTIONS", "7000"))
    if "onboarding" in token:
        return int(os.getenv("ANTHROPIC_AGENTIC_THINKING_BUDGET_ONBOARDING", "5000"))
    if "support" in token:
        return int(os.getenv("ANTHROPIC_AGENTIC_THINKING_BUDGET_SUPPORT", "5000"))
    return int(os.getenv("ANTHROPIC_AGENTIC_THINKING_BUDGET_DEFAULT", "4000"))


def build_agentic_payload(
    *,
    system_prompt: str,
    user_message: str,
    domain: str,
    tools: List[Dict[str, Any]],
    prior_messages: Optional[List[Dict[str, Any]]] = None,
    thinking_budget_override: Optional[int] = None,
) -> Dict[str, Any]:
    content: List[Dict[str, Any]] = [{"type": "text", "text": user_message}]
    messages = list(prior_messages or [])
    if not messages:
        messages = [{"role": "user", "content": content}]
    payload: Dict[str, Any] = {
        "model": os.getenv("ANTHROPIC_AGENTIC_MODEL", os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")),
        "system": system_prompt,
        "messages": messages,
        "tools": [
            {
                "name": item["name"],
                "description": item["description"],
                "input_schema": item["input_schema"],
            }
            for item in tools
        ],
        "max_tokens": int(os.getenv("ANTHROPIC_MAX_OUTPUT_TOKENS", "1024")),
    }
    if _thinking_enabled() and not _safe_eval_mode():
        payload["thinking"] = {
            "type": "enabled",
            "budget_tokens": int(thinking_budget_override or _budget_for(domain)),
        }
        payload["betas"] = [os.getenv("ANTHROPIC_AGENTIC_INTERLEAVED_BETA", "interleaved-thinking-2025-05-14")]
    return payload


def extract_tool_use_blocks(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    items = []
    for block in list(payload.get("content") or []):
        if isinstance(block, dict) and str(block.get("type") or "") == "tool_use":
            items.append(block)
    return items


def extract_text_blocks(payload: Dict[str, Any]) -> str:
    parts: List[str] = []
    for block in list(payload.get("content") or []):
        if isinstance(block, dict) and str(block.get("type") or "") == "text":
            text = str(block.get("text") or "").strip()
            if text:
                parts.append(text)
    return "\n".join(parts)


class AnthropicAgentRuntime:
    def __init__(self, *, engine: Optional[CognitiveEngine] = None) -> None:
        self._engine = engine or CognitiveEngine.get_instance()

    async def invoke(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        body = dict(payload)
        response = await self._engine._call_anthropic(
            body,
            base_url=os.getenv("ANTHROPIC_API_BASE", "https://api.anthropic.com/v1"),
            api_key=os.getenv("ANTHROPIC_API_KEY", ""),
        )
        response["_provider"] = "anthropic"
        return response

    async def run_tool_loop(
        self,
        *,
        system_prompt: str,
        user_message: str,
        domain: str,
        session: Optional[AsyncSession] = None,
        organization_id: Optional[UUID] = None,
        tool_names: Optional[List[str]] = None,
        max_rounds: int = 3,
        thinking_budget_override: Optional[int] = None,
    ) -> Dict[str, Any]:
        catalog = get_tool_catalog()
        if tool_names is not None:
            allowed = {str(name) for name in tool_names}
            catalog = [item for item in catalog if item["name"] in allowed]
        payload = build_agentic_payload(
            system_prompt=system_prompt,
            user_message=user_message,
            domain=domain,
            tools=catalog,
            thinking_budget_override=thinking_budget_override,
        )
        rounds = 0
        tool_uses = 0
        last_response: Dict[str, Any] = {}
        messages = list(payload.get("messages") or [])
        executed_tools: List[Dict[str, Any]] = []
        while rounds < max(1, max_rounds):
            rounds += 1
            payload["messages"] = messages
            response = await self.invoke(payload)
            last_response = response
            tool_blocks = extract_tool_use_blocks(response)
            if not tool_blocks:
                break
            messages.append({"role": "assistant", "content": list(response.get("content") or [])})
            tool_results: List[Dict[str, Any]] = []
            for block in tool_blocks:
                tool_uses += 1
                name = str(block.get("name") or "")
                args = dict(block.get("input") or {})
                try:
                    result = await execute_tool(
                        name,
                        args=args,
                        session=session,
                        organization_id=organization_id,
                    )
                    executed_tools.append({"tool": name, "args": args, "result": result})
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.get("id"),
                            "content": json.dumps(result, ensure_ascii=False, default=str),
                        }
                    )
                except Exception as exc:
                    if session is not None:
                        try:
                            await session.rollback()
                        except Exception:
                            pass
                    executed_tools.append(
                        {
                            "tool": name,
                            "args": args,
                            "error": str(exc),
                        }
                    )
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.get("id"),
                            "is_error": True,
                            "content": str(exc),
                        }
                    )
            messages.append({"role": "user", "content": tool_results})
        return {
            "final_answer": extract_text_blocks(last_response),
            "tool_rounds": rounds,
            "tool_uses": tool_uses,
            "raw_response": last_response,
            "usage": self._engine._extract_usage(last_response) or {},
            "executed_tools": executed_tools,
        }

    async def refine_case_decision(
        self,
        *,
        case_type: str,
        current_goal: str,
        business_context: Dict[str, Any],
        candidate_decision: Dict[str, Any],
        session: AsyncSession,
        organization_id: UUID,
    ) -> Dict[str, Any]:
        skills = match_skills(case_type=case_type, business_context=business_context)
        skill_lines = "\n".join(f"- {item['name']}: {item['summary']}" for item in skills)
        prompt = (
            "Evalua el siguiente caso empresarial. Puedes usar tools para inspeccionar estado, "
            "pero debes devolver texto breve con recomendaciones concretas.\n"
            f"Caso: {case_type}\nObjetivo: {current_goal}\n"
            f"Decision actual: {json.dumps(candidate_decision, ensure_ascii=False, default=str)}\n"
        )
        if skill_lines:
            prompt += f"Skills aplicables:\n{skill_lines}\n"
        safe_runtime = _safe_eval_mode()
        selected_tools = [] if safe_runtime else ["get_case_state", "get_business_state", "get_rollbackable_guardrails"]
        try:
            result = await self.run_tool_loop(
                system_prompt="Eres un operador empresarial que valida y refina decisiones antes de ejecutar.",
                user_message=prompt,
                domain=case_type,
                session=session,
                organization_id=organization_id,
                tool_names=selected_tools,
                max_rounds=1 if safe_runtime else 2,
            )
        except Exception as exc:
            raise
        usage = dict(result.get("usage") or {})
        return {
            "activated": True,
            "agentic_note": result.get("final_answer") or "",
            "anthropic_tool_rounds": int(result.get("tool_rounds") or 0),
            "anthropic_tool_uses": int(result.get("tool_uses") or 0),
            "usage": usage,
            "input_tokens": int(usage.get("input_tokens") or 0),
            "output_tokens": int(usage.get("output_tokens") or 0),
            "total_tokens": int(usage.get("total_tokens") or 0),
            "token_cost_usd": float(usage.get("cost_usd") or 0.0),
            "applied_skills": [item["name"] for item in skills],
        }
