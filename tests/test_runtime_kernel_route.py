from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi.testclient import TestClient

import main
import routes.runtime_kernel as runtime_route
from security import require_client_token

TEST_CLIENT_ID = "11111111-1111-1111-1111-111111111111"


def test_runtime_replay_route_success(monkeypatch) -> None:
    main.app.dependency_overrides[require_client_token] = lambda: TEST_CLIENT_ID

    async def fake_replay_run(**_kwargs):
        return SimpleNamespace(
            run_id="new-run-1",
            session_id="sess-1",
            status="queued",
            result={},
            updated_at=datetime.now(timezone.utc),
        )

    monkeypatch.setattr(runtime_route.runtime_kernel, "replay_run", fake_replay_run)
    monkeypatch.setattr(runtime_route.runtime_kernel, "queue_depth", lambda _session_id: 1)

    with TestClient(main.app) as client:
        response = client.post("/runtime/replay/source-run-1", json={"context_patch": {"x": 1}})

    assert response.status_code == 200
    body = response.json()
    assert body["accepted"] is True
    assert body["replay_of"] == "source-run-1"
    assert body["run_id"] == "new-run-1"

    main.app.dependency_overrides = {}


def test_runtime_replay_route_not_found(monkeypatch) -> None:
    main.app.dependency_overrides[require_client_token] = lambda: TEST_CLIENT_ID

    async def fake_replay_run(**_kwargs):
        raise ValueError("source_run_not_found")

    monkeypatch.setattr(runtime_route.runtime_kernel, "replay_run", fake_replay_run)

    with TestClient(main.app) as client:
        response = client.post("/runtime/replay/missing-run", json={})

    assert response.status_code == 404
    assert response.json()["detail"] == "source_run_not_found"

    main.app.dependency_overrides = {}


def test_runtime_journal_route_success(monkeypatch) -> None:
    main.app.dependency_overrides[require_client_token] = lambda: TEST_CLIENT_ID

    async def fake_get_run_journal(**_kwargs):
        return [
            {"ts": "2026-02-23T00:00:00+00:00", "phase": "lifecycle.queued", "payload": {"goal": "x"}},
            {"ts": "2026-02-23T00:00:01+00:00", "phase": "lifecycle.running", "payload": {}},
        ]

    monkeypatch.setattr(runtime_route.runtime_kernel, "get_run_journal", fake_get_run_journal)

    with TestClient(main.app) as client:
        response = client.get("/runtime/journal/run-1?phase=lifecycle.running&limit=10")

    assert response.status_code == 200
    body = response.json()
    assert body["run_id"] == "run-1"
    assert body["count"] == 2
    assert body["items"][0]["phase"] == "lifecycle.queued"

    main.app.dependency_overrides = {}


def test_runtime_journal_route_not_found_returns_empty(monkeypatch) -> None:
    main.app.dependency_overrides[require_client_token] = lambda: TEST_CLIENT_ID

    async def fake_get_run_journal(**_kwargs):
        return None

    monkeypatch.setattr(runtime_route.runtime_kernel, "get_run_journal", fake_get_run_journal)

    with TestClient(main.app) as client:
        response = client.get("/runtime/journal/missing-run")

    assert response.status_code == 200
    body = response.json()
    assert body["run_id"] == "missing-run"
    assert body["count"] == 0
    assert body["items"] == []

    main.app.dependency_overrides = {}


def test_runtime_journal_jsonl_route(monkeypatch) -> None:
    main.app.dependency_overrides[require_client_token] = lambda: TEST_CLIENT_ID

    async def fake_get_run_journal(**_kwargs):
        return [
            {"ts": "2026-02-23T00:00:00+00:00", "phase": "lifecycle.queued", "payload": {"goal": "x"}},
            {"ts": "2026-02-23T00:00:01+00:00", "phase": "lifecycle.running", "payload": {}},
        ]

    monkeypatch.setattr(runtime_route.runtime_kernel, "get_run_journal", fake_get_run_journal)

    with TestClient(main.app) as client:
        response = client.get("/runtime/journal/run-1/jsonl?limit=10")

    assert response.status_code == 200
    lines = [x for x in response.text.splitlines() if x.strip()]
    assert len(lines) == 2
    assert '"phase": "lifecycle.queued"' in lines[0]

    main.app.dependency_overrides = {}


def test_runtime_replay_route_non_terminal_conflict(monkeypatch) -> None:
    main.app.dependency_overrides[require_client_token] = lambda: TEST_CLIENT_ID

    async def fake_replay_run(**_kwargs):
        raise ValueError("source_run_not_terminal")

    monkeypatch.setattr(runtime_route.runtime_kernel, "replay_run", fake_replay_run)

    with TestClient(main.app) as client:
        response = client.post("/runtime/replay/inflight-run", json={})

    assert response.status_code == 409
    assert response.json()["detail"] == "source_run_not_terminal"

    main.app.dependency_overrides = {}
