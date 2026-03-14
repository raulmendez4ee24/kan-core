from __future__ import annotations

import asyncio
import importlib

from brain import attribution_engine as attribution_engine_module


def test_full_lead_lifecycle_and_campaign_roi(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "attribution.sqlite3"
    monkeypatch.setenv("ATTRIBUTION_ENGINE_DB_PATH", str(db_path))
    engine = importlib.reload(attribution_engine_module)

    async def scenario() -> None:
        await engine.track_lead_source(
            lead_id="lead-001",
            channel="whatsapp",
            campaign_id="cmp-meta-001",
            spend=10.0,
            metadata={"offer": "Growth"},
        )
        await engine.track_conversation(
            lead_id="lead-001",
            channel="whatsapp",
            conversation_id="conv-001",
            meaningful_reply=False,
        )
        await engine.track_conversation(
            lead_id="lead-001",
            channel="whatsapp",
            conversation_id="conv-001",
            meaningful_reply=True,
        )
        await engine.track_booking(
            lead_id="lead-001",
            booking_id="book-001",
            scheduled_at="2026-03-14T10:00:00+00:00",
            confirmed=True,
        )
        await engine.track_close(
            lead_id="lead-001",
            close_id="close-001",
            revenue=50.0,
            currency="USD",
            status="won",
        )

        funnel = await engine.get_full_funnel("cmp-meta-001")
        roi = await engine.get_campaign_roi("cmp-meta-001")

        assert funnel.leads == 1
        assert funnel.conversations == 1
        assert funnel.replies == 1
        assert funnel.bookings == 1
        assert funnel.closes == 1
        assert funnel.revenue == 50.0
        assert funnel.spend == 10.0
        assert funnel.conversation_rate == 1.0
        assert funnel.reply_rate == 1.0
        assert funnel.booking_rate == 1.0
        assert funnel.close_rate == 1.0
        assert funnel.roas == 5.0
        assert funnel.roi == 4.0

        assert roi.key == "cmp-meta-001"
        assert roi.roas == 5.0
        assert roi.roi == 4.0

    asyncio.run(scenario())


def test_channel_roi_aggregates_multiple_campaigns(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "attribution.sqlite3"
    monkeypatch.setenv("ATTRIBUTION_ENGINE_DB_PATH", str(db_path))
    engine = importlib.reload(attribution_engine_module)

    async def scenario() -> None:
        await engine.track_lead_source(
            lead_id="lead-a",
            channel="whatsapp",
            campaign_id="cmp-a",
            spend=5.0,
        )
        await engine.track_lead_source(
            lead_id="lead-b",
            channel="whatsapp",
            utm_campaign="utm-b",
            spend=15.0,
        )
        await engine.track_conversation(
            lead_id="lead-a",
            channel="whatsapp",
            conversation_id="conv-a",
            meaningful_reply=True,
        )
        await engine.track_conversation(
            lead_id="lead-b",
            channel="whatsapp",
            conversation_id="conv-b",
            meaningful_reply=False,
        )
        await engine.track_booking(
            lead_id="lead-a",
            booking_id="book-a",
            scheduled_at="2026-03-15T12:00:00+00:00",
        )
        await engine.track_close(
            lead_id="lead-a",
            close_id="close-a",
            revenue=100.0,
        )

        roi = await engine.get_channel_roi("whatsapp")

        assert roi.key == "whatsapp"
        assert roi.leads == 2
        assert roi.conversations == 2
        assert roi.replies == 1
        assert roi.bookings == 1
        assert roi.closes == 1
        assert roi.spend == 20.0
        assert roi.revenue == 100.0
        assert roi.booking_rate == 0.5
        assert roi.close_rate == 1.0
        assert roi.roas == 5.0
        assert roi.roi == 4.0

    asyncio.run(scenario())


def test_missing_campaign_resolution_does_not_break_flows(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "attribution.sqlite3"
    monkeypatch.setenv("ATTRIBUTION_ENGINE_DB_PATH", str(db_path))
    engine = importlib.reload(attribution_engine_module)

    async def scenario() -> None:
        lead = await engine.track_lead_source(
            lead_id="lead-missing",
            channel="instagram",
            spend=0.0,
        )
        conv = await engine.track_conversation(
            lead_id="lead-missing",
            channel="instagram",
            conversation_id="conv-missing",
            meaningful_reply=True,
        )
        booking = await engine.track_booking(
            lead_id="lead-missing",
            scheduled_at="2026-03-16T16:00:00+00:00",
        )
        close = await engine.track_close(
            lead_id="lead-missing",
            revenue=0.0,
            status="won",
        )

        assert lead.source_key is None
        assert conv.source_key is None
        assert booking.source_key is None
        assert close.source_key is None

        funnel = await engine.get_full_funnel("")
        assert funnel.leads == 0
        assert funnel.conversations == 0
        assert funnel.bookings == 0
        assert funnel.closes == 0

    asyncio.run(scenario())


def test_persistence_across_reload(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "attribution.sqlite3"
    monkeypatch.setenv("ATTRIBUTION_ENGINE_DB_PATH", str(db_path))
    engine = importlib.reload(attribution_engine_module)

    async def seed() -> None:
        await engine.track_lead_source(
            lead_id="lead-persist",
            channel="whatsapp",
            source_campaign="cmp-persist",
            spend=8.0,
        )
        await engine.track_conversation(
            lead_id="lead-persist",
            channel="whatsapp",
            conversation_id="conv-persist",
            meaningful_reply=True,
        )

    asyncio.run(seed())

    reloaded = importlib.reload(attribution_engine_module)

    async def verify() -> None:
        funnel = await reloaded.get_full_funnel("cmp-persist")
        assert funnel.leads == 1
        assert funnel.conversations == 1
        assert funnel.replies == 1
        assert funnel.spend == 8.0

    asyncio.run(verify())


def test_briefing_persistence_and_retrieval(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "attribution.sqlite3"
    monkeypatch.setenv("ATTRIBUTION_ENGINE_DB_PATH", str(db_path))
    engine = importlib.reload(attribution_engine_module)

    async def scenario() -> None:
        daily = await engine.save_briefing(
            briefing_type="daily",
            content={
                "generated_at": "2026-03-13T06:00:00+00:00",
                "diagnosis": {"summary": "daily summary"},
            },
            actions_executed=2,
            actions_recommended=3,
        )
        evening = await engine.save_briefing(
            briefing_type="evening",
            content={
                "generated_at": "2026-03-13T18:00:00+00:00",
                "diagnosis": {"summary": "evening summary"},
            },
            actions_executed=1,
            actions_recommended=1,
        )

        recent = await engine.get_recent_briefings(limit=7)
        latest_daily = await engine.get_latest_briefing("daily")
        latest_evening = await engine.get_latest_briefing("evening")

        assert len(recent) == 2
        assert {item.id for item in recent} == {daily.id, evening.id}
        assert latest_daily is not None
        assert latest_daily.type == "daily"
        assert latest_daily.content["diagnosis"]["summary"] == "daily summary"
        assert latest_daily.actions_executed == 2
        assert latest_daily.actions_recommended == 3
        assert latest_evening is not None
        assert latest_evening.type == "evening"
        assert latest_evening.content["diagnosis"]["summary"] == "evening summary"

    asyncio.run(scenario())
