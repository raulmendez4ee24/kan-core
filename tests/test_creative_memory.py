from __future__ import annotations

import importlib

from brain import creative_memory as creative_memory_module


def test_creative_memory_persists_across_reload(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "creative.sqlite3"
    monkeypatch.setenv("CREATIVE_MEMORY_DB_PATH", str(db_path))

    creative_memory_module.save_creative_pattern(
        creative_memory_module.CreativePattern(
            domain="campaign_execution",
            offer="Growth",
            hook="Cada lead que dejas sin seguimiento te cuesta ventas.",
            angle="Dolor concreto de respuesta lenta y seguimiento flojo.",
            cta="agendar una llamada",
            pattern_used="pain_loss",
            critic_score=0.91,
            campaign_id="cmp-persist",
        )
    )

    reloaded = importlib.reload(creative_memory_module)
    patterns = reloaded.get_top_patterns("campaign_execution")

    assert len(patterns) == 1
    assert patterns[0].hook == "Cada lead que dejas sin seguimiento te cuesta ventas."


def test_creative_memory_avoids_duplicates(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("CREATIVE_MEMORY_DB_PATH", str(tmp_path / "creative.sqlite3"))

    pattern = creative_memory_module.CreativePattern(
        domain="campaign_execution",
        offer="Growth",
        hook="No necesitas mas trafico; necesitas responder y seguir mejor.",
        angle="Hook contrarian sobre seguimiento.",
        cta="pedirme una demo",
        pattern_used="contrarian",
        critic_score=0.86,
        campaign_id="cmp-dup",
    )

    creative_memory_module.save_creative_pattern(pattern)
    creative_memory_module.save_creative_pattern(pattern)

    patterns = creative_memory_module.get_top_patterns("campaign_execution")
    assert len(patterns) == 1


def test_creative_memory_ranks_by_close_booking_reply_ctr_then_critic(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("CREATIVE_MEMORY_DB_PATH", str(tmp_path / "creative.sqlite3"))

    creative_memory_module.save_creative_pattern(
        creative_memory_module.CreativePattern(
            domain="campaign_execution",
            offer="Growth",
            hook="Hook A",
            angle="Angle A",
            cta="cta",
            pattern_used="proof",
            critic_score=0.7,
            campaign_id="cmp-a",
            ctr=2.1,
            reply_rate=0.4,
            booking_rate=0.18,
            close_rate=0.12,
        )
    )
    creative_memory_module.save_creative_pattern(
        creative_memory_module.CreativePattern(
            domain="campaign_execution",
            offer="Growth",
            hook="Hook B",
            angle="Angle B",
            cta="cta",
            pattern_used="authority",
            critic_score=0.95,
            campaign_id="cmp-b",
            ctr=3.8,
            reply_rate=0.6,
            booking_rate=0.22,
            close_rate=0.05,
        )
    )
    creative_memory_module.save_creative_pattern(
        creative_memory_module.CreativePattern(
            domain="campaign_execution",
            offer="Growth",
            hook="Hook C",
            angle="Angle C",
            cta="cta",
            pattern_used="curiosity_gap",
            critic_score=0.9,
            campaign_id="cmp-c",
        )
    )

    ranked = creative_memory_module.get_top_patterns("campaign_execution", limit=3)

    assert [item.hook for item in ranked] == ["Hook A", "Hook B", "Hook C"]


def test_update_pattern_metrics_updates_existing_campaign_rows(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("CREATIVE_MEMORY_DB_PATH", str(tmp_path / "creative.sqlite3"))

    creative_memory_module.save_creative_pattern(
        creative_memory_module.CreativePattern(
            domain="campaign_execution",
            offer="Growth",
            hook="Hook metrics",
            angle="Angle metrics",
            cta="cta",
            pattern_used="demo_result",
            critic_score=0.82,
            campaign_id="cmp-metrics",
        )
    )

    updated = creative_memory_module.update_pattern_metrics(
        "cmp-metrics",
        ctr=3.2,
        reply_rate=0.41,
        booking_rate=0.19,
        close_rate=0.08,
    )

    pattern = creative_memory_module.get_top_patterns("campaign_execution", limit=1)[0]
    assert updated == 1
    assert pattern.ctr == 3.2
    assert pattern.reply_rate == 0.41
    assert pattern.booking_rate == 0.19
    assert pattern.close_rate == 0.08
