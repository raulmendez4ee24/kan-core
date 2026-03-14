import asyncio
from typing import Any, Dict

import pytest

from brain.execution_guard import reset_execution_guard_state_for_tests
import tools.n8n_client as n8n_client


class _OkResponse:
    status_code = 200
    text = ""

    def raise_for_status(self) -> None:
        return None

    def json(self) -> Dict[str, Any]:
        return {"id": "wf-1"}


class _WebhookAcceptedResponse:
    status_code = 200
    text = '{"executionId":"exec-1"}'

    def raise_for_status(self) -> None:
        return None

    def json(self) -> Dict[str, Any]:
        return {"executionId": "exec-1"}


class _ExecutionSuccessResponse:
    status_code = 200
    text = ""

    def raise_for_status(self) -> None:
        return None

    def json(self) -> Dict[str, Any]:
        return {"id": "exec-1", "finished": True}


@pytest.fixture(autouse=True)
def reset_circuit() -> None:
    n8n_client._reset_circuit_state_for_tests()
    reset_execution_guard_state_for_tests()
    yield
    n8n_client._reset_circuit_state_for_tests()
    reset_execution_guard_state_for_tests()


def test_send_to_n8n_with_response_opens_circuit_after_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"count": 0}

    async def failing_post(*_args: Any, **_kwargs: Any) -> Any:
        calls["count"] += 1
        raise RuntimeError("n8n down")

    monkeypatch.setenv("N8N_WEBHOOK_URL", "https://n8n.example.com/webhook/test")
    monkeypatch.setenv("N8N_CIRCUIT_BREAKER_ENABLED", "true")
    monkeypatch.setenv("N8N_CIRCUIT_FAILURE_THRESHOLD", "2")
    monkeypatch.setenv("N8N_CIRCUIT_COOLDOWN_SECONDS", "120")
    monkeypatch.setattr(n8n_client, "post_json_with_retry", failing_post)

    assert asyncio.run(n8n_client.send_to_n8n_with_response({"x": 1}))["dispatched"] is False
    assert asyncio.run(n8n_client.send_to_n8n_with_response({"x": 2}))["dispatched"] is False
    # Third call should short-circuit and avoid calling n8n
    assert asyncio.run(n8n_client.send_to_n8n_with_response({"x": 3}))["dispatched"] is False
    assert calls["count"] == 2


def test_send_to_n8n_with_response_resets_circuit_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"count": 0}

    async def mixed_post(*_args: Any, **_kwargs: Any) -> Any:
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("temporary fail")
        return _OkResponse()

    monkeypatch.setenv("N8N_WEBHOOK_URL", "https://n8n.example.com/webhook/test")
    monkeypatch.setenv("N8N_CIRCUIT_BREAKER_ENABLED", "true")
    monkeypatch.setenv("N8N_CIRCUIT_FAILURE_THRESHOLD", "3")
    monkeypatch.setattr(n8n_client, "post_json_with_retry", mixed_post)

    assert asyncio.run(n8n_client.send_to_n8n_with_response({"x": 1}))["dispatched"] is False
    assert asyncio.run(n8n_client.send_to_n8n_with_response({"x": 2}))["dispatched"] is True
    # Circuit should be reset, so next call still goes through.
    assert asyncio.run(n8n_client.send_to_n8n_with_response({"x": 3}))["dispatched"] is True
    assert calls["count"] == 3


def test_create_workflow_short_circuits_when_open(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"count": 0}

    async def failing_post(*_args: Any, **_kwargs: Any) -> Any:
        calls["count"] += 1
        raise RuntimeError("n8n down")

    monkeypatch.setenv("N8N_API_URL", "https://n8n.example.com")
    monkeypatch.setenv("N8N_API_KEY", "token")
    monkeypatch.setenv("N8N_CIRCUIT_BREAKER_ENABLED", "true")
    monkeypatch.setenv("N8N_CIRCUIT_FAILURE_THRESHOLD", "1")
    monkeypatch.setenv("N8N_CIRCUIT_COOLDOWN_SECONDS", "120")
    monkeypatch.setattr(n8n_client, "post_json_with_retry", failing_post)

    assert asyncio.run(n8n_client.create_workflow({"name": "x"})) is None
    # Circuit already open from previous failure.
    assert asyncio.run(n8n_client.create_workflow({"name": "x"})) is None
    assert calls["count"] == 1


def test_dispatch_failure_does_not_open_create_workflow_circuit(monkeypatch: pytest.MonkeyPatch) -> None:
    dispatch_calls = {"count": 0}
    create_calls = {"count": 0}

    async def mixed_post(url: str, *_args: Any, **_kwargs: Any) -> Any:
        if "/api/v1/workflows" in url:
            create_calls["count"] += 1
            return _OkResponse()
        dispatch_calls["count"] += 1
        raise RuntimeError("webhook down")

    monkeypatch.setenv("N8N_WEBHOOK_URL", "https://n8n.example.com/webhook/test")
    monkeypatch.setenv("N8N_API_URL", "https://n8n.example.com")
    monkeypatch.setenv("N8N_API_KEY", "token")
    monkeypatch.setenv("N8N_CIRCUIT_BREAKER_ENABLED", "true")
    monkeypatch.setenv("N8N_CIRCUIT_FAILURE_THRESHOLD", "1")
    monkeypatch.setenv("N8N_CIRCUIT_COOLDOWN_SECONDS", "120")
    monkeypatch.setattr(n8n_client, "post_json_with_retry", mixed_post)

    assert asyncio.run(n8n_client.send_to_n8n_with_response({"x": 1}))["dispatched"] is False
    assert asyncio.run(n8n_client.create_workflow({"name": "x"})) == {"id": "wf-1"}
    assert dispatch_calls["count"] == 1
    assert create_calls["count"] == 1


def test_send_to_n8n_confirms_execution_when_execution_id_is_returned(monkeypatch: pytest.MonkeyPatch) -> None:
    async def ok_post(*_args: Any, **_kwargs: Any) -> Any:
        return _WebhookAcceptedResponse()

    async def ok_get(*_args: Any, **_kwargs: Any) -> Any:
        return _ExecutionSuccessResponse()

    monkeypatch.setenv("N8N_WEBHOOK_URL", "https://n8n.example.com/webhook/test")
    monkeypatch.setenv("N8N_API_URL", "https://n8n.example.com")
    monkeypatch.setenv("N8N_API_KEY", "token")
    monkeypatch.setenv("N8N_CONFIRM_WEBHOOK_EXECUTION", "true")
    monkeypatch.setattr(n8n_client, "post_json_with_retry", ok_post)
    monkeypatch.setattr(n8n_client, "request_with_retry", ok_get)

    result = asyncio.run(n8n_client.send_to_n8n_with_response({"x": 1}))

    assert result["dispatched"] is True
    assert result["confirmed"] is True
    assert result["pending"] is False
    assert result["execution_id"] == "exec-1"
    assert result["execution_status"] == "success"


def test_send_to_n8n_batches_large_delete_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"count": 0, "batch_sizes": []}

    async def ok_post(_url: str, payload: Dict[str, Any], **_kwargs: Any) -> Any:
        calls["count"] += 1
        record_ids = payload["action"]["arguments"]["record_ids"]
        calls["batch_sizes"].append(len(record_ids))
        return _OkResponse()

    monkeypatch.setenv("N8N_WEBHOOK_URL", "https://n8n.example.com/webhook/test")
    monkeypatch.setenv("AUTONOMY_DELETE_MAX_RECORDS", "2")
    monkeypatch.setattr(n8n_client, "post_json_with_retry", ok_post)

    result = asyncio.run(
        n8n_client.send_to_n8n_with_response(
            {
                "source": "test",
                "action": {"action": "delete_customer", "arguments": {"record_ids": [1, 2, 3, 4, 5]}},
            }
        )
    )

    assert calls["count"] == 3
    assert calls["batch_sizes"] == [2, 2, 1]
    assert result["dispatched"] is True
    assert result["batch_count"] == 3
    assert result["execution_status"] in {"batched_pending", "batched_dispatched"}


def test_send_to_n8n_pauses_amount_above_guardrail_without_split_target(monkeypatch: pytest.MonkeyPatch) -> None:
    async def ok_post(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("n8n should not be called when guardrail pauses")

    monkeypatch.setenv("N8N_WEBHOOK_URL", "https://n8n.example.com/webhook/test")
    monkeypatch.setenv("AUTONOMY_PAYMENT_MAX_AMOUNT_USD", "100")
    monkeypatch.setattr(n8n_client, "post_json_with_retry", ok_post)

    result = asyncio.run(
        n8n_client.send_to_n8n_with_response(
            {
                "source": "test",
                "action": {"action": "create_payment", "arguments": {"amount": 250.0}},
            }
        )
    )

    assert result["dispatched"] is False
    assert result["paused"] is True
    assert result["execution_status"] == "paused_by_guardrail"
    assert result["guardrail"]["pause_reason"] == "blast_radius_amount_exceeded"
