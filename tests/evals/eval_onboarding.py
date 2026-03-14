import asyncio
import os
from uuid import UUID

from brain.eval_harness import build_comparative_report

ORG_ID = UUID("33333333-3333-3333-3333-333333333333")


async def _fake_onboarding_runner(*, session, organization_id, scenario):
    assert session is None
    assert organization_id == ORG_ID
    anthropic = os.getenv("ENABLE_ANTHROPIC_AGENTIC_RUNTIME", "false").lower() == "true"
    case_id = f"{scenario['name']}-{'anthropic' if anthropic else 'legacy'}"
    if anthropic:
        return {
            "case": {"id": case_id, "status": "completed"},
            "decision": {"agentic": {"anthropic_tool_rounds": 1, "token_cost_usd": 0.08}},
            "execution_result": {
                "completed": True,
                "selected_tool": "http",
                "attempts": [{"tool": "http"}],
                "usage": {"cost_usd": 0.08},
                "rollback_attempted": False,
                "anthropic_tool_rounds": 1,
            },
            "outcome_evaluation": {"matches_expected": True},
            "supervision": {"action": "close_case"},
        }
    return {
        "case": {"id": case_id, "status": "waiting"},
        "decision": {"agentic": {"anthropic_tool_rounds": 0, "token_cost_usd": 0.04}},
        "execution_result": {
            "completed": False,
            "selected_tool": "n8n",
            "attempts": [{"tool": "n8n"}],
            "usage": {"cost_usd": 0.04},
            "rollback_attempted": False,
            "anthropic_tool_rounds": 0,
        },
        "outcome_evaluation": {"matches_expected": False},
        "supervision": {"action": "replan"},
    }


def test_onboarding_eval_report_surfaces_completion_and_mismatch() -> None:
    report = asyncio.run(
        build_comparative_report(
            session=None,
            organization_id=ORG_ID,
            domains={
                "onboarding_execution": [
                    {"name": "onboarding-a", "case_type": "onboarding_execution", "goal": "Completar setup inicial"},
                    {"name": "onboarding-b", "case_type": "onboarding_execution", "goal": "Validar credenciales del cliente"},
                ]
            },
            runner=_fake_onboarding_runner,
        )
    )

    domain = report["domains"][0]
    assert domain["legacy"]["metrics"]["case_completion_rate"] == 0.0
    assert domain["anthropic"]["metrics"]["case_completion_rate"] == 1.0
    assert domain["legacy"]["metrics"]["outcome_mismatch_rate"] == 1.0
    assert domain["anthropic"]["metrics"]["outcome_mismatch_rate"] == 0.0
    assert domain["anthropic"]["metrics"]["anthropic_tool_rounds_per_case"] == 1.0
    assert report["overall"]["anthropic"]["case_count"] == 2
    assert "onboarding_execution" in report["report_text"]
