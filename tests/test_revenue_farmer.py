from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from brain.revenue_operators.farmer import CustomerHealth, FarmerOperator


def test_farmer_detects_upsell_and_reactivation() -> None:
    operator = FarmerOperator()
    customers = [
        CustomerHealth(
            customer_id="web-1",
            current_products=["pagina web"],
            usage_score=0.9,
            satisfaction_score=0.9,
            nps=10,
        ),
        CustomerHealth(
            customer_id="churn-1",
            cancelled_at=datetime.now(timezone.utc) - timedelta(days=75),
            churn_reason="presupuesto",
        ),
    ]

    async def _run():
        actions = await operator.daily_farm(customers)
        action_types = {item.action_type for item in actions}
        assert "upsell" in action_types
        assert "reactivate_churned" in action_types

    asyncio.run(_run())
