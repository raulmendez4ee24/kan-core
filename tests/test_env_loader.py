from __future__ import annotations

import os

import pytest

import config.env_loader as env_loader


def test_sanitize_env_value_recovers_from_orphan_chars() -> None:
    assert env_loader._sanitize_env_value('"abc"}') == "abc"
    assert env_loader._sanitize_env_value("token]]") == "token"
    assert env_loader._sanitize_env_value("'value' # comment") == "value"


def test_load_env_file_tolerant_ignores_invalid_lines(tmp_path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "\n".join(
            [
                "TELEGRAM_TOKEN=abc123}",
                "INVALID LINE HERE",
                "JARVIS_CLIENT_ID='client-1'",
                "# comment",
                "OPENAI_API_KEY = sk-test # trailing",
            ]
        ),
        encoding="utf-8",
    )
    loaded = env_loader._load_env_file_tolerant(env_path)
    assert loaded["TELEGRAM_TOKEN"] == "abc123"
    assert loaded["JARVIS_CLIENT_ID"] == "client-1"
    assert loaded["OPENAI_API_KEY"] == "sk-test"
    assert "INVALID LINE HERE" not in loaded


def test_normalize_runtime_aliases_and_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "bot-123")
    monkeypatch.delenv("TELEGRAM_TOKEN", raising=False)
    monkeypatch.setenv("KAN_CLIENT_ID", "org-123")
    monkeypatch.delenv("JARVIS_CLIENT_ID", raising=False)

    env_loader._normalize_runtime_aliases()

    assert os.getenv("TELEGRAM_TOKEN") == "bot-123"
    assert os.getenv("JARVIS_CLIENT_ID") == "org-123"


def test_validate_required_env_strict_by_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JARVIS_CLIENT_ID", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_TOKEN", raising=False)

    with pytest.raises(RuntimeError, match=r"\[ENV\] Missing required vars for telegram_polling"):
        env_loader._validate_required_env(
            required_all=("JARVIS_CLIENT_ID",),
            required_any=(("TELEGRAM_BOT_TOKEN", "TELEGRAM_TOKEN"),),
            context="telegram_polling",
        )

    monkeypatch.setenv("JARVIS_CLIENT_ID", "org-abc")
    monkeypatch.setenv("TELEGRAM_TOKEN", "tg-abc")
    env_loader._validate_required_env(
        required_all=("JARVIS_CLIENT_ID",),
        required_any=(("TELEGRAM_BOT_TOKEN", "TELEGRAM_TOKEN"),),
        context="telegram_polling",
    )

