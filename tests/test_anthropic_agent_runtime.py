from __future__ import annotations

import asyncio
from uuid import UUID

from brain.anthropic_agent_runtime import (
    AnthropicAgentRuntime,
    build_agentic_payload,
    extract_text_blocks,
    extract_tool_use_blocks,
)


def test_build_agentic_payload_has_no_thinking_or_betas(monkeypatch) -> None:
    payload = build_agentic_payload(
        system_prompt="sys",
        user_message="hello",
        domain="payment_followthrough",
        tools=[{"name": "get_case_state", "description": "d", "input_schema": {"type": "object"}}],
    )

    assert "thinking" not in payload
    assert "betas" not in payload
    assert payload["tools"][0]["name"] == "get_case_state"


def test_extract_helpers_read_tool_and_text_blocks() -> None:
    payload = {
        "content": [
            {"type": "text", "text": "done"},
            {"type": "tool_use", "id": "toolu_1", "name": "get_case_state", "input": {"case_id": "123"}},
        ]
    }

    assert extract_text_blocks(payload) == "done"
    assert extract_tool_use_blocks(payload)[0]["name"] == "get_case_state"


def test_refine_case_decision_returns_agentic_metadata(monkeypatch) -> None:
    runtime = AnthropicAgentRuntime.__new__(AnthropicAgentRuntime)
    runtime._engine = object()

    async def fake_run_tool_loop(**kwargs):
        return {"final_answer": "Use internal fallback.", "tool_rounds": 2, "tool_uses": 1}

    runtime.run_tool_loop = fake_run_tool_loop  # type: ignore[method-assign]
    result = asyncio.run(
        runtime.refine_case_decision(
            case_type="payment_followthrough",
            current_goal="recover payment",
            business_context={},
            candidate_decision={"recommended_action": "payment_followup"},
            session=None,  # type: ignore[arg-type]
            organization_id=UUID("11111111-1111-1111-1111-111111111111"),
        )
    )

    assert result["agentic_note"] == "Use internal fallback."
    assert result["anthropic_tool_rounds"] == 2
    assert "payment_recovery" in result["applied_skills"]

