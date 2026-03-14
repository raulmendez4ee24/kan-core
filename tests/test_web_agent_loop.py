import asyncio

from browser_agent.web_agent_loop import WebAgentLoop


class _FakePage:
    def __init__(self) -> None:
        self.url = "https://demo.test/home"
        self.keyboard = type("K", (), {"press": self._press})()
        self.mouse = type("M", (), {"wheel": self._wheel})()
        self.keys = []
        self.wheels = []

    async def _press(self, key: str) -> None:
        self.keys.append(key)

    async def _wheel(self, x: int, y: int) -> None:
        self.wheels.append((x, y))

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
                    "selector": "#search",
                }
            ]
        if "document.body" in script:
            return "demo body"
        return None

    async def go_back(self, wait_until: str = "domcontentloaded") -> None:
        _ = wait_until


class _FakeBrowser:
    def __init__(self) -> None:
        self._page = _FakePage()

    async def _ensure_session(self) -> None:
        return None

    async def browser_launch(self, *, start_url: str | None = None, **_kwargs):
        if start_url:
            self._page.url = start_url
        return {"url": self._page.url}

    async def browser_goto(self, *, url: str, **_kwargs):
        self._page.url = url
        return {"url": url}

    async def browser_click(self, *, selector: str | None = None, text: str | None = None, **_kwargs):
        return {"selector": selector, "text": text}

    async def browser_fill(self, *, selector: str, text: str, **_kwargs):
        return {"selector": selector, "text": text}

    async def browser_press(self, *, selector: str, key: str, **_kwargs):
        return {"selector": selector, "key": key}

    async def browser_extract(self, *, selector: str, **_kwargs):
        return {"selector": selector, "value": "ok"}

    async def browser_screenshot(self, *, full_page: bool = False):
        _ = full_page
        return {"mime_type": "image/png", "image_base64": "ZmFrZQ=="}


def test_web_agent_loop_runs_beyond_goto(monkeypatch) -> None:
    agent = WebAgentLoop(browser=_FakeBrowser(), max_steps=5)
    observations = {"count": 0}

    original_observe = agent.observe

    async def _observe_wrapper():
        observations["count"] += 1
        return await original_observe()

    decisions = iter(
        [
            {"action": "goto", "url": "https://demo.test/search", "reasoning": "open search"},
            {"action": "extract", "selector": "h1", "reasoning": "read result"},
        ]
    )

    async def _fake_decide(_goal: str, _state: dict) -> dict:
        return next(decisions)

    async def _fake_verify(_goal: str, *, state=None, last_result=None) -> dict:
        _ = state
        if str((last_result or {}).get("action") or "") == "extract":
            return {"done": True, "summary": "completed", "data": dict(agent.extracted_data)}
        return {"done": False, "summary": "continue", "data": dict(agent.extracted_data)}

    monkeypatch.setattr(agent, "observe", _observe_wrapper)
    monkeypatch.setattr(agent, "decide", _fake_decide)
    monkeypatch.setattr(agent, "verify", _fake_verify)

    result = asyncio.get_event_loop().run_until_complete(
        agent.run("Busca en la web", start_url="https://demo.test")
    )

    assert result["success"] is True
    assert observations["count"] >= 2
    assert len(result["actions"]) >= 2
    assert any(str(item.get("action") or "") == "extract" for item in result["actions"])

