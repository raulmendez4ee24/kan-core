from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi.testclient import TestClient

from brain.creative_memory import CreativePattern, save_creative_pattern
from brain.forge import AdCreative, CompleteOffer, OfferForge, list_generated_offers, save_generated_offer
from brain.revenue_operators.hunter import OpportunityInput
from brain.revenue_brain import RevenueBrain
from main import app


def test_offer_forge_returns_complete_offer_from_mocked_claude(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CREATIVE_MEMORY_DB_PATH", str(tmp_path / "creative_memory.sqlite3"))

    save_creative_pattern(
        CreativePattern(
            domain="campaign_execution",
            offer="Clinica Web + Chatbot",
            hook="Tus pacientes te escriben y nadie responde a tiempo",
            angle="captacion y seguimiento de pacientes",
            cta="Te muestro como quedaria",
            pattern_used="pain_loss",
            critic_score=91.0,
            close_rate=0.21,
        )
    )

    forge = OfferForge()

    async def _fake_call(_: str) -> str:
        return """
        {
          "offer": {
            "name": "Paciente 24/7",
            "description": "Sistema para captar y responder pacientes por web y WhatsApp.",
            "features": [
              "Landing médica",
              "Bot de WhatsApp",
              "Calificación automática",
              "Agenda de citas"
            ],
            "price_mxn": 6900,
            "guarantee": "Si no queda instalado el flujo base en 10 días, damos soporte extra sin costo.",
            "timeline": "7 días hábiles"
          },
          "whatsapp_copy": "Hola, vi que su clínica puede mejorar mucho la velocidad de respuesta. Le preparé una propuesta para captar y atender pacientes 24/7. ¿Se la comparto?",
          "outreach_sequence": [
            {"day": 1, "label": "day_1_hook", "goal": "abrir conversación", "message": "Vi una oportunidad clara para captar pacientes sin perderlos por falta de seguimiento."},
            {"day": 3, "label": "day_3_follow_up", "goal": "reforzar dolor", "message": "Le escribo de nuevo porque muchas clínicas pierden citas por no responder a tiempo."},
            {"day": 7, "label": "day_7_case_study", "goal": "bajar riesgo", "message": "Le comparto un caso donde automatizar WhatsApp ayudó a subir citas y reducir tiempos muertos."},
            {"day": 14, "label": "day_14_urgency", "goal": "activar decisión", "message": "Si esto sigue igual, el costo no es la herramienta: son los pacientes que ya no regresan."}
          ]
        }
        """

    monkeypatch.setattr(forge, "_call_claude", _fake_call)

    async def _run() -> CompleteOffer:
        return await forge.forge_offer(
            OpportunityInput(
                business_name="Clinica Santa Maria",
                vertical="clinics",
                city="Guadalajara",
                has_website=False,
                has_bot=False,
                estimated_market_size=180,
                pain_score=0.84,
                payment_capacity_score=0.83,
                competition_score=0.28,
                stack_fit_score=0.94,
                source="google_maps",
            )
        )

    result = asyncio.run(_run())
    assert result.offer.name == "Paciente 24/7"
    assert result.offer.price_mxn == 6900
    assert result.offer_id
    assert len(result.outreach_sequence) == 4
    assert result.patterns_used
    assert result.patterns_used[0]["pattern_used"] == "pain_loss"
    assert result.generation_model == forge.model
    assert result.stripe_payment_url


def test_offer_forge_fallback_when_claude_fails(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CREATIVE_MEMORY_DB_PATH", str(tmp_path / "creative_memory.sqlite3"))

    save_creative_pattern(
        CreativePattern(
            domain="campaign_execution",
            offer="Restaurante Web + Chatbot",
            hook="Tus reservaciones se te van por WhatsApp",
            angle="reservas y seguimiento",
            cta="Te comparto un ejemplo real",
            pattern_used="demo_result",
            critic_score=87.0,
            reply_rate=0.31,
        )
    )

    forge = OfferForge()

    async def _boom(_: str) -> str:
        raise RuntimeError("anthropic unavailable")

    monkeypatch.setattr(forge, "_call_claude", _boom)

    async def _run() -> CompleteOffer:
        return await forge.forge_offer(
            OpportunityInput(
                business_name="La Mesa Roja",
                vertical="restaurants",
                city="Zapopan",
                has_website=False,
                has_bot=False,
                estimated_market_size=240,
                pain_score=0.79,
                payment_capacity_score=0.69,
                competition_score=0.32,
                stack_fit_score=0.88,
                source="complaints",
            )
        )

    result = asyncio.run(_run())
    assert result.offer.name == "Restaurants Web + Chatbot"
    assert result.generation_model.endswith(":fallback")
    assert len(result.outreach_sequence) == 4
    assert "La Mesa Roja" in result.whatsapp_copy
    assert result.offer_id


def test_generate_landing_page_saves_html_with_vertical_theme(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CREATIVE_MEMORY_DB_PATH", str(tmp_path / "creative_memory.sqlite3"))
    monkeypatch.setenv("OFFER_LANDINGS_DIR", str(tmp_path / "landings"))
    monkeypatch.setenv("STRIPE_PAYMENT_LINK", "https://buy.stripe.com/test_clinic_offer")

    save_creative_pattern(
        CreativePattern(
            domain="campaign_execution",
            offer="Clinica Web + Chatbot",
            hook="Tus pacientes te escriben y nadie responde a tiempo",
            angle="captacion y seguimiento de pacientes",
            cta="Te muestro como quedaria",
            pattern_used="pain_loss",
            critic_score=91.0,
        )
    )

    forge = OfferForge()

    async def _boom(_: str) -> str:
        raise RuntimeError("skip llm")

    monkeypatch.setattr(forge, "_call_claude", _boom)

    async def _run() -> CompleteOffer:
        return await forge.forge_offer(
            OpportunityInput(
                business_name="Clinica Azul",
                vertical="clinics",
                city="Guadalajara",
                has_website=False,
                has_bot=False,
                estimated_market_size=200,
                pain_score=0.82,
                payment_capacity_score=0.8,
                competition_score=0.24,
                stack_fit_score=0.94,
                source="google_maps",
            )
        )

    offer = asyncio.run(_run())
    html_output = forge.generate_landing_page(offer)
    expected_file = tmp_path / "landings" / f"{offer.offer_id}.html"

    assert expected_file.exists()
    assert offer.landing_path == str(expected_file)
    assert "Pagar con Stripe" in html_output
    assert "https://buy.stripe.com/test_clinic_offer" in html_output
    assert "#1d4ed8" in html_output
    assert "Prueba social" in html_output


def test_generate_ad_creatives_avoids_fatigued_hooks(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CREATIVE_MEMORY_DB_PATH", str(tmp_path / "creative_memory.sqlite3"))

    save_creative_pattern(
        CreativePattern(
            domain="campaign_execution",
            offer="Clinica Web + Chatbot",
            hook="Hook fresco para clinicas que convierten mejor",
            angle="captacion y seguimiento de pacientes",
            cta="Te comparto la propuesta",
            pattern_used="pain_loss",
            critic_score=91.0,
            close_rate=0.21,
        )
    )
    save_creative_pattern(
        CreativePattern(
            domain="campaign_execution",
            offer="Clinica Web + Chatbot",
            hook="Hook fatigado que ya no funciona",
            angle="captacion de pacientes",
            cta="Escribeme",
            pattern_used="authority",
            critic_score=55.0,
            ctr=0.008,
            reply_rate=0.09,
            close_rate=0.0,
        )
    )

    forge = OfferForge()

    async def _boom(_: str) -> str:
        raise RuntimeError("skip llm")

    monkeypatch.setattr(forge, "_call_claude", _boom)

    async def _run() -> CompleteOffer:
        return await forge.forge_offer(
            OpportunityInput(
                business_name="Clinica Norte",
                vertical="clinics",
                city="Guadalajara",
                has_website=False,
                has_bot=False,
                estimated_market_size=200,
                pain_score=0.84,
                payment_capacity_score=0.81,
                competition_score=0.26,
                stack_fit_score=0.93,
                source="google_maps",
            )
        )

    offer = asyncio.run(_run())
    creatives = forge.generate_ad_creatives(offer)

    assert len(creatives) == 3
    assert all(isinstance(item, AdCreative) for item in creatives)
    assert all(item.cta_button_text for item in creatives)
    assert all(item.suggested_visual_description for item in creatives)
    assert all("Activa hoy" in item.body for item in creatives)
    assert not any("Hook fatigado que ya no funciona" in item.headline for item in creatives)
    assert any("Hook fresco para clinicas que convierten mejor" in item.headline for item in creatives)


def test_save_and_list_generated_offers(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CREATIVE_MEMORY_DB_PATH", str(tmp_path / "creative_memory.sqlite3"))
    monkeypatch.setenv("FORGE_DB_PATH", str(tmp_path / "offer_forge.sqlite3"))

    save_creative_pattern(
        CreativePattern(
            domain="campaign_execution",
            offer="Clinica Web + Chatbot",
            hook="Tu clínica puede responder más rápido",
            angle="seguimiento de pacientes",
            cta="Te comparto la propuesta",
            pattern_used="pain_loss",
            critic_score=88.0,
        )
    )
    forge = OfferForge()

    async def _boom(_: str) -> str:
        raise RuntimeError("skip llm")

    monkeypatch.setattr(forge, "_call_claude", _boom)

    async def _run() -> CompleteOffer:
        return await forge.forge_offer(
            OpportunityInput(
                business_name="Clinica Prisma",
                vertical="clinics",
                city="Guadalajara",
                has_website=False,
                has_bot=False,
                estimated_market_size=220,
                pain_score=0.84,
                payment_capacity_score=0.82,
                competition_score=0.22,
                stack_fit_score=0.93,
                source="google_maps",
            )
        )

    offer = asyncio.run(_run())
    landing_html = forge.generate_landing_page(offer)
    creatives = forge.generate_ad_creatives(offer)
    saved = save_generated_offer(offer, landing_html=landing_html, ad_creatives=creatives)
    rows = list_generated_offers(limit=10)

    assert saved.offer_id == offer.offer_id
    assert len(rows) == 1
    assert rows[0].offer.name == offer.offer.name
    assert rows[0].landing_html is not None
    assert len(rows[0].ad_creatives) == 3


def test_revenue_brain_daily_cycle_generates_and_persists_offers(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("FORGE_DB_PATH", str(tmp_path / "offer_forge.sqlite3"))
    monkeypatch.setenv("CREATIVE_MEMORY_DB_PATH", str(tmp_path / "creative_memory.sqlite3"))

    save_creative_pattern(
        CreativePattern(
            domain="campaign_execution",
            offer="Clinica Web + Chatbot",
            hook="Leads fríos por WhatsApp te cuestan citas",
            angle="captacion y seguimiento",
            cta="Te explico cómo se implementa",
            pattern_used="pain_loss",
            critic_score=90.0,
        )
    )

    brain = RevenueBrain()

    async def _fake_forge(opportunity: OpportunityInput) -> CompleteOffer:
        return CompleteOffer(
            offer_id=f"offer-{opportunity.business_name.lower().replace(' ', '-')}",
            vertical=opportunity.vertical,
            opportunity_name=opportunity.business_name,
            offer={
                "name": "Oferta Hunter",
                "description": "Oferta generada para captar y seguir leads.",
                "features": ["Landing", "Bot", "Seguimiento"],
                "price_mxn": 6500,
                "guarantee": "Garantía base",
                "timeline": "7 días",
            },
            whatsapp_copy="Tengo una propuesta lista para tu negocio.",
            outreach_sequence=[
                {"day": 1, "label": "day_1_hook", "goal": "abrir", "message": "Hook"},
                {"day": 3, "label": "day_3_follow_up", "goal": "follow", "message": "Follow"},
                {"day": 7, "label": "day_7_case_study", "goal": "proof", "message": "Caso"},
                {"day": 14, "label": "day_14_urgency", "goal": "urgency", "message": "Urgencia"},
            ],
            patterns_used=[],
            generation_model="test-model",
            stripe_payment_url="https://buy.stripe.com/test_offer",
        )

    monkeypatch.setattr(brain.offer_forge, "forge_offer", _fake_forge)
    monkeypatch.setattr(brain.offer_forge, "generate_landing_page", lambda offer: "<html>landing</html>")
    monkeypatch.setattr(
        brain.offer_forge,
        "generate_ad_creatives",
        lambda offer: [
            AdCreative(
                headline="Headline",
                body="Pain solución social proof urgencia",
                cta_button_text="Quiero activarlo",
                suggested_visual_description="Visual",
            )
        ]
        * 3,
    )

    async def _run() -> None:
        briefing = await brain.daily_cycle(
            {
                "city": "Guadalajara",
                "google_maps_results": [
                    {
                        "business_name": "Clinica Delta",
                        "vertical": "clinics",
                        "city": "Guadalajara",
                        "has_website": False,
                        "has_bot": False,
                        "estimated_market_size": 520,
                        "pain_score": 0.97,
                        "payment_capacity_score": 0.94,
                        "competition_score": 0.08,
                        "stack_fit_score": 0.98,
                        "trend_score": 68,
                        "engagement_score": 45,
                    },
                    {
                        "business_name": "Clinica Nova",
                        "vertical": "clinics",
                        "city": "Guadalajara",
                        "has_website": False,
                        "has_bot": False,
                        "estimated_market_size": 480,
                        "pain_score": 0.95,
                        "payment_capacity_score": 0.9,
                        "competition_score": 0.1,
                        "stack_fit_score": 0.97,
                        "trend_score": 61,
                        "engagement_score": 40,
                    },
                ],
                "job_board_results": [],
                "competitor_activity_results": [],
                "complaints_results": [],
                "arbitrage_results": [],
                "internal_demand_results": [],
                "pipeline_leads": [],
                "customers": [],
                "campaign_context": {},
            }
        )
        assert briefing.diagnosis.metrics["generated_offers"]

    asyncio.run(_run())
    rows = list_generated_offers(limit=10)
    assert len(rows) >= 1
    assert any(row.landing_html for row in rows)
    assert any(len(row.ad_creatives) == 3 for row in rows)


def test_forge_offers_endpoint_returns_generated_offers(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("FORGE_DB_PATH", str(tmp_path / "offer_forge.sqlite3"))
    monkeypatch.setenv("CLIENT_TOKENS_JSON", '{"test-client":"test-token"}')
    monkeypatch.setenv("KAN_CLIENT_ID", "test-client")

    offer = CompleteOffer(
        offer_id="offer-endpoint-1",
        vertical="clinics",
        opportunity_name="Clinica Endpoint",
        offer={
            "name": "Oferta Endpoint",
            "description": "Oferta guardada",
            "features": ["Landing", "Bot"],
            "price_mxn": 5500,
            "guarantee": "Garantía",
            "timeline": "7 días",
        },
        whatsapp_copy="Mensaje",
        outreach_sequence=[
            {"day": 1, "label": "day_1_hook", "goal": "abrir", "message": "Hook"},
            {"day": 3, "label": "day_3_follow_up", "goal": "follow", "message": "Follow"},
            {"day": 7, "label": "day_7_case_study", "goal": "proof", "message": "Caso"},
            {"day": 14, "label": "day_14_urgency", "goal": "urgency", "message": "Urgencia"},
        ],
        patterns_used=[],
        generation_model="test-model",
        stripe_payment_url="https://buy.stripe.com/test_offer",
    )
    save_generated_offer(offer, landing_html="<html>saved</html>", ad_creatives=[])

    with TestClient(app) as client:
        response = client.get(
            "/forge/offers",
            headers={"X-Client-Token": "test-token"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload
    assert payload[0]["offer_id"] == "offer-endpoint-1"
