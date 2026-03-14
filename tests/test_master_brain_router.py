import asyncio
import uuid

import pytest

from brain.master_brain import MasterBrain


async def _snapshot_browser(*_args, **_kwargs):
    return {
        "selected_tool": "browser",
        "tools": [
            {"tool": "browser", "available": True, "success_prob": 0.9, "sample_size": 20},
            {"tool": "api", "available": True, "success_prob": 0.6, "sample_size": 10},
            {"tool": "desktop", "available": True, "success_prob": 0.5, "sample_size": 8},
        ],
    }


def test_master_brain_choose_tools_routes_dynamically() -> None:
    brain = MasterBrain(client_id=str(uuid.uuid4()))
    brain._tool_snapshot_builder = _snapshot_browser

    plan = [
        {
            "id": "step-1",
            "title": "Paso analítico",
            "action_type": "analysis",
            "action": "analyze",
            "args": {"goal": "Analiza embudo"},
            "risk": "low",
        },
        {
            "id": "step-2",
            "title": "Paso web",
            "action_type": "web",
            "action": "browser_goto",
            "args": {"url": "https://example.com"},
            "risk": "low",
        },
        {
            "id": "step-3",
            "title": "Paso desktop",
            "action_type": "desktop",
            "action": "desktop_click",
            "args": {},
            "risk": "medium",
        },
    ]

    routing = asyncio.run(brain._choose_tools(plan, {"intent": "business"}))

    assert routing["selected_tool"] == "browser"
    assert routing["routes"]["step-1"] == "web_agent"
    assert routing["routes"]["step-2"] == "web_agent"
    assert routing["routes"]["step-3"] == "autonomous_agent"
