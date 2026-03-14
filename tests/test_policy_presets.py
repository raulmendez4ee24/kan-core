import json
from uuid import uuid4

from brain.policy_engine import EffectivePolicy, validate_desktop_action
from brain.policy_presets import apply_openclaw_allow_all_preset, apply_openclaw_preset


def test_apply_openclaw_preset_sets_full_auto_and_guardrails(monkeypatch) -> None:
    monkeypatch.setenv("DESKTOP_AGENT_BROWSER_DOMAIN_ALLOWLIST_JSON", json.dumps(["example.com"]))
    monkeypatch.setenv(
        "DESKTOP_AGENT_ALLOWLIST_JSON",
        json.dumps({"notepad": "C:\\\\Windows\\\\System32\\\\notepad.exe"}),
    )

    out = apply_openclaw_preset({})
    assert out["autonomy"]["execution_mode"] == "full_auto"
    assert out["autonomy"]["max_auto_risk"] == "high"
    assert out["desktop"]["auto_approve_enabled"] is True
    assert out["browser"]["auto_approve_enabled"] is True
    assert out["workflow"]["allow_auto_recovery"] is True
    assert out["guardrails"]["circuit_breaker"]["enabled"] is True
    assert out["browser"]["domain_allowlist"] == ["example.com"]
    assert "notepad" in out["desktop"]["allow_open_programs"]


def test_apply_openclaw_preset_does_not_wipe_existing_allowlists() -> None:
    existing = {
        "browser": {"domain_allowlist": ["a.com"]},
        "desktop": {"allow_open_programs": ["calc"]},
        "guardrails": {"circuit_breaker": {"enabled": False, "custom": 1}},
    }
    out = apply_openclaw_preset(existing)
    assert out["browser"]["domain_allowlist"] == ["a.com"]
    assert out["desktop"]["allow_open_programs"] == ["calc"]
    # The preset enforces enabled=True but preserves unknown keys.
    assert out["guardrails"]["circuit_breaker"]["enabled"] is True
    assert out["guardrails"]["circuit_breaker"]["custom"] == 1


def test_policy_open_program_allowlist_is_case_insensitive() -> None:
    policy = EffectivePolicy(
        organization_id=uuid4(),
        policy={"desktop": {"allow_open_programs": ["notepad"]}},
        updated_at=None,
        enforced=True,
    )
    assert validate_desktop_action(action="open_program", args={"name": "Notepad"}, policy=policy) is None


def test_apply_openclaw_allow_all_preset_disables_allowlist_requirements() -> None:
    out = apply_openclaw_allow_all_preset(
        {
            "browser": {"domain_allowlist": ["example.com"]},
            "desktop": {"allow_open_programs": ["notepad"]},
        }
    )
    assert out["guardrails"]["require_domain_allowlist_for_browser"] is False
    assert out["guardrails"]["require_open_program_allowlist"] is False
    assert out["browser"]["domain_allowlist"] == []
    assert out["desktop"]["allow_open_programs"] == []
