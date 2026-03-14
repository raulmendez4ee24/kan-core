import asyncio

import pytest

from browser_agent.web_agent import WebAgent


class _FakeKeyboard:
    def __init__(self) -> None:
        self.keys = []

    async def press(self, key: str) -> None:
        self.keys.append(key)


class _FakeMouse:
    def __init__(self) -> None:
        self.wheels = []

    async def wheel(self, x: int, y: int) -> None:
        self.wheels.append((x, y))


class _FakePage:
    def __init__(self) -> None:
        self.url = "https://demo.test/home"
        self.keyboard = _FakeKeyboard()
        self.mouse = _FakeMouse()
        self.back_calls = 0

    async def title(self) -> str:
        return "Demo"

    async def evaluate(self, script: str):
        if "querySelectorAll('a,button,input" in script:
            return [
                {
                    "tag": "input",
                    "id": "search",
                    "name": "q",
                    "type": "text",
                    "role": "",
                    "text": "",
                    "selector": '#search',
                },
                {
                    "tag": "button",
                    "id": "",
                    "name": "",
                    "type": "",
                    "role": "button",
                    "text": "Buscar",
                    "selector": 'button:has-text("Buscar")',
                },
            ]
        if "document.body" in script:
            return "Pagina demo con formulario y boton buscar"
        return None

    async def go_back(self, wait_until: str = "domcontentloaded") -> None:
        _ = wait_until
        self.back_calls += 1


class _FakeBrowser:
    def __init__(self) -> None:
        self._page = _FakePage()
        self.launches = 0
        self.gotos = []
        self.clicks = []
        self.fills = []

    async def _ensure_session(self) -> None:
        return None

    async def browser_launch(self, *, start_url: str | None = None, **_kwargs):
        self.launches += 1
        if start_url:
            self._page.url = start_url
        return {"url": self._page.url, "title": "Demo"}

    async def browser_goto(self, *, url: str, wait_until: str = "domcontentloaded"):
        _ = wait_until
        self.gotos.append(url)
        self._page.url = url
        return {"url": url, "title": "Demo"}

    async def browser_click(self, *, selector: str | None = None, text: str | None = None, **_kwargs):
        if selector == "#missing":
            raise RuntimeError("element not found")
        self.clicks.append((selector, text))
        return {"selector": selector, "text": text}

    async def browser_fill(self, *, selector: str, text: str, **_kwargs):
        self.fills.append((selector, text))
        return {"selector": selector, "text": text}

    async def browser_press(self, *, selector: str, key: str, **_kwargs):
        return {"selector": selector, "key": key}

    async def browser_extract(self, *, selector: str, attribute: str | None = None, all_matches: bool = False, **_kwargs):
        _ = (attribute, all_matches)
        return {"selector": selector, "value": "dato extraido"}

    async def browser_screenshot(self, *, full_page: bool = False):
        _ = full_page
        return {"mime_type": "image/png", "image_base64": "ZmFrZQ=="}


def test_web_agent_observe_collects_state() -> None:
    browser = _FakeBrowser()
    agent = WebAgent(browser=browser, max_steps=3)
    state = asyncio.get_event_loop().run_until_complete(agent.observe())

    assert state["url"] == "https://demo.test/home"
    assert state["title"] == "Demo"
    assert isinstance(state["interactive_elements"], list)
    assert len(state["interactive_elements"]) >= 1


def test_web_agent_run_completes_with_extract(monkeypatch: pytest.MonkeyPatch) -> None:
    browser = _FakeBrowser()
    agent = WebAgent(browser=browser, max_steps=5)

    decisions = iter(
        [
            {"action": "goto", "url": "https://demo.test/search", "reasoning": "open page"},
            {"action": "extract", "selector": "h1", "reasoning": "collect result"},
        ]
    )

    async def _fake_decide(_goal: str, _state: dict) -> dict:
        return next(decisions)

    async def _fake_verify(_goal: str, *, state=None, last_result=None) -> dict:
        _ = state
        if str((last_result or {}).get("action") or "") == "extract":
            return {"done": True, "summary": "listo", "data": dict(agent.extracted_data)}
        return {"done": False, "summary": "sigue", "data": dict(agent.extracted_data)}

    monkeypatch.setattr(agent, "decide", _fake_decide)
    monkeypatch.setattr(agent, "verify", _fake_verify)

    result = asyncio.get_event_loop().run_until_complete(agent.run("Busca datos en https://demo.test"))

    assert result["success"] is True
    assert result["data"].get("h1") == "dato extraido"
    assert "listo" in result["summary"]


def test_web_agent_loop_guard_stops_repeated_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    browser = _FakeBrowser()
    agent = WebAgent(browser=browser, max_steps=8)

    async def _fake_decide(_goal: str, _state: dict) -> dict:
        return {"action": "click", "selector": "#missing", "reasoning": "retry"}

    async def _fake_verify(_goal: str, *, state=None, last_result=None) -> dict:
        _ = (state, last_result)
        return {"done": False, "summary": "not yet", "data": {}}

    monkeypatch.setattr(agent, "decide", _fake_decide)
    monkeypatch.setattr(agent, "verify", _fake_verify)

    result = asyncio.get_event_loop().run_until_complete(agent.run("Haz click en boton roto"))

    assert result["success"] is False
    assert "loop guard" in result["summary"].lower()
