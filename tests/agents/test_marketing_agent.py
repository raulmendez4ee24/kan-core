from __future__ import annotations

import asyncio
from uuid import UUID

from brain.agents.marketing_agent import run_marketing_agent


def test_marketing_agent_executes_campaign_path(monkeypatch) -> None:
    monkeypatch.setenv("ENABLE_ANTHROPIC_AGENTIC_RUNTIME", "true")

    async def fake_run_tool_loop(self, **kwargs):
        assert kwargs["thinking_budget_override"] == 6000
        assert kwargs["tool_names"] == ["execute_case_action"]
        return {
            "final_answer": "Optimize targeting now.",
            "usage": {"total_tokens": 110},
            "executed_tools": [
                {"tool": "execute_case_action", "result": {"completed": True, "selected_tool": "internal_service"}},
            ],
        }

    monkeypatch.setattr("brain.agents.marketing_agent.AnthropicAgentRuntime.run_tool_loop", fake_run_tool_loop)

    result = asyncio.run(
        run_marketing_agent(
            None,  # type: ignore[arg-type]
            organization_id=UUID("11111111-1111-1111-1111-111111111111"),
            handoff={
                "domain": "campaign_execution",
                "risk_score": 0.8,
                "allowed_tools": ["execute_case_action"],
                "context": {"daily_spend_usd": 12000, "ctr_drop_pct": 30},
            },
        )
    )

    assert result["status"] == "completed"
    assert "tool:execute_case_action" in result["actions_taken"]
    assert result["usage"]["total_tokens"] == 110
    assert result["skill"] == "meta_ads_growth"
    assert result["offer_brief"]["recommended_offer"] == "Growth"
    assert result["creative_review"]["approved"] is True


def test_marketing_agent_repairs_whatsapp_followup_for_ghosting(monkeypatch) -> None:
    monkeypatch.setenv("ENABLE_ANTHROPIC_AGENTIC_RUNTIME", "false")

    async def fake_execute_tool(tool_name, *, args, session, organization_id):
        assert tool_name == "execute_case_action"
        assert args["step"]["execution_payload"]["strategy"] == "repair_whatsapp_followup"
        diagnosis = args["step"]["execution_payload"]["diagnosis"]
        assert diagnosis["primary_stage"] == "whatsapp_followup"
        assert "lead_ghosting" in diagnosis["bottlenecks"]
        return {"completed": True, "selected_tool": "internal_service"}

    monkeypatch.setattr("brain.agents.marketing_agent.execute_tool", fake_execute_tool)

    result = asyncio.run(
        run_marketing_agent(
            None,  # type: ignore[arg-type]
            organization_id=UUID("11111111-1111-1111-1111-111111111111"),
            handoff={
                "domain": "campaign_execution",
                "risk_score": 0.3,
                "allowed_tools": ["execute_case_action"],
                "context": {
                    "ctr_link_pct": 3.4,
                    "left_on_read_pct": 82,
                    "reply_rate": 0.12,
                    "first_response_minutes": 18,
                },
            },
        )
    )

    assert result["status"] == "completed"
    assert result["actions_taken"][0] == "repair_whatsapp_followup"
    assert "lead_ghosting" in result["bottlenecks"]


def test_marketing_agent_stabilizes_scaling_when_budget_grows_without_sales(monkeypatch) -> None:
    monkeypatch.setenv("ENABLE_ANTHROPIC_AGENTIC_RUNTIME", "false")

    async def fake_execute_tool(tool_name, *, args, session, organization_id):
        assert tool_name == "execute_case_action"
        assert args["step"]["execution_payload"]["strategy"] == "stabilize_scaling"
        diagnosis = args["step"]["execution_payload"]["diagnosis"]
        assert diagnosis["primary_stage"] == "scaling"
        assert "budget_scaled_without_sales" in diagnosis["bottlenecks"]
        return {"completed": True, "selected_tool": "internal_service"}

    monkeypatch.setattr("brain.agents.marketing_agent.execute_tool", fake_execute_tool)

    result = asyncio.run(
        run_marketing_agent(
            None,  # type: ignore[arg-type]
            organization_id=UUID("11111111-1111-1111-1111-111111111111"),
            handoff={
                "domain": "campaign_execution",
                "risk_score": 0.4,
                "allowed_tools": ["execute_case_action"],
                "context": {
                    "ctr_link_pct": 3.8,
                    "budget_scale_pct": 45,
                    "sales_change_pct": 0,
                    "daily_spend_usd": 700,
                },
            },
        )
    )

    assert result["status"] == "completed"
    assert result["actions_taken"][0] == "stabilize_scaling"
    assert "budget_scaled_without_sales" in result["bottlenecks"]
