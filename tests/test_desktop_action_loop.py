"""Tests for desktop_action_loop: runner offline and delegation."""
import asyncio

from brain.desktop_action_loop import is_runner_online_for_client, run_desktop_action_loop


def test_is_runner_online_for_client_returns_bool() -> None:
    out = asyncio.run(is_runner_online_for_client("11111111-1111-1111-1111-111111111111"))
    assert isinstance(out, bool)


def test_run_desktop_action_loop_runner_offline_returns_immediately(monkeypatch) -> None:
    async def fake_online(_client_id):
        return False
    monkeypatch.setattr("brain.desktop_action_loop.is_runner_online_for_client", fake_online)
    result = asyncio.run(
        run_desktop_action_loop(
            client_id="11111111-1111-1111-1111-111111111111",
            goal="Abre Chrome",
        )
    )
    assert result["status"] == "runner_offline"
    assert "runner_offline" in result.get("error", "")
    assert "RUNNER_TOKEN" in result.get("summary", "") or "Runner" in result.get("summary", "")


def test_run_desktop_action_loop_no_client_id() -> None:
    result = asyncio.run(run_desktop_action_loop(client_id="", goal="Abre Chrome"))
    assert result["status"] == "failed"
    assert "client_id" in result.get("summary", "").lower() or "requerido" in result.get("summary", "").lower()
