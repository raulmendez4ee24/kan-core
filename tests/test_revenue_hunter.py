from __future__ import annotations

import asyncio

from brain.revenue_operators.hunter import HunterOperator


def test_hunter_scores_opportunity_and_generates_offer() -> None:
    operator = HunterOperator()

    async def _run():
        report = await operator.daily_hunt(
            google_maps_results=[
                {
                    "business_name": "Clinica Sonrisa",
                    "vertical": "clinica dental",
                    "has_website": False,
                    "has_bot": False,
                    "estimated_market_size": 420,
                    "pain_score": 0.9,
                    "payment_capacity_score": 0.8,
                    "competition_score": 0.2,
                    "stack_fit_score": 0.9,
                }
            ],
            business_types=["clinica dental"],
            products=["chatbots", "paginas web personalizadas"],
        )
        assert report.opportunities_scanned == 1
        assert report.top_opportunities[0].score > 70
        assert report.offers[0].package_name == "Web + Chatbot"

    asyncio.run(_run())
