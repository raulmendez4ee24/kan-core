import asyncio

import pytest

from brain.agent_bridge import AgentBridge


class _FakeWebAgent:
    def __init__(self, *args, **kwargs) -> None:
        _ = (args, kwargs)

    async def run(self, goal: str, start_url: str | None = None):
        _ = start_url
        return {
            "success": True,
            "summary": f"web ok: {goal[:40]}",
            "data": {"result": "ok"},
            "actions": [{"action": "extract", "selector": "h1"}],
            "pages_visited": ["https://example.com"],
            "results": [{"ok": True, "action": "extract"}],
        }


def test_agent_bridge_routes_web_goal_to_web_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("browser_agent.web_agent_loop.WebAgentLoop", _FakeWebAgent)

    bridge = AgentBridge(client_id="client-test", max_iterations=4)
    result = asyncio.get_event_loop().run_until_complete(
        bridge.run("Abre https://example.com y extrae el titulo")
    )

    assert result["runtime"] == "web_agent_loop"
    assert result["status"] == "completed"
    assert result["data"]["result"] == "ok"
