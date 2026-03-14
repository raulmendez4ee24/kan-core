import asyncio
import os
from uuid import UUID

from brain.eval_harness import build_comparative_report

ORG_ID = UUID("22222222-2222-2222-2222-222222222222")


async def _fake_collections_runner(*, session, organization_id, scenario):
    assert session is None
    assert organization_id == ORG_ID
    anthropic = os.getenv("ENABLE_ANTHROPIC_AGENTIC_RUNTIME", "false").lower() == "true"
    suffix = "anthropic" if anthropic else "legacy"
    if anthropic:
        return {
            "case": {"id": f"{scenario['name']}-{suffix}", "status": "completed"},
            "decision": {"agentic": {"anthropic_tool_rounds": 2, "token_cost_usd": 0.11}},
            "execution_result": {
                "completed": True,
                "selected_tool": "internal_service",
                "attempts": [{"tool": "internal_service"}],
                "usage": {"cost_usd": 0.11},
                "rollback_attempted": False,
                "anthropic_tool_rounds": 2,
            },
            "outcome_evaluation": {"matches_expected": True},
            "supervision": {"action": "close_case"},
        }
    return {
        "case": {"id": f"{scenario['name']}-{suffix}", "status": "completed"},
        "decision": {"agentic": {"anthropic_tool_rounds": 0, "token_cost_usd": 0.06}},
        "execution_result": {
            "completed": True,
            "selected_tool": "n8n",
            "attempts": [{"tool": "n8n"}, {"tool": "internal_service"}],
            "usage": {"cost_usd": 0.06},
            "rollback_attempted": False,
            "anthropic_tool_rounds": 0,
        },
        "outcome_evaluation": {"matches_expected": True},
        "supervision": {"action": "switch_tool"},
    }


def test_collections_eval_report_tracks_tool_switch_and_cost() -> None:
    report = asyncio.run(
        build_comparative_report(
            session=None,
            organization_id=ORG_ID,
            domains={
                "collections_followthrough": [
                    {"name": "collections-a", "case_type": "collections_followthrough", "goal": "Negociar saldo vencido"},
                    {"name": "collections-b", "case_type": "collections_followthrough", "goal": "Registrar promesa de pago"},
                ]
            },
            runner=_fake_collections_runner,
        )
    )

    domain = report["domains"][0]
    assert domain["legacy"]["metrics"]["tool_switch_rate"] == 1.0
    assert domain["anthropic"]["metrics"]["tool_switch_rate"] == 0.0
    assert domain["legacy"]["metrics"]["case_completion_rate"] == 1.0
    assert domain["anthropic"]["metrics"]["case_completion_rate"] == 1.0
    assert domain["anthropic"]["metrics"]["token_cost_per_case"] == 0.11
    assert domain["legacy"]["metrics"]["token_cost_per_case"] == 0.06
    assert domain["delta"]["anthropic_tool_rounds_per_case"] == 2.0
    assert "collections_followthrough" in report["report_text"]
