from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from brain import creative_event_log as _event_log
from brain import creative_attribution as _attr


def _setup(monkeypatch, tmp_path):
    db = tmp_path / "attr_test.sqlite3"
    monkeypatch.setenv("CREATIVE_MEMORY_DB_PATH", str(db))
    _event_log._reset_connection()


# -------------------------------------------------------------------
# extract_campaign_from_message
# -------------------------------------------------------------------


def test_extract_ref_basic():
    assert _attr.extract_campaign_from_message(
        "Hola, vengo del anuncio ref:cmp-abc123"
    ) == "cmp-abc123"


def test_extract_ref_with_dots_and_underscores():
    assert _attr.extract_campaign_from_message(
        "ref:cmp-2026.03_spring"
    ) == "cmp-2026.03_spring"


def test_extract_ref_case_insensitive():
    assert _attr.extract_campaign_from_message("REF:CMP-UPPER") == "CMP-UPPER"


def test_extract_ref_embedded_in_long_message():
    text = (
        "Buenos dias, me interesa saber mas sobre el servicio que "
        "ofrecen. Vi su anuncio ref:cmp-meta-42 y quiero agendar "
        "una llamada."
    )
    assert _attr.extract_campaign_from_message(text) == "cmp-meta-42"


def test_extract_ref_no_match():
    assert _attr.extract_campaign_from_message("Hola, quiero informes") is None


def test_extract_ref_empty():
    assert _attr.extract_campaign_from_message("") is None
    assert _attr.extract_campaign_from_message(None) is None


# -------------------------------------------------------------------
# register_attribution + resolve_campaign (round-trip)
# -------------------------------------------------------------------


def test_register_and_resolve(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)

    ok = _attr.register_attribution(
        lead_id="+521234567890",
        campaign_id="cmp-001",
        source="utm_param",
        confidence=0.9,
    )
    assert ok is True

    result = _attr.resolve_campaign("+521234567890")
    assert result is not None
    cid, conf = result
    assert cid == "cmp-001"
    assert conf == 0.9


def test_register_overwrites_previous(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)

    _attr.register_attribution("+5200001", "cmp-old", "landing", 0.7)
    _attr.register_attribution("+5200001", "cmp-new", "ref_tag", 1.0)

    result = _attr.resolve_campaign("+5200001")
    assert result is not None
    assert result[0] == "cmp-new"
    assert result[1] == 1.0


def test_register_rejects_empty_ids(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    assert _attr.register_attribution("", "cmp-x", "test") is False
    assert _attr.register_attribution("lead-1", "", "test") is False


# -------------------------------------------------------------------
# Attribution expiry
# -------------------------------------------------------------------


def test_expired_attribution_returns_none(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)

    _attr.register_attribution(
        lead_id="lead-exp",
        campaign_id="cmp-expired",
        source="test",
        confidence=0.8,
        expires_days=1,
    )

    future = datetime.now(timezone.utc) + timedelta(days=2)
    with patch.object(_attr, "_now_utc", return_value=future), \
         patch.object(_attr, "_now_iso", return_value=future.isoformat()):
        result = _attr.resolve_campaign("lead-exp")

    assert result is None


def test_non_expired_attribution_resolves(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)

    _attr.register_attribution(
        lead_id="lead-fresh",
        campaign_id="cmp-fresh",
        source="test",
        confidence=0.85,
        expires_days=30,
    )

    result = _attr.resolve_campaign("lead-fresh")
    assert result is not None
    assert result[0] == "cmp-fresh"


# -------------------------------------------------------------------
# Hint priority
# -------------------------------------------------------------------


def test_hints_campaign_id_wins_over_db(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)

    _attr.register_attribution("lead-h", "cmp-db", "landing", 0.8)

    result = _attr.resolve_campaign(
        "lead-h", hints={"campaign_id": "cmp-override"}
    )
    assert result == ("cmp-override", 1.0)


def test_hints_campaign_ref(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    result = _attr.resolve_campaign(
        "lead-x", hints={"campaign_ref": "cmp-ref-42"}
    )
    assert result == ("cmp-ref-42", 1.0)


def test_hints_raw_text_extraction(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    result = _attr.resolve_campaign(
        "lead-x",
        hints={"raw_text": "Hola vengo por ref:cmp-wa-77"},
    )
    assert result == ("cmp-wa-77", 1.0)


def test_hints_utm_campaign(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    result = _attr.resolve_campaign(
        "lead-x", hints={"utm_campaign": "spring_2026"}
    )
    assert result == ("spring_2026", 0.9)


def test_hints_source_campaign(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    result = _attr.resolve_campaign(
        "lead-x", hints={"source_campaign": "src-88"}
    )
    assert result == ("src-88", 0.7)


def test_hints_ad_name(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    result = _attr.resolve_campaign(
        "lead-x", hints={"ad_name": "ad_dolor_whatsapp_v3"}
    )
    assert result == ("ad_dolor_whatsapp_v3", 0.5)


def test_hints_priority_order(monkeypatch, tmp_path):
    """campaign_id beats utm_campaign beats ad_name."""
    _setup(monkeypatch, tmp_path)

    hints = {
        "ad_name": "ad-low",
        "utm_campaign": "utm-mid",
        "campaign_id": "cmp-high",
    }
    result = _attr.resolve_campaign("lead-x", hints=hints)
    assert result == ("cmp-high", 1.0)

    del hints["campaign_id"]
    result = _attr.resolve_campaign("lead-x", hints=hints)
    assert result == ("utm-mid", 0.9)

    del hints["utm_campaign"]
    result = _attr.resolve_campaign("lead-x", hints=hints)
    assert result == ("ad-low", 0.5)


# -------------------------------------------------------------------
# Edge cases
# -------------------------------------------------------------------


def test_resolve_unknown_lead_returns_none(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    assert _attr.resolve_campaign("nonexistent-lead") is None


def test_resolve_empty_lead_id():
    assert _attr.resolve_campaign("") is None
    assert _attr.resolve_campaign(None) is None


def test_confidence_is_clamped(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    _attr.register_attribution("lead-clamp", "cmp-x", "test", confidence=5.0)
    result = _attr.resolve_campaign("lead-clamp")
    assert result is not None
    assert result[1] == 1.0

    _attr.register_attribution("lead-clamp2", "cmp-y", "test", confidence=-3.0)
    result2 = _attr.resolve_campaign("lead-clamp2")
    assert result2 is not None
    assert result2[1] == 0.0
