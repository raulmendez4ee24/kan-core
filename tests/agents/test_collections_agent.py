from __future__ import annotations

import asyncio
from uuid import UUID

from brain.agents.collections_agent import run_collections_agent


def test_collections_agent_executes_followthrough(monkeypatch) -> None:
    monkeypatch.setenv("ENABLE_ANTHROPIC_AGENTIC_RUNTIME", "true")

    async def fake_run_tool_loop(self, **kwargs):
        assert kwargs["thinking_budget_override"] == 5000
        assert kwargs["tool_names"] == ["execute_case_action"]
        return {
            "final_answer": "Run collections followthrough.",
            "usage": {"total_tokens": 120},
            "executed_tools": [
                {"tool": "execute_case_action", "result": {"completed": True, "selected_tool": "internal_service"}},
            ],
        }

    monkeypatch.setattr("brain.agents.collections_agent.AnthropicAgentRuntime.run_tool_loop", fake_run_tool_loop)

    result = asyncio.run(
        run_collections_agent(
            None,  # type: ignore[arg-type]
            organization_id=UUID("11111111-1111-1111-1111-111111111111"),
            handoff={
                "domain": "collections_followthrough",
                "risk_score": 0.8,
                "allowed_tools": ["execute_case_action"],
                "context": {"deuda_total": 12000},
            },
        )
    )

    assert result["status"] == "completed"
    assert "tool:execute_case_action" in result["actions_taken"]
    assert result["usage"]["total_tokens"] == 120
