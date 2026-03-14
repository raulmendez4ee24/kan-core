from types import SimpleNamespace

import pytest

import desktop_agent.actions as actions



def test_mover_mouse_calls_pyautogui(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    class FakePyAuto:
        @staticmethod
        def moveTo(x: int, y: int) -> None:
            calls.append((x, y))

    monkeypatch.setattr(actions, "_ensure_windows", lambda: None)
    monkeypatch.setattr(actions, "_get_pyautogui", lambda: FakePyAuto)

    result = actions.mover_mouse(120, 340)

    assert result == {"x": 120, "y": 340}
    assert calls == [(120, 340)]



def test_open_program_blocked_when_not_allowlisted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(actions, "_ensure_windows", lambda: None)

    with pytest.raises(ValueError):
        actions.open_program("cmd", {"notepad": "C:/Windows/notepad.exe"})



def test_open_program_runs_subprocess(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(actions, "_ensure_windows", lambda: None)
    # Force Windows path so the test is platform-independent
    monkeypatch.setattr(actions.platform, "system", lambda: "Windows")

    class FakeProcess:
        pid = 444

    calls = []

    def fake_popen(cmd: list[str]):
        calls.append(cmd)
        return FakeProcess()

    monkeypatch.setattr(actions.subprocess, "Popen", fake_popen)

    result = actions.open_program(
        "notepad",
        {"notepad": "C:/Windows/System32/notepad.exe"},
    )

    assert result["name"] == "notepad"
    assert result["pid"] == 444
    assert calls == [["C:/Windows/System32/notepad.exe"]]


def test_open_program_unsafe_allows_non_allowlisted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(actions, "_ensure_windows", lambda: None)
    # Force Windows path so the test is platform-independent
    monkeypatch.setattr(actions.platform, "system", lambda: "Windows")

    class FakeProcess:
        pid = 777

    calls = []

    def fake_popen(cmd: list[str]):
        calls.append(cmd)
        return FakeProcess()

    monkeypatch.setattr(actions.subprocess, "Popen", fake_popen)

    result = actions.open_program(
        "notepad",
        {"calc": "C:/Windows/System32/calc.exe"},
        unsafe_allow_any=True,
    )

    assert result["pid"] == 777
    # In unsafe mode, it should try direct execution by name first.
    assert calls and calls[0] == ["notepad"]



def test_click_validates_button(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(actions, "_ensure_windows", lambda: None)

    class FakePyAuto:
        @staticmethod
        def click(**kwargs) -> None:
            _ = kwargs

    monkeypatch.setattr(actions, "_get_pyautogui", lambda: FakePyAuto)

    with pytest.raises(ValueError):
        actions.click(button="invalid", clicks=1)


def test_click_with_coordinates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(actions, "_ensure_windows", lambda: None)
    calls = []

    class FakePyAuto:
        @staticmethod
        def click(**kwargs) -> None:
            calls.append(kwargs)

    monkeypatch.setattr(actions, "_get_pyautogui", lambda: FakePyAuto)

    result = actions.click(button="left", clicks=1, x=540, y=320)

    assert result["x"] == 540
    assert result["y"] == 320
    assert calls and calls[0]["x"] == 540 and calls[0]["y"] == 320
