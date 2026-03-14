from __future__ import annotations

import pytest

import brain.jarvis_ui_state as ui_state


def test_publish_and_read_state_roundtrip(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    state_path = tmp_path / "jarvis_ui_state.json"
    monkeypatch.setenv("JARVIS_UI_STATE_FILE", str(state_path))

    ui_state.publish_state("speaking", source="tts", text="hola", level=0.82)
    payload = ui_state.read_state()

    assert payload["mode"] == "speaking"
    assert payload["source"] == "tts"
    assert payload["text"] == "hola"
    assert abs(float(payload["level"]) - 0.82) < 1e-6
    assert float(payload["updated_at"]) > 0.0


def test_publish_state_clamps_level(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    state_path = tmp_path / "state.json"
    monkeypatch.setenv("JARVIS_UI_STATE_FILE", str(state_path))

    ui_state.publish_state("speaking", source="tts", text="", level=9.0)
    assert ui_state.read_state()["level"] == 1.0

    ui_state.publish_state("speaking", source="tts", text="", level=-1.0)
    assert ui_state.read_state()["level"] == 0.0


def test_read_state_handles_corrupt_json(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    state_path = tmp_path / "broken_state.json"
    monkeypatch.setenv("JARVIS_UI_STATE_FILE", str(state_path))
    state_path.write_text("{broken", encoding="utf-8")

    payload = ui_state.read_state()
    assert payload["mode"] == "standby"
    assert payload["source"] == "corrupt"
