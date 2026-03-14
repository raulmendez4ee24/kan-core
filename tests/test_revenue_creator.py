from __future__ import annotations

import asyncio

from brain.revenue_operators.creator import CreatorOperator


def test_creator_returns_safe_action_when_signals_are_weak() -> None:
    operator = CreatorOperator()

    async def _run():
        actions = await operator.suggest_actions(
            goal="generar leads para agentes",
            context={
                "campaign_name": "Test campaign",
                "daily_spend_usd": 4,
                "impressions": 120,
                "link_clicks": 1,
                "ctr_link_pct": 0.8,
            },
        )
        assert actions
        assert actions[0].action_type in {"hold_steady", "mark_for_creative_refresh", "inspect_followup_funnel"}

    asyncio.run(_run())
