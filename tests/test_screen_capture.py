import desktop_agent.screen_capture as sc


class _FakeImage:
    def __init__(self, width: int = 1200, height: int = 800) -> None:
        self.width = width
        self.height = height

    def resize(self, size):
        return _FakeImage(width=int(size[0]), height=int(size[1]))

    def save(self, fp, format="PNG") -> None:
        _ = format
        fp.write(b"\x89PNG\r\n")


class _FakePyAutoGUI:
    @staticmethod
    def screenshot():
        return _FakeImage()


def test_take_screenshot_uses_locking_and_releases(monkeypatch) -> None:
    calls = {"acquire": 0, "release": 0}
    lock_obj = object()

    monkeypatch.setattr(sc, "_ensure_windows", lambda: None)
    monkeypatch.setattr(sc, "_get_pyautogui", lambda: _FakePyAutoGUI())

    def fake_acquire_process_lock(timeout_s: float):
        _ = timeout_s
        calls["acquire"] += 1
        return lock_obj

    def fake_release_process_lock(handle) -> None:
        assert handle is lock_obj
        calls["release"] += 1

    monkeypatch.setattr(sc, "_acquire_process_lock", fake_acquire_process_lock)
    monkeypatch.setattr(sc, "_release_process_lock", fake_release_process_lock)

    out = sc.take_screenshot(max_width=1600)
    assert out["mime_type"] == "image/png"
    assert out["width"] == 1200
    assert out["height"] == 800
    assert calls["acquire"] == 1
    assert calls["release"] == 1


def test_take_screenshot_triggers_gc_collect(monkeypatch) -> None:
    monkeypatch.setattr(sc, "_ensure_windows", lambda: None)
    monkeypatch.setattr(sc, "_get_pyautogui", lambda: _FakePyAutoGUI())
    monkeypatch.setattr(sc, "_acquire_process_lock", lambda timeout_s: object())
    monkeypatch.setattr(sc, "_release_process_lock", lambda _handle: None)

    calls = {"gc": 0}

    def fake_collect() -> int:
        calls["gc"] += 1
        return 0

    monkeypatch.setattr(sc.gc, "collect", fake_collect)
    out = sc.take_screenshot(max_width=1600)
    assert out["mime_type"] == "image/png"
    assert calls["gc"] == 1
