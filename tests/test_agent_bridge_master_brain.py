import asyncio

import pytest

from brain.agent_bridge import AgentBridge


class _MasterBrainOk:
    def __init__(self, *args, **kwargs) -> None:
        _ = (args, kwargs)

    async def run(self, goal: str):
        return {
            "status": "completed",
            "summary": f"master resolved: {goal}",
            "data": {"source": "master"},
            "runtime": "master_brain",
            "iterations": 2,
            "steps": [{"stage": "strategy_generation", "status": "ok"}],
            "needs_confirmation": False,
            "confirmation_reason": None,
            "run_id": "run-master-1",
        }


class _MasterBrainFail:
    def __init__(self, *args, **kwargs) -> None:
        _ = (args, kwargs)

    async def run(self, _goal: str):
        raise RuntimeError("master down")


def test_agent_bridge_delegates_to_master_brain(monkeypatch: pytest.MonkeyPatch) -> None:
    # Deprecated flag must not disable MasterBrain delegation.
    monkeypatch.setenv("ENABLE_MASTER_BRAIN", "false")
    monkeypatch.setattr("brain.master_brain.MasterBrain", _MasterBrainOk)

    bridge = AgentBridge(client_id="client-master", max_iterations=4)
    result = asyncio.get_event_loop().run_until_complete(bridge.run("haz marketing"))

    assert result["status"] == "completed"
    assert result["runtime"] == "master_brain"
    assert result["data"]["source"] == "master"


def test_agent_bridge_returns_master_failure_even_with_fallback_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENABLE_MASTER_BRAIN", "true")
    # Deprecated flag must not re-enable ReAct fallback.
    monkeypatch.setenv("MASTER_BRAIN_FALLBACK_REACT", "true")
    monkeypatch.setattr("brain.master_brain.MasterBrain", _MasterBrainFail)

    bridge = AgentBridge(client_id="client-master", max_iterations=2)
    result = asyncio.get_event_loop().run_until_complete(bridge.run("abre chrome"))

    assert result["status"] == "failed"
    assert result["runtime"] == "master_brain"


def test_agent_bridge_returns_master_failure_when_disable_flag_present(monkeypatch: pytest.MonkeyPatch) -> None:
    # Deprecated disable flag is ignored: MasterBrain is still the only runtime path.
    monkeypatch.setenv("ENABLE_MASTER_BRAIN", "false")
    monkeypatch.setenv("MASTER_BRAIN_FALLBACK_REACT", "false")
    monkeypatch.setattr("brain.master_brain.MasterBrain", _MasterBrainFail)

    bridge = AgentBridge(client_id="client-master", max_iterations=2)
    result = asyncio.get_event_loop().run_until_complete(bridge.run("abre chrome"))

    assert result["status"] == "failed"
    assert result["runtime"] == "master_brain"


# ---------------------------------------------------------------------------
# Timeout test (Phase 2-C)
# ---------------------------------------------------------------------------

class _MasterBrainHang:
    """Simulates a MasterBrain that never finishes."""

    def __init__(self, *args, **kwargs) -> None:
        _ = (args, kwargs)

    async def run(self, _goal: str):
        await asyncio.sleep(9999)  # blocks forever in test context
        return {"status": "completed", "summary": "", "data": {}, "runtime": "master_brain",
                "iterations": 0, "steps": [], "needs_confirmation": False,
                "confirmation_reason": None, "run_id": "hang"}


def test_agent_bridge_timeout_returns_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    """AgentBridge must return 'failed' within AGENT_BRIDGE_TIMEOUT_SEC seconds."""
    monkeypatch.setenv("AGENT_BRIDGE_TIMEOUT_SEC", "1")  # 1s for fast test
    monkeypatch.setattr("brain.master_brain.MasterBrain", _MasterBrainHang)

    bridge = AgentBridge(client_id="client-timeout", max_iterations=3)
    result = asyncio.get_event_loop().run_until_complete(bridge.run("analiza todo"))

    assert result["status"] == "failed"
    assert result["runtime"] == "master_brain"
    assert "tardó" in result["summary"].lower() or "timeout" in result["summary"].lower()
    assert result["data"].get("timeout_sec") == 1
