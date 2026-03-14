import asyncio
import os
from uuid import UUID

from brain.eval_harness import build_comparative_report

ORG_ID = UUID("11111111-1111-1111-1111-111111111111")


async def _fake_payment_runner(*, session, organization_id, scenario):
    assert session is None
    assert organization_id == ORG_ID
    anthropic = os.getenv("ENABLE_ANTHROPIC_AGENTIC_RUNTIME", "false").lower() == "true"
    case_id = f"{scenario['name']}-{'anthropic' if anthropic else 'legacy'}"
    if anthropic:
        return {
            "case": {"id": case_id, "status": "completed"},
            "decision": {"agentic": {"anthropic_tool_rounds": 3, "token_cost_usd": 0.14}},
            "execution_result": {
                "completed": True,
                "selected_tool": "http",
                "attempts": [{"tool": "http"}],
                "usage": {"cost_usd": 0.14},
                "rollback_attempted": True,
                "rollback_result": {"status": "dispatched"},
                "compensated": True,
                "anthropic_tool_rounds": 3,
            },
            "outcome_evaluation": {"matches_expected": True},
            "supervision": {"action": "close_case"},
        }
    return {
        "case": {"id": case_id, "status": "waiting"},
        "decision": {"agentic": {"anthropic_tool_rounds": 0, "token_cost_usd": 0.05}},
        "execution_result": {
            "completed": False,
            "selected_tool": "n8n",
            "attempts": [{"tool": "n8n"}, {"tool": "http"}],
            "usage": {"cost_usd": 0.05},
            "rollback_attempted": True,
            "rollback_result": {"status": "failed"},
            "compensated": False,
            "anthropic_tool_rounds": 0,
        },
        "outcome_evaluation": {"matches_expected": False},
        "supervision": {"action": "switch_tool"},
    }


def test_payment_eval_report_compares_legacy_vs_anthropic() -> None:
    report = asyncio.run(
        build_comparative_report(
            session=None,
            organization_id=ORG_ID,
            domains={
                "payment_followthrough": [
                    {"name": "payment-a", "case_type": "payment_followthrough", "goal": "Recuperar pago atrasado"},
                    {"name": "payment-b", "case_type": "payment_followthrough", "goal": "Corregir cobro duplicado"},
                ]
            },
            runner=_fake_payment_runner,
        )
    )

    domain = report["domains"][0]
    assert domain["domain"] == "payment_followthrough"
    assert domain["legacy"]["metrics"]["case_completion_rate"] == 0.0
    assert domain["anthropic"]["metrics"]["case_completion_rate"] == 1.0
    assert domain["legacy"]["metrics"]["tool_switch_rate"] == 1.0
    assert domain["anthropic"]["metrics"]["tool_switch_rate"] == 0.0
    assert domain["legacy"]["metrics"]["rollback_success_rate"] == 0.0
    assert domain["anthropic"]["metrics"]["rollback_success_rate"] == 1.0
    assert domain["anthropic"]["metrics"]["anthropic_tool_rounds_per_case"] == 3.0
    assert domain["legacy"]["metrics"]["outcome_mismatch_rate"] == 1.0
    assert "payment_followthrough" in report["report_text"]
    assert "case_completion_rate" in report["report_text"]
