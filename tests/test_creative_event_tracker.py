from __future__ import annotations

from brain import creative_event_log as _log
from brain import creative_memory as _mem
from brain import creative_event_tracker as _tracker
from brain.creative_event_tracker import (
    on_ad_metrics,
    on_campaign_deployed,
    on_close,
    on_conversation_started,
    on_meeting_scheduled,
    on_reply,
    on_show,
)


def _setup(monkeypatch, tmp_path):
    db = tmp_path / "tracker_e2e.sqlite3"
    monkeypatch.setenv("CREATIVE_MEMORY_DB_PATH", str(db))
    _log._reset_connection()
    _tracker._reset_connection()


# -------------------------------------------------------------------
# Full E2E: deploy → conversation → reply → meeting → show → close
# -------------------------------------------------------------------


def test_full_funnel_updates_creative_patterns(monkeypatch, tmp_path):
    """The critical test: walk a lead through the entire funnel and verify
    that the creative_patterns table receives the correct derived rates."""
    _setup(monkeypatch, tmp_path)

    pattern = _mem.save_creative_pattern(
        _mem.CreativePattern(
            domain="campaign_execution",
            offer="Growth",
            hook="Cada lead sin seguimiento te cuesta ventas.",
            angle="Dolor de respuesta lenta.",
            cta="agendar llamada",
            pattern_used="pain_loss",
            critic_score=0.88,
        )
    )
    assert pattern.campaign_id is None, "pattern starts without campaign_id"

    on_campaign_deployed("cmp-e2e-001", pattern.id, "campaign_execution")

    linked = _mem.get_top_patterns("campaign_execution", limit=1)
    assert linked[0].campaign_id == "cmp-e2e-001", (
        "on_campaign_deployed must set campaign_id on the pattern row"
    )

    on_conversation_started(
        lead_id="+5211111111",
        channel="whatsapp",
        raw_text="Hola, vi su anuncio ref:cmp-e2e-001 y quiero info",
    )

    on_reply("+5211111111", "whatsapp", is_qualified=True)

    on_meeting_scheduled("+5211111111", "2026-03-20T10:00:00")

    on_show("+5211111111")

    on_close("+5211111111", value_usd=3500.0)

    # -- Verify the metrics cache ---
    cached = _log.get_cached_metrics("cmp-e2e-001")
    assert cached is not None
    assert cached["conversations"] == 1
    assert cached["qualified_replies"] == 1
    assert cached["meetings_scheduled"] == 1
    assert cached["shows"] == 1
    assert cached["closes"] == 1
    assert cached["total_revenue_usd"] == 3500.0

    assert cached["response_rate"] == 1.0
    assert cached["booking_rate"] == 1.0
    assert cached["show_rate"] == 1.0
    assert cached["close_rate"] == 1.0
    assert cached["revenue_per_conversation"] == 3500.0

    # -- Verify creative_patterns was updated ---
    final = _mem.get_top_patterns("campaign_execution", limit=1)[0]
    assert final.reply_rate == 1.0, "response_rate → reply_rate propagation"
    assert final.booking_rate == 1.0
    assert final.close_rate == 1.0


# -------------------------------------------------------------------
# Deployment without prior campaign_id
# -------------------------------------------------------------------


def test_deploy_links_pattern_to_campaign(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)

    pattern = _mem.save_creative_pattern(
        _mem.CreativePattern(
            domain="campaign_execution",
            offer="Growth",
            hook="Hook deploy test",
            angle="Angle deploy test",
            cta="demo",
            pattern_used="contrarian",
            critic_score=0.80,
        )
    )
    assert pattern.campaign_id is None

    on_campaign_deployed("cmp-dep-01", pattern.id, "campaign_execution")

    refreshed = _mem.get_top_patterns("campaign_execution", limit=1)[0]
    assert refreshed.campaign_id == "cmp-dep-01"

    deps = _log.get_deployments_for_campaign("cmp-dep-01")
    assert len(deps) == 1
    assert deps[0]["pattern_id"] == pattern.id


# -------------------------------------------------------------------
# Ad metrics propagate CTR
# -------------------------------------------------------------------


def test_ad_metrics_propagates_ctr(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)

    pattern = _mem.save_creative_pattern(
        _mem.CreativePattern(
            domain="campaign_execution",
            offer="Growth",
            hook="Hook ctr test",
            angle="Angle ctr test",
            cta="demo",
            pattern_used="proof",
            critic_score=0.78,
        )
    )

    on_campaign_deployed("cmp-ctr", pattern.id, "campaign_execution")
    on_ad_metrics("cmp-ctr", impressions=2000, clicks=80)

    refreshed = _mem.get_top_patterns("campaign_execution", limit=1)[0]
    assert refreshed.ctr is not None
    assert abs(refreshed.ctr - 0.04) < 0.0001


# -------------------------------------------------------------------
# Unattributed event still recorded
# -------------------------------------------------------------------


def test_unattributed_conversation_recorded(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)

    on_conversation_started(
        lead_id="+5299999999",
        channel="whatsapp",
        raw_text="Hola quiero informes",
    )

    assert _log.get_unattributed_event_count() == 1


# -------------------------------------------------------------------
# Attribution persists across funnel stages
# -------------------------------------------------------------------


def test_attribution_carries_through_funnel(monkeypatch, tmp_path):
    """ref:cmp-xxx in the first message should propagate to all later events
    even though on_reply/on_show/etc. receive no campaign hint."""
    _setup(monkeypatch, tmp_path)

    pattern = _mem.save_creative_pattern(
        _mem.CreativePattern(
            domain="campaign_execution",
            offer="Growth",
            hook="Hook carry test",
            angle="Angle carry test",
            cta="demo",
            pattern_used="authority",
            critic_score=0.82,
        )
    )
    on_campaign_deployed("cmp-carry", pattern.id, "campaign_execution")

    on_conversation_started(
        "+5233333333", "whatsapp", "Me interesa ref:cmp-carry"
    )
    on_reply("+5233333333", "whatsapp", is_qualified=True)
    on_close("+5233333333", value_usd=1000.0)

    cached = _log.get_cached_metrics("cmp-carry")
    assert cached is not None
    assert cached["conversations"] == 1
    assert cached["qualified_replies"] == 1
    assert cached["closes"] == 1


# -------------------------------------------------------------------
# Tracker never raises
# -------------------------------------------------------------------


def test_tracker_never_raises_on_bad_inputs(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)

    on_campaign_deployed("", "", "")
    on_ad_metrics("", 0, 0)
    on_conversation_started("", "", "")
    on_reply("", "")
    on_meeting_scheduled("", "")
    on_show("")
    on_close("")
    on_close(None, value_usd=None)


# -------------------------------------------------------------------
# Multiple leads, same campaign
# -------------------------------------------------------------------


def test_multiple_leads_aggregate_correctly(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)

    pattern = _mem.save_creative_pattern(
        _mem.CreativePattern(
            domain="campaign_execution",
            offer="Growth",
            hook="Hook multi test",
            angle="Angle multi test",
            cta="demo",
            pattern_used="curiosity_gap",
            critic_score=0.84,
        )
    )
    on_campaign_deployed("cmp-multi", pattern.id, "campaign_execution")

    for phone in ("+521000001", "+521000002", "+521000003"):
        on_conversation_started(
            phone, "whatsapp", f"Hola ref:cmp-multi"
        )

    on_reply("+521000001", "whatsapp", is_qualified=True)
    on_reply("+521000002", "whatsapp", is_qualified=True)
    on_meeting_scheduled("+521000001", "2026-03-21")
    on_show("+521000001")
    on_close("+521000001", value_usd=2000.0)

    cached = _log.get_cached_metrics("cmp-multi")
    assert cached["conversations"] == 3
    assert cached["qualified_replies"] == 2
    assert cached["meetings_scheduled"] == 1
    assert cached["shows"] == 1
    assert cached["closes"] == 1
    assert cached["total_revenue_usd"] == 2000.0

    assert abs(cached["response_rate"] - 2 / 3) < 0.001
    assert abs(cached["booking_rate"] - 1 / 3) < 0.001
    assert cached["show_rate"] == 1.0
    assert cached["close_rate"] == 1.0

    final = _mem.get_top_patterns("campaign_execution", limit=1)[0]
    assert final.campaign_id == "cmp-multi"
    assert final.close_rate == 1.0
    assert abs(final.reply_rate - 2 / 3) < 0.001
