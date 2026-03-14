import asyncio
import time

import pytest

from desktop_agent.controller import DesktopController



def test_controller_dispatch_unknown_action() -> None:
    controller = DesktopController(open_program_allowlist={})

    result = asyncio.run(
        controller.execute(action="unknown", args={}, timeout_ms=1000)
    )

    assert result["status"] == "failed"
    assert "Unsupported action" in str(result["error"])



def test_controller_execute_open_program_success(monkeypatch: pytest.MonkeyPatch) -> None:
    controller = DesktopController(open_program_allowlist={"notepad": "C:/Windows/notepad.exe"})

    monkeypatch.setattr(
        "desktop_agent.controller.open_program",
        lambda name, allowlist, unsafe_allow_any=False: {
            "name": name,
            "pid": 123,
            "allowlist": allowlist,
            "unsafe_allow_any": unsafe_allow_any,
        },
    )

    result = asyncio.run(
        controller.execute(
            action="open_program",
            args={"name": "notepad"},
            timeout_ms=2000,
        )
    )

    assert result["status"] == "success"
    assert result["payload"]["pid"] == 123



def test_controller_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    controller = DesktopController(open_program_allowlist={})

    def slow_run(_action: str, _args: dict):
        time.sleep(0.25)
        return {"ok": True}

    monkeypatch.setattr(controller, "_run_desktop_action", slow_run)

    result = asyncio.run(
        controller.execute(action="take_screenshot", args={}, timeout_ms=50)
    )

    assert result["status"] == "failed"
    assert result["error"] == "execution_timeout"


def test_controller_click_with_coordinates(monkeypatch: pytest.MonkeyPatch) -> None:
    controller = DesktopController(open_program_allowlist={})

    monkeypatch.setattr(
        "desktop_agent.controller.click",
        lambda button, clicks, x=None, y=None: {
            "button": button,
            "clicks": clicks,
            "x": x,
            "y": y,
        },
    )

    result = asyncio.run(
        controller.execute(
            action="click",
            args={"button": "left", "clicks": 1, "x": 540, "y": 320},
            timeout_ms=2000,
        )
    )

    assert result["status"] == "success"
    assert result["payload"]["x"] == 540
    assert result["payload"]["y"] == 320


def test_controller_browser_click_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    controller = DesktopController(open_program_allowlist={})

    async def _fake_browser_click(**kwargs):
        return {"ok": True, "kwargs": kwargs}

    monkeypatch.setattr(controller._browser, "browser_click", _fake_browser_click)

    result = asyncio.run(
        controller.execute(
            action="browser_click",
            args={"selector": "#login"},
            timeout_ms=2000,
        )
    )

    assert result["status"] == "success"
    assert result["payload"]["ok"] is True
