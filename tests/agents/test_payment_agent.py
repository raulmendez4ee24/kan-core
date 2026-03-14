from __future__ import annotations

import asyncio
from uuid import UUID

from brain.agents.payment_agent import run_payment_agent


def test_payment_agent_uses_run_rollback_for_compensation(monkeypatch) -> None:
    monkeypatch.setenv("ENABLE_ANTHROPIC_AGENTIC_RUNTIME", "true")

    async def fake_run_tool_loop(self, **kwargs):
        assert kwargs["thinking_budget_override"] == 6000
        assert kwargs["tool_names"] == ["execute_case_action", "run_rollback"]
        return {
            "final_answer": "Apply compensation now.",
            "usage": {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150},
            "executed_tools": [
                {"tool": "run_rollback", "result": {"status": "dispatched"}},
            ],
        }

    monkeypatch.setattr("brain.agents.payment_agent.AnthropicAgentRuntime.run_tool_loop", fake_run_tool_loop)

    result = asyncio.run(
        run_payment_agent(
            None,  # type: ignore[arg-type]
            organization_id=UUID("11111111-1111-1111-1111-111111111111"),
            handoff={
                "domain": "payment_followthrough",
                "risk_score": 0.92,
                "allowed_tools": ["execute_case_action", "run_rollback"],
                "context": {"rollback_metadata": {"operation_type": "payment"}},
            },
        )
    )

    assert result["status"] == "completed"
    assert result["compensation_attempted"] is True
    assert "tool:run_rollback" in result["actions_taken"]
    assert result["usage"]["total_tokens"] == 150
