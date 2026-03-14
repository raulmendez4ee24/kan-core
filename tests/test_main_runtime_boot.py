from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

import main
from brain.runtime_events import runtime_event_bus


class _DummyListener:
    started = False
    stopped = False

    async def start(self) -> None:
        _DummyListener.started = True
        while True:
            await asyncio.sleep(0.05)

    async def stop(self) -> None:
        _DummyListener.stopped = True


class _DummyProcess:
    def __init__(self) -> None:
        self.pid = 99999
        self.returncode = None

    def terminate(self) -> None:
        self.returncode = 0

    def kill(self) -> None:
        self.returncode = -9

    async def wait(self) -> int:
        if self.returncode is None:
            self.returncode = 0
        return int(self.returncode)


def _reset_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "ENABLE_JARVIS_LISTENER",
        "ENABLE_TELEGRAM_POLLING",
        "ENABLE_JARVIS_GUI",
        "JARVIS_CLIENT_ID",
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_TOKEN",
    ):
        monkeypatch.delenv(key, raising=False)


def test_lifespan_starts_enabled_runtime_components(monkeypatch: pytest.MonkeyPatch) -> None:
    _reset_env(monkeypatch)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setattr(main.sys, "argv", ["uvicorn"])
    monkeypatch.setenv("ENABLE_JARVIS_LISTENER", "true")
    monkeypatch.setenv("ENABLE_TELEGRAM_POLLING", "true")
    monkeypatch.setenv("ENABLE_JARVIS_GUI", "true")
    monkeypatch.setenv("JARVIS_CLIENT_ID", "org-test")
    monkeypatch.setenv("TELEGRAM_TOKEN", "token-test")

    _DummyListener.started = False
    _DummyListener.stopped = False
    marks = {"polling_started": False, "gui_started": False}

    async def _polling_idle() -> None:
        while True:
            await asyncio.sleep(0.05)

    def _fake_start_polling_task() -> asyncio.Task:
        marks["polling_started"] = True
        return asyncio.create_task(_polling_idle())

    async def _fake_create_subprocess_exec(*_args, **_kwargs):
        marks["gui_started"] = True
        return _DummyProcess()

    monkeypatch.setattr("jarvis_listener.JarvisListener", _DummyListener)
    monkeypatch.setattr("workers.telegram_polling.start_polling_task", _fake_start_polling_task)
    monkeypatch.setattr(main.asyncio, "create_subprocess_exec", _fake_create_subprocess_exec)
    monkeypatch.setattr(
        main,
        "ensure_telegram_mode",
        AsyncMock(return_value={"mode": "polling", "configured_mode": "polling", "ok": True}),
    )
    monkeypatch.setattr(main, "close_db", AsyncMock())
    monkeypatch.setattr(main, "init_db", AsyncMock())
    shutdown_mock = AsyncMock()
    monkeypatch.setattr(runtime_event_bus, "shutdown", shutdown_mock)

    with TestClient(main.app) as client:
        response = client.get("/healthz")
        assert response.status_code == 200

    assert _DummyListener.started is True
    assert _DummyListener.stopped is True
    assert marks["polling_started"] is True
    assert marks["gui_started"] is True
    shutdown_mock.assert_awaited_once()


def test_lifespan_does_not_start_disabled_components(monkeypatch: pytest.MonkeyPatch) -> None:
    _reset_env(monkeypatch)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setattr(main.sys, "argv", ["uvicorn"])
    monkeypatch.setenv("ENABLE_JARVIS_LISTENER", "false")
    monkeypatch.setenv("ENABLE_TELEGRAM_POLLING", "false")
    monkeypatch.setenv("ENABLE_JARVIS_GUI", "false")

    marks = {"gui_started": False}

    async def _fake_create_subprocess_exec(*_args, **_kwargs):
        marks["gui_started"] = True
        return _DummyProcess()

    monkeypatch.setattr(main.asyncio, "create_subprocess_exec", _fake_create_subprocess_exec)
    monkeypatch.setattr(
        main,
        "ensure_telegram_mode",
        AsyncMock(return_value={"mode": "off", "configured_mode": "off", "ok": True}),
    )
    monkeypatch.setattr(main, "close_db", AsyncMock())
    shutdown_mock = AsyncMock()
    monkeypatch.setattr(runtime_event_bus, "shutdown", shutdown_mock)

    with TestClient(main.app) as client:
        response = client.get("/healthz")
        assert response.status_code == 200

    assert marks["gui_started"] is False
    shutdown_mock.assert_awaited_once()
