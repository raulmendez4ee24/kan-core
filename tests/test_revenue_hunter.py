from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from brain.revenue_operators.hunter import HunterOperator, OpportunityScore


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


def test_hunter_sends_template_outbound_once_and_skips_duplicate(monkeypatch, tmp_path: Path) -> None:
    operator = HunterOperator()
    monkeypatch.setenv("HUNTER_OUTBOUND_DB_PATH", str(tmp_path / "hunter_outbound.sqlite3"))
    monkeypatch.setattr(
        HunterOperator,
        "_now_local",
        staticmethod(lambda: datetime(2026, 3, 16, 10, 0, tzinfo=ZoneInfo("America/Mexico_City"))),
    )
    calls: list[dict] = []

    async def _fake_sender(**kwargs):
        calls.append(kwargs)
        return {"id": "msg_123", "status": "accepted"}

    monkeypatch.setattr(
        "brain.revenue_operators.hunter.send_ycloud_whatsapp_template_message",
        _fake_sender,
    )

    opportunity = OpportunityScore(
        business_name="Clínica Xaviera",
        vertical="clinics",
        city="Zapopan",
        score=82.47,
        market_score=18.0,
        pain_score=86.0,
        payment_capacity_score=88.0,
        competition_score=46.0,
        stack_fit_score=95.0,
        metadata={"phone": "33 2148 7586", "place_id": "place_1"},
    )

    async def _run():
        first = await operator.send_clinic_outbound_message(opportunity)
        second = await operator.send_clinic_outbound_message(opportunity)
        assert first.status == "sent"
        assert first.phone_number == "+523321487586"
        assert second.status == "skipped_duplicate"
        assert len(calls) == 1
        assert calls[0]["template_name"] == "outbound_prospecto_v1"
        assert calls[0]["body_variables"] == [
            "Clínica Xaviera",
            "Web + Chatbot para Clínicas",
            "Hoy puedes responder más rápido y agendar más citas por WhatsApp sin depender de contestar manualmente.",
        ]

    asyncio.run(_run())


def test_hunter_respects_daily_whatsapp_limit(monkeypatch, tmp_path: Path) -> None:
    operator = HunterOperator()
    monkeypatch.setenv("HUNTER_OUTBOUND_DB_PATH", str(tmp_path / "hunter_outbound.sqlite3"))
    monkeypatch.setattr(
        HunterOperator,
        "_now_local",
        staticmethod(lambda: datetime(2026, 3, 16, 10, 0, tzinfo=ZoneInfo("America/Mexico_City"))),
    )
    calls: list[str] = []

    async def _fake_sender(**kwargs):
        calls.append(kwargs["to"])
        return {"id": f"msg_{len(calls)}", "status": "accepted"}

    monkeypatch.setattr(
        "brain.revenue_operators.hunter.send_ycloud_whatsapp_template_message",
        _fake_sender,
    )

    async def _run():
        opportunities = [
            OpportunityScore(
                business_name=f"Clínica {idx}",
                vertical="clinics",
                city="Guadalajara",
                score=80.0 + idx,
                market_score=18.0,
                pain_score=86.0,
                payment_capacity_score=88.0,
                competition_score=46.0,
                stack_fit_score=95.0,
                metadata={"phone": f"33100000{idx:02d}", "place_id": f"place_{idx}"},
            )
            for idx in range(1, 4)
        ]
        results = await operator.send_clinic_outbound_campaign(opportunities, daily_limit=2)
        assert [item.status for item in results] == ["sent", "sent", "skipped_rate_limit"]
        assert len(calls) == 2

    asyncio.run(_run())


def test_hunter_business_hours_helper() -> None:
    mx = ZoneInfo("America/Mexico_City")
    assert HunterOperator.is_business_hours(datetime(2026, 3, 16, 9, 0, tzinfo=mx))
    assert HunterOperator.is_business_hours(datetime(2026, 3, 21, 17, 59, tzinfo=mx))
    assert not HunterOperator.is_business_hours(datetime(2026, 3, 16, 8, 59, tzinfo=mx))
    assert not HunterOperator.is_business_hours(datetime(2026, 3, 16, 18, 0, tzinfo=mx))
    assert not HunterOperator.is_business_hours(datetime(2026, 3, 22, 11, 0, tzinfo=mx))


def test_hunter_queues_outbound_outside_business_hours_and_flushes_later(
    monkeypatch,
    tmp_path: Path,
) -> None:
    operator = HunterOperator()
    monkeypatch.setenv("HUNTER_OUTBOUND_DB_PATH", str(tmp_path / "hunter_outbound.sqlite3"))
    calls: list[dict] = []

    async def _fake_sender(**kwargs):
        calls.append(kwargs)
        return {"id": "msg_queued_1", "status": "accepted"}

    monkeypatch.setattr(
        "brain.revenue_operators.hunter.send_ycloud_whatsapp_template_message",
        _fake_sender,
    )

    opportunity = OpportunityScore(
        business_name="Unidad Médica Altagracia",
        vertical="clinics",
        city="Zapopan",
        score=83.54,
        market_score=18.0,
        pain_score=94.0,
        payment_capacity_score=88.0,
        competition_score=46.0,
        stack_fit_score=95.0,
        metadata={"phone": "33 3656 3145", "place_id": "place_queue_1"},
    )

    async def _run():
        monkeypatch.setattr(HunterOperator, "_now_local", staticmethod(lambda: datetime(2026, 3, 16, 6, 0, tzinfo=ZoneInfo("America/Mexico_City"))))
        queued = await operator.send_clinic_outbound_message(opportunity)
        assert queued.status == "queued"
        assert queued.reason == "outside_business_hours"
        assert calls == []

        monkeypatch.setattr(HunterOperator, "_now_local", staticmethod(lambda: datetime(2026, 3, 16, 9, 5, tzinfo=ZoneInfo("America/Mexico_City"))))
        flushed = await operator.flush_queued_clinic_outbound_messages()
        assert len(flushed) == 1
        assert flushed[0].status == "sent"
        assert flushed[0].phone_number == "+523336563145"
        assert len(calls) == 1

    asyncio.run(_run())
