from __future__ import annotations

import importlib
import sqlite3
from pathlib import Path


def _reload_modules(monkeypatch, db_path: Path):
    monkeypatch.setenv("CREATIVE_MEMORY_DB_PATH", str(db_path))

    from brain import creative_memory as creative_memory_module
    from brain import creative_event_tracker as tracker_module

    creative_memory_module = importlib.reload(creative_memory_module)
    tracker_module = importlib.reload(tracker_module)
    tracker_module._reset_connection()
    return creative_memory_module, tracker_module


def test_tracker_runtime_persists_usage_and_updates_memory(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "creative_tracker_runtime.sqlite3"
    memory, tracker = _reload_modules(monkeypatch, db_path)

    memory.save_creative_pattern(
        memory.CreativePattern(
            domain="campaign_execution",
            offer="Growth",
            hook="Tu negocio pierde leads por responder tarde.",
            angle="Perdida por seguimiento lento.",
            cta="Escribeme y te muestro como lo arreglo.",
            pattern_used="pain_loss",
            critic_score=0.84,
            campaign_id="cmp-runtime-001",
        )
    )

    assert tracker.register_creative_pattern_usage(
        "cmp-runtime-001",
        domain="campaign_execution",
        offer="Growth",
        hook="Tu negocio pierde leads por responder tarde.",
        angle="Perdida por seguimiento lento.",
        cta="Escribeme y te muestro como lo arreglo.",
        pattern_used="pain_loss",
        traffic_temperature="warm",
    )

    assert tracker.record_campaign_metrics("cmp-runtime-001", impressions=1000, clicks=50)
    assert tracker.record_whatsapp_outcome(
        "cmp-runtime-001",
        conversation_started=True,
        replied=True,
        meeting_requested=True,
        booked=True,
    )
    assert tracker.record_sales_outcome("cmp-runtime-001", showed=True, closed=True)

    metrics = tracker.recalculate_and_update_pattern_metrics("cmp-runtime-001")
    assert metrics["ctr"] == 0.05
    assert metrics["reply_rate"] == 1.0
    assert metrics["booking_rate"] == 1.0
    assert metrics["close_rate"] == 1.0

    pattern = memory.get_top_patterns("campaign_execution", limit=1)[0]
    assert pattern.campaign_id == "cmp-runtime-001"
    assert pattern.ctr == 0.05
    assert pattern.reply_rate == 1.0
    assert pattern.booking_rate == 1.0
    assert pattern.close_rate == 1.0

    with sqlite3.connect(str(db_path)) as conn:
        outcome_row = conn.execute(
            "SELECT impressions, clicks, conversations, replies, meeting_requests, bookings, shows, closes "
            "FROM creative_outcomes WHERE campaign_id = ?",
            ("cmp-runtime-001",),
        ).fetchone()
        link_row = conn.execute(
            "SELECT domain, offer, pattern_used, traffic_temperature "
            "FROM creative_campaign_links WHERE campaign_id = ?",
            ("cmp-runtime-001",),
        ).fetchone()

    assert outcome_row == (1000, 50, 1, 1, 1, 1, 1, 1)
    assert link_row == ("campaign_execution", "Growth", "pain_loss", "warm")


def test_tracker_runtime_survives_reload(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "creative_tracker_runtime.sqlite3"
    memory, tracker = _reload_modules(monkeypatch, db_path)

    memory.save_creative_pattern(
        memory.CreativePattern(
            domain="campaign_execution",
            offer="Starter",
            hook="No necesitas mas trafico; necesitas mejor seguimiento.",
            angle="Contraste entre trafico y conversion.",
            cta="Pide una demo.",
            pattern_used="contrarian",
            critic_score=0.78,
            campaign_id="cmp-runtime-002",
        )
    )
    tracker.register_creative_pattern_usage(
        "cmp-runtime-002",
        domain="campaign_execution",
        offer="Starter",
        hook="No necesitas mas trafico; necesitas mejor seguimiento.",
        angle="Contraste entre trafico y conversion.",
        cta="Pide una demo.",
        pattern_used="contrarian",
        traffic_temperature="cold",
    )
    tracker.record_campaign_metrics("cmp-runtime-002", impressions=200, clicks=8)

    _, tracker = _reload_modules(monkeypatch, db_path)
    metrics = tracker.recalculate_and_update_pattern_metrics("cmp-runtime-002")

    assert metrics["impressions"] == 200
    assert metrics["clicks"] == 8
    assert metrics["ctr"] == 0.04


def test_tracker_runtime_missing_campaign_id_is_safe(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "creative_tracker_runtime.sqlite3"
    _, tracker = _reload_modules(monkeypatch, db_path)

    assert tracker.resolve_campaign_source(None, "utm-a", "src-b", "ad-c") == "utm-a"
    assert tracker.resolve_campaign_source(None, None, "src-b", "ad-c") == "src-b"
    assert tracker.resolve_campaign_source(None, None, None, "ad-c") == "ad-c"

    assert not tracker.register_creative_pattern_usage(
        "",
        domain="campaign_execution",
        offer="Growth",
        hook="hook",
        angle="angle",
        cta="cta",
        pattern_used="proof",
        traffic_temperature="warm",
    )
    assert not tracker.record_campaign_metrics("", impressions=100, clicks=5)
    assert not tracker.record_whatsapp_outcome("", conversation_started=True, replied=True)
    assert not tracker.record_sales_outcome("", showed=True, closed=True)
    assert tracker.recalculate_and_update_pattern_metrics("") == {}
