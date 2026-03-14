import asyncio
import logging
from unittest.mock import AsyncMock

import pytest

from integrations import telegram_admin


def test_resolve_mode_off_without_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TELEGRAM_MODE", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("ENABLE_TELEGRAM_POLLING", raising=False)

    state = telegram_admin.resolve_telegram_mode()
    assert state["mode"] == "off"
    assert state["token_present"] is False


def test_resolve_mode_intelligent_webhook(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TELEGRAM_MODE", raising=False)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token-test")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_URL", "https://example.ngrok.io/webhooks/telegram")
    monkeypatch.delenv("ENABLE_TELEGRAM_POLLING", raising=False)

    state = telegram_admin.resolve_telegram_mode()
    assert state["mode"] == "webhook"
    assert state["token_present"] is True


def test_resolve_mode_legacy_polling(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TELEGRAM_MODE", raising=False)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token-test")
    monkeypatch.delenv("TELEGRAM_WEBHOOK_URL", raising=False)
    monkeypatch.setenv("ENABLE_TELEGRAM_POLLING", "true")

    state = telegram_admin.resolve_telegram_mode()
    assert state["mode"] == "polling"
    assert state["legacy_polling"] is True


def test_ensure_mode_polling_deletes_webhook(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_MODE", "polling")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token-test")
    monkeypatch.delenv("TELEGRAM_WEBHOOK_URL", raising=False)

    delete_mock = AsyncMock(return_value={"ok": True})
    info_mock = AsyncMock(return_value={"ok": True, "result": {"url": ""}})
    monkeypatch.setattr(telegram_admin, "delete_webhook", delete_mock)
    monkeypatch.setattr(telegram_admin, "get_webhook_info", info_mock)

    state = asyncio.run(telegram_admin.ensure_telegram_mode(logging.getLogger("test.telegram")))

    assert state["mode"] == "polling"
    assert state["action"] == "delete_webhook"
    assert delete_mock.await_count == 1


def test_ensure_mode_webhook_without_url_fails_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_MODE", "webhook")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token-test")
    monkeypatch.delenv("TELEGRAM_WEBHOOK_URL", raising=False)

    with pytest.raises(RuntimeError, match="TELEGRAM_WEBHOOK_URL"):
        asyncio.run(telegram_admin.ensure_telegram_mode(logging.getLogger("test.telegram")))

