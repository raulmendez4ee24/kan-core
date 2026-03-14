import asyncio
import json
from typing import Any, Dict

from runner.local_runner import _handle_execute_message


class _FakeWebSocket:
    def __init__(self) -> None:
        self.sent: list[Dict[str, Any]] = []

    async def send(self, payload: str) -> None:
        self.sent.append(json.loads(payload))


class _FakeController:
    async def execute(self, *, action: str, args: Dict[str, Any], timeout_ms: int) -> Dict[str, Any]:
        return {
            "status": "success",
            "payload": {
                "action": action,
                "args": args,
                "timeout_ms": timeout_ms,
            },
            "error": None,
        }


def test_local_runner_execute_protocol_returns_result() -> None:
    async def run_case() -> Dict[str, Any]:
        ws = _FakeWebSocket()
        controller = _FakeController()
        await _handle_execute_message(
            ws,
            controller=controller,  # type: ignore[arg-type]
            message={
                "type": "execute",
                "run_id": "run-123",
                "step_id": "step-abc",
                "tool": "browser",
                "action": "goto",
                "args": {"url": "https://example.com"},
            },
        )
        assert len(ws.sent) == 1
        return ws.sent[0]

    payload = asyncio.run(run_case())
    assert payload["type"] == "result"
    assert payload["run_id"] == "run-123"
    assert payload["step_id"] == "step-abc"
    assert payload["command_id"] == "step-abc"
    assert payload["status"] == "completed"
    assert payload["data"]["args"]["url"] == "https://example.com"

