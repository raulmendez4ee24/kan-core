from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

from brain.revenue_brain import RevenueBrain
from brain.revenue_operators.closer import PipelineLead
from brain.revenue_operators.farmer import CustomerHealth


def test_revenue_brain_daily_cycle_full_mocked_run() -> None:
    brain = RevenueBrain()

    async def _run():
        now = datetime.now(timezone.utc)
        briefing = await brain.daily_cycle(
            {
                "revenue_yesterday_mxn": 4200,
                "active_leads": 3,
                "campaign_flags": ["declining_ctr", "creative_fatigue"],
                "city": "Guadalajara",
                "business_types": ["clinica", "restaurante", "inmobiliaria"],
                "products": [
                    "chatbots",
                    "agentes de voz",
                    "agentes autónomos",
                    "paginas web personalizadas",
                ],
                "google_maps_results": [
                    {
                        "business_name": "Clinica Santa Fe",
                        "vertical": "clinica",
                        "has_website": False,
                        "has_bot": False,
                        "estimated_market_size": 220,
                        "pain_score": 0.84,
                        "payment_capacity_score": 0.82,
                        "competition_score": 0.25,
                        "stack_fit_score": 0.94,
                    },
                    {
                        "business_name": "Restaurante Don Julio",
                        "vertical": "restaurante",
                        "has_website": False,
                        "has_bot": False,
                        "estimated_market_size": 170,
                        "pain_score": 0.76,
                        "payment_capacity_score": 0.61,
                        "competition_score": 0.34,
                        "stack_fit_score": 0.88,
                    },
                    {
                        "business_name": "Inmobiliaria Alfa",
                        "vertical": "inmobiliaria",
                        "has_website": True,
                        "has_bot": False,
                        "estimated_market_size": 95,
                        "pain_score": 0.71,
                        "payment_capacity_score": 0.79,
                        "competition_score": 0.41,
                        "stack_fit_score": 0.81,
                    },
                    {
                        "business_name": "Dental Nova",
                        "vertical": "clinica",
                        "has_website": False,
                        "has_bot": True,
                        "estimated_market_size": 130,
                        "pain_score": 0.63,
                        "payment_capacity_score": 0.73,
                        "competition_score": 0.51,
                        "stack_fit_score": 0.74,
                    },
                    {
                        "business_name": "Estetica Roma",
                        "vertical": "belleza",
                        "has_website": False,
                        "has_bot": False,
                        "estimated_market_size": 140,
                        "pain_score": 0.69,
                        "payment_capacity_score": 0.58,
                        "competition_score": 0.38,
                        "stack_fit_score": 0.7,
                    },
                ],
                "job_board_results": [
                    {
                        "business_name": "Barbería Norte",
                        "vertical": "barbershops",
                        "city": "Guadalajara",
                        "has_website": False,
                        "has_bot": False,
                        "estimated_market_size": 210,
                        "pain_score": 0.74,
                        "payment_capacity_score": 0.63,
                        "competition_score": 0.29,
                        "stack_fit_score": 0.82,
                        "metadata": {"job_board": "indeed", "job_title": "Recepcionista"},
                    },
                    {
                        "business_name": "Restaurante Cielo",
                        "vertical": "restaurants",
                        "city": "Zapopan",
                        "has_website": False,
                        "has_bot": False,
                        "estimated_market_size": 260,
                        "pain_score": 0.71,
                        "payment_capacity_score": 0.66,
                        "competition_score": 0.27,
                        "stack_fit_score": 0.84,
                        "metadata": {"job_board": "computrabajo", "job_title": "Community Manager"},
                    },
                ],
                "competitor_activity_results": [
                    {
                        "business_name": "Clinica Santa Fe",
                        "vertical": "clinica",
                        "city": "Guadalajara",
                        "has_website": False,
                        "has_bot": False,
                        "estimated_market_size": 220,
                        "pain_score": 0.84,
                        "payment_capacity_score": 0.82,
                        "competition_score": 0.25,
                        "stack_fit_score": 0.94,
                        "metadata": {"source_url": "https://competidor.com/precios", "change_type": "pricing_update"},
                    },
                    {
                        "business_name": "Spa Aurora",
                        "vertical": "spas",
                        "city": "Zapopan",
                        "has_website": True,
                        "has_bot": False,
                        "estimated_market_size": 180,
                        "pain_score": 0.78,
                        "payment_capacity_score": 0.69,
                        "competition_score": 0.24,
                        "stack_fit_score": 0.85,
                        "metadata": {"source_url": "https://competidor.com/services/new", "change_type": "new_service_page"},
                    },
                ],
                "complaints_results": [
                    {
                        "business_name": "complaints:web_presence",
                        "vertical": "services",
                        "city": "MX",
                        "has_website": False,
                        "has_bot": False,
                        "estimated_market_size": 180,
                        "pain_score": 0.88,
                        "payment_capacity_score": 0.72,
                        "competition_score": 0.24,
                        "stack_fit_score": 0.9,
                        "metadata": {"theme": "web_presence", "signals_count": 14},
                    }
                ],
                "arbitrage_results": [
                    {
                        "business_name": "arbitrage:VoiceCloser",
                        "vertical": "services",
                        "city": "LATAM",
                        "has_website": False,
                        "has_bot": False,
                        "estimated_market_size": 260,
                        "pain_score": 0.79,
                        "payment_capacity_score": 0.74,
                        "competition_score": 0.18,
                        "stack_fit_score": 0.87,
                        "metadata": {"product_name": "VoiceCloser", "has_spanish_competitor": False},
                    }
                ],
                "internal_demand_results": [
                    {
                        "business_name": "internal_demand:whatsapp_automation",
                        "vertical": "services",
                        "city": "MX",
                        "has_website": False,
                        "has_bot": False,
                        "estimated_market_size": 160,
                        "pain_score": 0.81,
                        "payment_capacity_score": 0.71,
                        "competition_score": 0.22,
                        "stack_fit_score": 0.88,
                        "metadata": {"theme": "whatsapp_automation", "signals_count": 9},
                    }
                ],
                "pipeline_leads": [
                    PipelineLead(
                        lead_id="lead-hot",
                        engagement_score=91,
                        budget_score=82,
                        urgency_score=88,
                        vertical="clinica",
                        source="meta_ads",
                        ready_for_booking=True,
                        last_contact_at=now - timedelta(hours=5),
                    ),
                    PipelineLead(
                        lead_id="lead-cold",
                        engagement_score=38,
                        budget_score=55,
                        urgency_score=34,
                        vertical="restaurante",
                        source="instagram",
                        ready_for_booking=False,
                        last_contact_at=now - timedelta(days=4),
                    ),
                    PipelineLead(
                        lead_id="lead-dead",
                        engagement_score=10,
                        budget_score=25,
                        urgency_score=18,
                        vertical="inmobiliaria",
                        source="meta_ads",
                        ready_for_booking=False,
                        last_contact_at=now - timedelta(days=16),
                    ),
                ],
                "customers": [
                    CustomerHealth(
                        customer_id="customer-churn",
                        current_products=["chatbot"],
                        usage_score=0.25,
                        satisfaction_score=0.42,
                        nps=4,
                        days_to_renewal=9,
                    ),
                    CustomerHealth(
                        customer_id="customer-upsell",
                        current_products=["pagina web"],
                        usage_score=0.88,
                        satisfaction_score=0.93,
                        nps=9,
                        days_since_onboarding=21,
                    ),
                ],
                "campaign_goal": "vender agentes y automatización",
                "campaign_context": {
                    "campaign_name": "Meta WA GDL",
                    "objective": "messages",
                    "daily_spend_usd": 52,
                    "impressions": 4800,
                    "link_clicks": 39,
                    "ctr_link_pct": 0.81,
                    "ctr_drop_pct": 29,
                    "frequency": 3.4,
                    "reply_rate": 0.16,
                    "left_on_read_pct": 71,
                    "first_response_minutes": 18,
                    "sales_change_pct": 0,
                    "budget_change_pct": 25,
                    "channel": "whatsapp",
                },
            }
        )

        assert briefing.actions
        operators = {item.operator for item in briefing.actions}
        assert {"hunter", "closer", "farmer", "creator"} <= operators

        action_summary: dict[str, list[dict[str, object]]] = {}
        for item in briefing.actions:
            action_summary.setdefault(item.operator, []).append(
                {
                    "action_type": item.action_type,
                    "priority": item.priority,
                    "confidence": item.confidence,
                    "channel": item.channel,
                    "reason": item.reason,
                }
            )

        print("\n=== REVENUE BRAIN ACTIONS BY OPERATOR ===")
        print(json.dumps(action_summary, indent=2, ensure_ascii=False))

        print("\n=== AUTO-EXECUTED ACTIONS ===")
        print(
            json.dumps(
                [item.model_dump() for item in briefing.executed],
                indent=2,
                ensure_ascii=False,
            )
        )

        print("\n=== RECOMMENDED TO RAUL ===")
        print(
            json.dumps(
                [item.model_dump() for item in briefing.recommended],
                indent=2,
                ensure_ascii=False,
            )
        )

        print("\n=== FULL DAILY BRIEFING ===")
        print(json.dumps(briefing.model_dump(mode="json"), indent=2, ensure_ascii=False))

    asyncio.run(_run())


def test_revenue_brain_gather_metrics_uses_new_market_scanners() -> None:
    brain = RevenueBrain()

    async def _scan_complaints():
        return [{"business_name": "complaints:web_presence", "vertical": "services"}]

    async def _scan_arbitrage():
        return [{"business_name": "arbitrage:VoiceCloser", "vertical": "services"}]

    async def _scan_internal_demand(*, crm_texts=None, conversation_texts=None):
        assert crm_texts == ["pagina web", "crm"]
        assert conversation_texts == ["automatizacion whatsapp"]
        return [{"business_name": "internal_demand:crm_followup", "vertical": "services"}]

    brain.hunter.scan_complaints = _scan_complaints  # type: ignore[method-assign]
    brain.hunter.scan_arbitrage = _scan_arbitrage  # type: ignore[method-assign]
    brain.hunter.scan_internal_demand = _scan_internal_demand  # type: ignore[method-assign]
    brain.hunter.scan_job_boards = lambda city="Guadalajara": __import__("asyncio").sleep(0, result=[])  # type: ignore[method-assign]
    brain.hunter.scan_competitor_activity = lambda domains: __import__("asyncio").sleep(0, result=[])  # type: ignore[method-assign]

    async def _run() -> None:
        metrics = await brain.gather_metrics(
            {
                "crm_signal_texts": ["pagina web", "crm"],
                "conversation_signal_texts": ["automatizacion whatsapp"],
            }
        )
        assert metrics["complaints_results"][0]["business_name"] == "complaints:web_presence"
        assert metrics["arbitrage_results"][0]["business_name"] == "arbitrage:VoiceCloser"
        assert metrics["internal_demand_results"][0]["business_name"] == "internal_demand:crm_followup"

    asyncio.run(_run())
