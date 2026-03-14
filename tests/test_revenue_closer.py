from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from brain.revenue_operators.closer import CloserOperator, PipelineLead


def test_closer_prioritizes_hot_lead_for_booking() -> None:
    operator = CloserOperator()
    leads = [
        PipelineLead(
            lead_id="cold-1",
            engagement_score=20,
            budget_score=10,
            urgency_score=10,
            last_contact_at=datetime.now(timezone.utc) - timedelta(days=10),
        ),
        PipelineLead(
            lead_id="hot-1",
            engagement_score=80,
            budget_score=70,
            urgency_score=60,
            ready_for_booking=True,
            last_contact_at=datetime.now(timezone.utc) - timedelta(hours=2),
        ),
    ]

    async def _run():
        actions = await operator.daily_close_plan(leads)
        assert actions[0].lead_id == "hot-1"
        assert actions[0].action_type == "push_booking"

    asyncio.run(_run())
