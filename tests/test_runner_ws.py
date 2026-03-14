"""Tests for runner WebSocket protocol: execute message and result parsing."""
import pytest

from schemas.desktop_runner_protocol import (
    build_execute_message,
    parse_result_message,
    EXECUTE_ACTIONS,
    LEGACY_ACTION_MAP,
)


def test_build_execute_message_minimal() -> None:
    msg = build_execute_message(run_id="r1", step_id="s1", action="open_app", args={"app_name": "Chrome"})
    assert msg["type"] == "execute"
    assert msg["run_id"] == "r1"
    assert msg["step_id"] == "s1"
    assert msg["action"] == "open_app"
    assert msg["args"] == {"app_name": "Chrome"}


def test_build_execute_message_with_timeout() -> None:
    msg = build_execute_message(
        run_id="r2", step_id="s2", action="screenshot", args={}, timeout_s=10
    )
    assert msg["timeout_s"] == 10


def test_parse_result_message_success() -> None:
    raw = {
        "type": "result",
        "run_id": "r1",
        "step_id": "s1",
        "command_id": "s1",
        "status": "completed",
        "summary": "Done",
        "data": {"current_url": "https://example.com"},
        "duration_ms": 500,
    }
    out = parse_result_message(raw)
    assert out["type"] == "result"
    assert out["status"] == "completed"
    assert out["summary"] == "Done"
    assert out["current_url"] == "https://example.com"
    assert out["duration_ms"] == 500


def test_parse_result_message_evidence_from_data() -> None:
    raw = {
        "type": "result",
        "run_id": "r1",
        "step_id": "s1",
        "status": "completed",
        "data": {"screenshot_base64": "base64data", "evidence": {"screenshot_b64": "b64"}},
    }
    out = parse_result_message(raw)
    assert "screenshot_b64" in out["evidence"] or "base64data" in str(out["evidence"])


def test_execute_actions_list_non_empty() -> None:
    assert len(EXECUTE_ACTIONS) >= 10
    assert "open_app" in EXECUTE_ACTIONS
    assert "screenshot" in EXECUTE_ACTIONS
    assert "click" in EXECUTE_ACTIONS


def test_legacy_action_map_covers_required() -> None:
    assert LEGACY_ACTION_MAP.get("open_app") == "open_program"
    assert LEGACY_ACTION_MAP.get("goto") == "browser_goto"
    assert LEGACY_ACTION_MAP.get("screenshot") == "take_screenshot"
