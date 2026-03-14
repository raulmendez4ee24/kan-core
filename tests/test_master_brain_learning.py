import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

from brain.master_brain import MasterBrain


class _DummySessionCtx:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, exc_type, exc, tb):
        _ = (exc_type, exc, tb)
        return False


def _session_factory():
    return _DummySessionCtx()


def test_master_brain_learning_hooks_are_called(monkeypatch) -> None:
    client_id = str(uuid.uuid4())
    brain = MasterBrain(client_id=client_id)
    brain._session_factory = _session_factory
    brain._continuous_optimizer = AsyncMock(
        return_value=SimpleNamespace(
            objective_runs_analyzed=1,
            actions_analyzed=2,
            suggested_confidence_threshold=0.8,
            strategy="balanced_confirm",
            metadata={"success_rate": 0.7},
        )
    )
    brain._global_refresh = AsyncMock(return_value=[{"pattern": "a"}, {"pattern": "b"}])
    brain._tool_outcome_recorder = AsyncMock(return_value=None)

    results = [
        {"executor": "autonomy_v2", "status": "completed", "duration_ms": 22},
        {"executor": "web_agent", "status": "failed", "duration_ms": 34},
    ]

    payload = asyncio.run(
        brain._learn(
            "objetivo de prueba",
            {"intent": "business"},
            results,
        )
    )

    assert payload["refreshed_patterns"] == 2
    assert brain._continuous_optimizer.await_count == 1
    assert brain._global_refresh.await_count == 1
    assert brain._tool_outcome_recorder.await_count == 2
