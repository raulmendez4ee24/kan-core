import asyncio
from unittest.mock import AsyncMock

import pytest

from browser_agent.browser_controller import BrowserController


class _FakeMouse:
    def __init__(self) -> None:
        self.clicks: list = []

    async def click(self, x: int, y: int) -> None:
        self.clicks.append((x, y))


class _FakePage:
    def __init__(self) -> None:
        self.url = "https://app.example.com/home"
        self.mouse = _FakeMouse()
        self.last_goto = None
        self.last_load_state = None

    def set_default_timeout(self, _timeout: int) -> None:
        return None

    async def goto(self, url: str, wait_until: str = "domcontentloaded") -> None:
        self.last_goto = (url, wait_until)
        self.url = url

    async def title(self) -> str:
        return "Fake Page"

    async def wait_for_load_state(self, state: str) -> None:
        self.last_load_state = state

    async def screenshot(self, full_page: bool, type: str) -> bytes:
        _ = (full_page, type)
        return (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
            b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\x0cIDATx\x9cc\xf8\xff"
            b"\xff?\x00\x05\xfe\x02\xfeA\xa6\xdf\xc1\x00\x00\x00\x00IEND\xaeB`\x82"
        )


class _FakeContext:
    def __init__(self) -> None:
        self.pages = [_FakePage()]

    async def new_page(self) -> _FakePage:
        return _FakePage()

    async def close(self) -> None:
        return None


class _FakeChromium:
    def __init__(self, recorder: dict) -> None:
        self._recorder = recorder

    async def launch_persistent_context(self, **kwargs):
        self._recorder["launch_kwargs"] = kwargs
        return _FakeContext()


class _FakePlaywright:
    def __init__(self, recorder: dict) -> None:
        self.chromium = _FakeChromium(recorder)

    async def stop(self) -> None:
        return None


class _FakePlaywrightStarter:
    def __init__(self, recorder: dict) -> None:
        self._recorder = recorder

    async def start(self):
        return _FakePlaywright(self._recorder)


def _controller() -> BrowserController:
    return BrowserController(
        enabled=True,
        headless=False,
        channel="chromium",
        user_data_dir="",
        domain_allowlist=["example.com"],
        allow_all_domains=False,
        default_timeout_ms=15000,
    )


def test_browser_controller_blocks_non_allowlisted_domain() -> None:
    controller = _controller()
    assert controller._is_domain_allowed("https://evil.com") is False
    with pytest.raises(ValueError):
        controller._require_allowed_url("https://evil.com/login")


def test_browser_controller_allows_any_domain_when_allow_all_enabled() -> None:
    controller = BrowserController(
        enabled=True,
        headless=False,
        channel="chromium",
        user_data_dir="",
        domain_allowlist=[],
        allow_all_domains=True,
        default_timeout_ms=15000,
    )
    assert controller._is_domain_allowed("https://evil.com") is True
    controller._require_allowed_url("https://evil.com/login")


def test_browser_goto_uses_page_when_url_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    controller = _controller()
    fake_page = _FakePage()

    async def fake_ensure_session() -> None:
        controller._page = fake_page
        controller._context = object()

    monkeypatch.setattr(controller, "_ensure_session", fake_ensure_session)

    result = asyncio.get_event_loop().run_until_complete(
        controller.browser_goto(url="https://app.example.com/login")
    )

    assert result["url"] == "https://app.example.com/login"
    assert fake_page.last_goto == ("https://app.example.com/login", "domcontentloaded")


def test_browser_click_coordinates(monkeypatch: pytest.MonkeyPatch) -> None:
    controller = _controller()
    fake_page = _FakePage()

    async def fake_ensure_page() -> None:
        controller._page = fake_page
        controller._context = object()

    monkeypatch.setattr(controller, "_ensure_interaction_page", fake_ensure_page)

    result = asyncio.get_event_loop().run_until_complete(
        controller.browser_click(x=120, y=240)
    )

    assert result["mode"] == "coordinates"
    assert fake_page.mouse.clicks == [(120, 240)]


def test_browser_launch_forces_playwright_chromium_with_sandbox_flags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = BrowserController(
        enabled=True,
        headless=True,
        channel="chrome",
        user_data_dir="",
        domain_allowlist=["example.com"],
        allow_all_domains=False,
        default_timeout_ms=15000,
    )
    recorder: dict = {}
    monkeypatch.setattr(
        "browser_agent.browser_controller.async_playwright",
        lambda: _FakePlaywrightStarter(recorder),
    )

    result = asyncio.get_event_loop().run_until_complete(controller.browser_launch())

    launch_kwargs = recorder["launch_kwargs"]
    assert launch_kwargs["channel"] == "chromium"
    assert "--no-sandbox" in launch_kwargs["args"]
    assert "--disable-setuid-sandbox" in launch_kwargs["args"]
    assert result["channel"] == "chromium"


def test_browser_screenshot_waits_for_networkidle_and_buffer(monkeypatch: pytest.MonkeyPatch) -> None:
    controller = _controller()
    fake_page = _FakePage()

    async def fake_ensure_page() -> None:
        controller._page = fake_page
        controller._context = object()

    sleep_mock = AsyncMock()
    monkeypatch.setattr(controller, "_ensure_interaction_page", fake_ensure_page)
    monkeypatch.setattr("browser_agent.browser_controller.asyncio.sleep", sleep_mock)

    result = asyncio.get_event_loop().run_until_complete(
        controller.browser_screenshot(full_page=False)
    )

    assert fake_page.last_load_state == "networkidle"
    sleep_mock.assert_awaited_once_with(0.5)
    assert result["mime_type"] == "image/png"
