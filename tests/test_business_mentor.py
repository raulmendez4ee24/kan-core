from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from brain.attribution_engine import track_booking, track_close, track_lead_source
from brain.business_mentor import (
    BookingSnapshot,
    BusinessMentor,
    CrmSnapshot,
    MetaCampaignSnapshot,
)
from brain.creative_memory import get_top_patterns
from main import app


def test_business_mentor_daily_briefing_sends_whatsapp(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ATTRIBUTION_ENGINE_DB_PATH", str(tmp_path / "attribution.sqlite3"))
    monkeypatch.setenv("RAUL_WHATSAPP_TO", "+5215512345678")
    monkeypatch.setenv("YCLOUD_WHATSAPP_FROM", "+5215599999999")

    sent_messages: list[dict[str, str]] = []

    async def _fake_send(*, from_number: str, to: str, text: str) -> dict[str, object]:
        sent_messages.append({"from": from_number, "to": to, "text": text})
        return {"sent": True}

    async def _run() -> None:
        now = datetime.now(timezone.utc)
        await track_lead_source(
            lead_id="lead-mentor-1",
            channel="whatsapp",
            campaign_id="campaign-mentor",
            spend=800,
        )
        await track_booking(
            lead_id="lead-mentor-1",
            booking_id="booking-mentor-1",
            scheduled_at=now + timedelta(hours=3),
            campaign_id="campaign-mentor",
            confirmed=True,
        )
        await track_close(
            lead_id="lead-mentor-1",
            close_id="close-mentor-1",
            campaign_id="campaign-mentor",
            revenue=15000,
            status="won",
        )

        mentor = BusinessMentor(whatsapp_sender=_fake_send)
        mentor.collect_crm_metrics = lambda: asyncio.sleep(0, result=CrmSnapshot(  # type: ignore[method-assign]
            active_leads=7,
            pipeline={"new": 2, "qualified": 3, "proposal_sent": 2, "won": 1, "lost": 1},
            won_count=1,
            lost_count=1,
            close_rate=0.5,
        ))
        mentor.collect_meta_metrics = lambda snapshot=None: asyncio.sleep(0, result=MetaCampaignSnapshot(  # type: ignore[method-assign]
            account_id="act_123",
            top_campaigns=[
                {"id": "camp1", "name": "Meta WA GDL", "roas": 3.4, "link_ctr": 1.2, "flags": ["fatigue"]}
            ],
            fatigued_entities=1,
            scaling_opportunities=1,
            account_flags=["fatigue"],
            summary="1 campaña con fatiga y 1 oportunidad de escala.",
        ))

        briefing = await mentor.daily_briefing()
        assert briefing.revenue.month_revenue_mxn == 15000
        assert briefing.bookings.scheduled_today == 1
        assert len(briefing.top_priorities) == 3
        assert sent_messages
        assert "Mentor diario 6:30 AM" in sent_messages[0]["text"]
        assert "Ingreso ayer" in sent_messages[0]["text"]

    asyncio.run(_run())


def test_business_mentor_weekly_review_updates_creative_memory(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ATTRIBUTION_ENGINE_DB_PATH", str(tmp_path / "attribution.sqlite3"))
    monkeypatch.setenv("CREATIVE_MEMORY_DB_PATH", str(tmp_path / "creative_memory.sqlite3"))
    monkeypatch.setenv("RAUL_WHATSAPP_TO", "+5215512345678")
    monkeypatch.setenv("YCLOUD_WHATSAPP_FROM", "+5215599999999")

    sent_messages: list[str] = []

    async def _fake_send(*, from_number: str, to: str, text: str) -> dict[str, object]:
        sent_messages.append(text)
        return {"sent": True}

    async def _real_run() -> None:
        now = datetime.now(timezone.utc)
        await track_lead_source(
            lead_id="lead-mentor-2",
            channel="whatsapp",
            campaign_id="campaign-weekly",
            spend=600,
        )
        await track_booking(
            lead_id="lead-mentor-2",
            booking_id="booking-mentor-2",
            scheduled_at=now + timedelta(hours=4),
            campaign_id="campaign-weekly",
            confirmed=True,
        )
        await track_close(
            lead_id="lead-mentor-2",
            close_id="close-mentor-2",
            campaign_id="campaign-weekly",
            revenue=9800,
            status="won",
        )

        mentor = BusinessMentor(whatsapp_sender=_fake_send)
        mentor.collect_crm_metrics = lambda: asyncio.sleep(0, result=CrmSnapshot(  # type: ignore[method-assign]
            active_leads=5,
            pipeline={"qualified": 3, "proposal_sent": 1, "won": 2, "lost": 1},
            won_count=2,
            lost_count=1,
            close_rate=0.6667,
        ))
        mentor.collect_meta_metrics = lambda snapshot=None: asyncio.sleep(0, result=MetaCampaignSnapshot(  # type: ignore[method-assign]
            account_id="act_123",
            top_campaigns=[
                {"id": "camp-week-1", "name": "Clinicas WA", "roas": 4.1, "link_ctr": 2.3, "flags": []},
                {"id": "camp-week-2", "name": "Dentistas GDL", "roas": 3.6, "link_ctr": 1.9, "flags": ["scale"]},
            ],
            fatigued_entities=0,
            scaling_opportunities=2,
            account_flags=["scale"],
            summary="2 campañas con señal clara de escala.",
        ))
        mentor.collect_bookings_metrics = lambda: asyncio.sleep(0, result=BookingSnapshot(  # type: ignore[method-assign]
            scheduled_today=2,
            next_booking_at=(now + timedelta(hours=4)).isoformat(),
            bookings_today=[{"lead_id": "lead-mentor-2"}],
        ))

        review = await mentor.weekly_review()
        patterns = get_top_patterns(domain="weekly_review", limit=5)
        assert review.creative_patterns_saved == 2
        assert patterns
        assert patterns[0].pattern_used == "weekly_review"
        assert sent_messages
        assert "Weekly review" in sent_messages[0]

    asyncio.run(_real_run())


def test_mentor_ask_endpoint(monkeypatch) -> None:
    monkeypatch.setenv("CLIENT_TOKENS_JSON", '{"test-client":"test-token"}')

    async def _fake_answer(self: BusinessMentor, question: str) -> str:
        return f"mentor:{question}"

    monkeypatch.setattr("brain.business_mentor.BusinessMentor.answer_question", _fake_answer)
    with TestClient(app) as client:
        response = client.post(
            "/mentor/ask",
            headers={"X-Client-Id": "test-client", "X-Client-Token": "test-token"},
            json={"question": "Como cierro mas ventas?"},
        )

    assert response.status_code == 200
    assert response.json()["answer"] == "mentor:Como cierro mas ventas?"
