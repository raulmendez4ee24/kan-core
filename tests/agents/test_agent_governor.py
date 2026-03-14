from __future__ import annotations

from brain.agents.agent_governor import evaluate_handoff, evaluate_result


def test_agent_governor_blocks_missing_rollback_tool() -> None:
    result = evaluate_handoff(
        agent_name="payment_agent",
        handoff={
            "risk_score": 0.95,
            "allowed_tools": ["execute_case_action"],
            "context": {"rollback_metadata": {"operation_type": "payment"}},
        },
    )

    assert result["permitted"] is False
    assert result["hold_reason"] == "rollback_tool_missing"


def test_agent_governor_flags_repeated_action_loop() -> None:
    result = evaluate_result(
        agent_name="marketing_agent",
        handoff={"case_id": "abc"},
        agent_result={
            "status": "waiting",
            "actions_taken": ["refresh_creative", "refresh_creative", "refresh_creative"],
            "selected_tool": "execute_case_action",
        },
    )

    assert result["force_escalate"] is True
    assert "loop_suspected" in result["flags"]
