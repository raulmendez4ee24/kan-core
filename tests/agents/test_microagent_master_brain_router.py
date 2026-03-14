from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

from brain.agents.master_brain_router import route_case_via_master_brain


def test_master_brain_router_builds_payment_handoff_and_verifies_success(monkeypatch) -> None:
    async def fake_payment_agent(session, *, organization_id, handoff):
        assert handoff["domain"] == "payment_followthrough"
        assert handoff["skill"] == "payment_recovery"
        assert "run_rollback" in handoff["allowed_tools"]
        return {
            "agent": "payment_agent",
            "status": "completed",
            "actions_taken": ["retry_charge", "tool:execute_case_action"],
            "selected_tool": "execute_case_action",
            "tool_result": {"completed": True, "selected_tool": "internal_service"},
        }

    monkeypatch.setattr("brain.agents.payment_agent.run_payment_agent", fake_payment_agent)

    result = asyncio.run(
        route_case_via_master_brain(
            None,  # type: ignore[arg-type]
            organization_id=UUID("11111111-1111-1111-1111-111111111111"),
            case_id=uuid4(),
            current_goal="recuperar pago de suscripcion vencida",
            business_context={"dias_vencido": 12, "monto_mensual": 1500},
            business_state={"recommended_focus": "payment_followthrough", "critical_risks": []},
            decision_hint={"risk_score": 0.4},
            rollbackable=None,
        )
    )

    assert result["handoff"]["domain"] == "payment_followthrough"
    assert result["agent_name"] == "payment_agent"
    assert result["outcome_evaluation"]["matches_expected"] is True
    assert result["supervision"]["action"] == "close_case"
