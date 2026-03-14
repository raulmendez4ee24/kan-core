from __future__ import annotations

import asyncio
from uuid import UUID

from brain.agents.onboarding_agent import run_onboarding_agent


def test_onboarding_agent_executes_activation_path(monkeypatch) -> None:
    monkeypatch.setenv("ENABLE_ANTHROPIC_AGENTIC_RUNTIME", "true")

    async def fake_run_tool_loop(self, **kwargs):
        assert kwargs["thinking_budget_override"] == 4000
        assert kwargs["tool_names"] == ["execute_case_action"]
        return {
            "final_answer": "Activate the pending features.",
            "usage": {"total_tokens": 90},
            "executed_tools": [
                {"tool": "execute_case_action", "result": {"completed": True, "selected_tool": "internal_service"}},
            ],
        }

    monkeypatch.setattr("brain.agents.onboarding_agent.AnthropicAgentRuntime.run_tool_loop", fake_run_tool_loop)

    result = asyncio.run(
        run_onboarding_agent(
            None,  # type: ignore[arg-type]
            organization_id=UUID("11111111-1111-1111-1111-111111111111"),
            handoff={
                "domain": "onboarding_execution",
                "risk_score": 0.8,
                "allowed_tools": ["execute_case_action"],
                "context": {"features_pendientes": ["integracion_api"]},
            },
        )
    )

    assert result["status"] == "completed"
    assert "tool:execute_case_action" in result["actions_taken"]
    assert result["usage"]["total_tokens"] == 90
