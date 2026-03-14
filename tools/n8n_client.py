import asyncio
import json
import logging
import os
import time
from typing import Any, Dict, Optional

import httpx

from brain.execution_guard import emit_execution_guard_audit, observe_guarded_execution, plan_guarded_execution
from observability import capture_exception
from tools.retry import post_json_with_retry, request_with_retry

# Response shape for dispatch calls: dispatched, pending, confirmed, execution_status.
N8N_DISPATCH_RESPONSE_KEYS = (
    "dispatched",
    "pending",
    "confirmed",
    "execution_id",
    "execution_status",
    "paused",
    "batch_count",
    "guardrail",
    "response_excerpt",
    "webhook_url",
    "status_code",
)

logger = logging.getLogger("kan_core.n8n")

_CIRCUIT_OPERATIONS = ("dispatch", "create_workflow")
_circuit_open_until: Dict[str, float] = {}
_circuit_failures: Dict[str, int] = {}


def _circuit_enabled() -> bool:
    return os.getenv("N8N_CIRCUIT_BREAKER_ENABLED", "true").lower() in {"1", "true", "yes"}


def _mock_mode_enabled() -> bool:
    return os.getenv("N8N_MOCK_MODE", "false").lower() in {"1", "true", "yes"}


def _failure_threshold() -> int:
    return max(1, int(os.getenv("N8N_CIRCUIT_FAILURE_THRESHOLD", "3")))


def _cooldown_seconds() -> float:
    return max(1.0, float(os.getenv("N8N_CIRCUIT_COOLDOWN_SECONDS", "60")))


def _circuit_is_open(operation: str) -> bool:
    return _circuit_enabled() and time.monotonic() < float(_circuit_open_until.get(operation) or 0.0)


def _record_failure(operation: str) -> None:
    failures = int(_circuit_failures.get(operation) or 0) + 1
    _circuit_failures[operation] = failures
    if _circuit_enabled() and failures >= _failure_threshold():
        _circuit_open_until[operation] = time.monotonic() + _cooldown_seconds()


def _record_success(operation: Optional[str] = None) -> None:
    if operation:
        _circuit_failures[operation] = 0
        _circuit_open_until[operation] = 0.0
        return
    for key in _CIRCUIT_OPERATIONS:
        _circuit_failures[key] = 0
        _circuit_open_until[key] = 0.0


def _reset_circuit_state_for_tests() -> None:
    _record_success()


def _sanitize_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Remove circular references and non-serializable objects via JSON round-trip."""
    try:
        return json.loads(json.dumps(payload, default=str))
    except Exception:
        return {"error": "payload_not_serializable"}


def _response_excerpt(text: str, max_len: int = 200) -> str:
    if not text or not isinstance(text, str):
        return ""
    try:
        data = json.loads(text)
        excerpt = json.dumps(data, ensure_ascii=True)[:max_len]
        if len(str(data)) > max_len:
            excerpt += "..."
        return excerpt
    except Exception:
        return (text[:max_len] + "...") if len(text) > max_len else text


def _response_json(text: str) -> Optional[Dict[str, Any]]:
    if not text or not isinstance(text, str):
        return None
    try:
        payload = json.loads(text)
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _extract_execution_id(payload: Optional[Dict[str, Any]]) -> Optional[str]:
    if not isinstance(payload, dict):
        return None
    direct = payload.get("executionId") or payload.get("execution_id")
    if direct:
        return str(direct)
    data = payload.get("data")
    if isinstance(data, dict):
        nested = data.get("executionId") or data.get("execution_id")
        if nested:
            return str(nested)
    return None


def _execution_state(payload: Dict[str, Any]) -> str:
    raw = str(payload.get("status") or "").strip().lower()
    finished = payload.get("finished")
    if raw in {"success", "error", "failed", "canceled", "crashed", "running", "waiting"}:
        return raw
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    result_data = data.get("resultData") if isinstance(data.get("resultData"), dict) else {}
    has_error = bool(result_data.get("error")) or bool(payload.get("stoppedAt") and payload.get("retryOf"))
    if finished is True and has_error:
        return "error"
    if finished is True:
        return "success"
    if finished is False:
        return "running"
    return "unknown"


def _confirmation_enabled() -> bool:
    return os.getenv("N8N_CONFIRM_WEBHOOK_EXECUTION", "true").lower() in {"1", "true", "yes"}


async def _confirm_execution(execution_id: str) -> Optional[str]:
    if not execution_id or not _confirmation_enabled():
        return None
    base = (os.getenv("N8N_API_URL") or "").strip().rstrip("/")
    api_key = (os.getenv("N8N_API_KEY") or "").strip()
    if not base or not api_key:
        return None

    timeout = float(os.getenv("N8N_TIMEOUT", "10"))
    retries = int(os.getenv("N8N_RETRIES", "3"))
    backoff_base = float(os.getenv("N8N_BACKOFF_BASE", "0.5"))
    backoff_max = float(os.getenv("N8N_BACKOFF_MAX", "5.0"))
    poll_timeout = max(0.0, float(os.getenv("N8N_EXECUTION_POLL_TIMEOUT", "8")))
    poll_interval = max(0.2, float(os.getenv("N8N_EXECUTION_POLL_INTERVAL", "1.0")))
    headers = {"X-N8N-API-KEY": api_key}
    url = f"{base}/api/v1/executions/{execution_id}"
    deadline = time.monotonic() + poll_timeout

    while True:
        try:
            async with httpx.AsyncClient() as client:
                response = await request_with_retry(
                    client,
                    "GET",
                    url,
                    headers=headers,
                    timeout=timeout,
                    retries=max(1, retries),
                    backoff_base=backoff_base,
                    backoff_max=backoff_max,
                )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                return "unknown"
            state = _execution_state(payload)
            if state in {"success", "error", "failed", "canceled", "crashed"}:
                return state
            if time.monotonic() >= deadline:
                return state
        except Exception as exc:
            logger.warning("n8n execution confirmation failed: %s", exc)
            capture_exception(exc, {"service": "n8n", "operation": "confirm_execution"})
            return "unknown"

        if time.monotonic() >= deadline:
            return "running"
        await asyncio.sleep(poll_interval)


def _empty_dispatch_result(*, webhook_url: str) -> Dict[str, Any]:
    return {
        "dispatched": False,
        "pending": False,
        "confirmed": False,
        "execution_id": None,
        "execution_status": "not_dispatched",
        "paused": False,
        "batch_count": 1,
        "guardrail": {},
        "response_json": None,
        "response_excerpt": "",
        "webhook_url": webhook_url or "",
        "status_code": None,
    }


async def _dispatch_to_n8n_once(payload: Dict[str, Any], *, webhook_url: str) -> Dict[str, Any]:
    out = _empty_dispatch_result(webhook_url=webhook_url)
    if not webhook_url:
        logger.debug("n8n: N8N_WEBHOOK_URL not set")
        return out
    if _circuit_is_open("dispatch"):
        logger.warning("n8n: circuit open, skipping request")
        return out

    timeout = float(os.getenv("N8N_TIMEOUT", "10"))
    retries = int(os.getenv("N8N_RETRIES", "3"))
    backoff_base = float(os.getenv("N8N_BACKOFF_BASE", "0.5"))
    backoff_max = float(os.getenv("N8N_BACKOFF_MAX", "5.0"))
    try:
        response = await post_json_with_retry(
            webhook_url,
            payload,
            timeout=timeout,
            retries=retries,
            backoff_base=backoff_base,
            backoff_max=backoff_max,
        )
        status_code = getattr(response, "status_code", None)
        body = getattr(response, "text", "") or ""
        logger.info(
            "n8n dispatch url=%s status_code=%s body_excerpt=%s",
            webhook_url[:80] + "..." if len(webhook_url) > 80 else webhook_url,
            status_code,
            _response_excerpt(body)[:120],
        )
        response.raise_for_status()
        _record_success("dispatch")
        body_json = _response_json(body)
        execution_id = _extract_execution_id(body_json)
        execution_status = "accepted"
        confirmed = False
        pending = True
        if execution_id:
            confirmed_state = await _confirm_execution(execution_id)
            if confirmed_state:
                execution_status = confirmed_state
                confirmed = confirmed_state in {"success", "error", "failed", "canceled", "crashed"}
                pending = not confirmed
        out["dispatched"] = True
        out["pending"] = pending
        out["confirmed"] = confirmed
        out["execution_id"] = execution_id
        out["execution_status"] = execution_status
        out["status_code"] = status_code
        out["response_json"] = body_json
        out["response_excerpt"] = _response_excerpt(body)
        return out
    except Exception as exc:
        _record_failure("dispatch")
        logger.exception("n8n webhook request failed")
        capture_exception(exc, {"service": "n8n"})
        return out


def _merge_batch_results(
    *,
    webhook_url: str,
    guardrail: Dict[str, Any],
    batch_results: list[Dict[str, Any]],
) -> Dict[str, Any]:
    out = _empty_dispatch_result(webhook_url=webhook_url)
    out["batch_count"] = max(1, len(batch_results))
    out["guardrail"] = guardrail
    out["batch_results"] = batch_results
    if not batch_results:
        return out

    out["dispatched"] = any(bool(item.get("dispatched")) for item in batch_results)
    out["pending"] = any(bool(item.get("pending")) for item in batch_results)
    out["confirmed"] = all(bool(item.get("confirmed")) for item in batch_results if item.get("dispatched")) and out["dispatched"]
    out["execution_id"] = batch_results[-1].get("execution_id")
    out["status_code"] = batch_results[-1].get("status_code")
    out["response_excerpt"] = batch_results[-1].get("response_excerpt") or ""

    statuses = [str(item.get("execution_status") or "not_dispatched") for item in batch_results]
    failed = any(status in {"error", "failed", "canceled", "crashed", "not_dispatched"} for status in statuses)
    if failed and out["dispatched"]:
        out["execution_status"] = "partial_batch_failure"
    elif out["dispatched"] and out["confirmed"] and all(status == "success" for status in statuses):
        out["execution_status"] = "success"
    elif out["dispatched"]:
        out["execution_status"] = "batched_pending" if out["pending"] else "batched_dispatched"
    return out


async def send_to_n8n_with_response(payload: Dict[str, Any]) -> Dict[str, Any]:
    """POST to n8n webhook with local preflight guards and per-operation batching."""
    webhook_url = (os.getenv("N8N_WEBHOOK_URL") or "").strip()
    out = _empty_dispatch_result(webhook_url=webhook_url)
    if _mock_mode_enabled():
        out["execution_status"] = "not_dispatched"
        out["response_excerpt"] = "mock_mode"
        return out
    if not webhook_url:
        logger.debug("n8n: N8N_WEBHOOK_URL not set")
        return out
    if _circuit_is_open("dispatch"):
        logger.warning("n8n: circuit open, skipping request")
        return out

    payload = _sanitize_payload(payload)
    guardrail = plan_guarded_execution(payload, channel="n8n")
    out["guardrail"] = guardrail

    if guardrail.get("dry_run_preview"):
        logger.info("n8n preflight dry-run action=%s preview=%s", guardrail.get("action_name"), guardrail.get("dry_run_preview"))
        await emit_execution_guard_audit(payload, stage="preflight", guardrail=guardrail)

    if guardrail.get("should_pause"):
        out["paused"] = True
        out["execution_status"] = "paused_by_guardrail"
        out["response_excerpt"] = str(guardrail.get("pause_reason") or "paused")
        await emit_execution_guard_audit(payload, stage="paused", guardrail=guardrail)
        return out

    batch_payloads = list(guardrail.get("batch_payloads") or [])
    if not batch_payloads:
        guarded_payload = dict(payload)
        meta = guarded_payload.get("_autonomy_guard")
        meta = dict(meta) if isinstance(meta, dict) else {}
        meta.update(
            {
                "dry_run_preview": guardrail.get("dry_run_preview"),
                "rollback_snapshot": guardrail.get("rollback_snapshot"),
                "context_signature": guardrail.get("context_signature"),
            }
        )
        guarded_payload["_autonomy_guard"] = meta
        result = await _dispatch_to_n8n_once(guarded_payload, webhook_url=webhook_url)
        tripwire = observe_guarded_execution(guardrail, result)
        await emit_execution_guard_audit(payload, stage="tripwire", guardrail=guardrail, tripwire=tripwire, result=result)
        out.update(result)
        out["guardrail"] = {**guardrail, "tripwire": tripwire}
        return out

    batch_results: list[Dict[str, Any]] = []
    for batched_payload in batch_payloads:
        guarded_payload = _sanitize_payload(batched_payload)
        meta = guarded_payload.get("_autonomy_guard")
        meta = dict(meta) if isinstance(meta, dict) else {}
        meta.update(
            {
                "dry_run_preview": guardrail.get("dry_run_preview"),
                "rollback_snapshot": guardrail.get("rollback_snapshot"),
                "context_signature": guardrail.get("context_signature"),
            }
        )
        guarded_payload["_autonomy_guard"] = meta
        result = await _dispatch_to_n8n_once(guarded_payload, webhook_url=webhook_url)
        tripwire = observe_guarded_execution(guardrail, result)
        await emit_execution_guard_audit(payload, stage="tripwire", guardrail=guardrail, tripwire=tripwire, result=result)
        batch_results.append({**result, "tripwire": tripwire})
        if not bool(result.get("dispatched")) or str(result.get("execution_status") or "") in {
            "error",
            "failed",
            "canceled",
            "crashed",
            "not_dispatched",
        }:
            break

    merged = _merge_batch_results(
        webhook_url=webhook_url,
        guardrail={**guardrail, "tripwire": batch_results[-1].get("tripwire") if batch_results else {"triggered": False}},
        batch_results=batch_results,
    )
    return merged


def build_workflow_url(workflow_id: str) -> Optional[str]:
    base = (
        os.getenv("N8N_WEB_URL")
        or os.getenv("N8N_EDITOR_BASE_URL")
        or os.getenv("N8N_API_URL")
        or os.getenv("N8N_BASE_URL")
        or ""
    ).strip().rstrip("/")
    token = str(workflow_id or "").strip()
    if not base or not token:
        return None
    return f"{base}/workflow/{token}"


async def create_workflow(workflow: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    base = (os.getenv("N8N_API_URL") or "").strip().rstrip("/")
    api_key = (os.getenv("N8N_API_KEY") or "").strip()
    if not base or not api_key:
        return None
    if _circuit_is_open("create_workflow"):
        return None

    timeout = float(os.getenv("N8N_TIMEOUT", "15"))
    retries = int(os.getenv("N8N_RETRIES", "3"))
    backoff_base = float(os.getenv("N8N_BACKOFF_BASE", "0.5"))
    backoff_max = float(os.getenv("N8N_BACKOFF_MAX", "5.0"))
    headers = {"X-N8N-API-KEY": api_key, "Content-Type": "application/json"}
    url = f"{base}/api/v1/workflows"

    try:
        response = await post_json_with_retry(
            url,
            workflow,
            headers=headers,
            timeout=timeout,
            retries=retries,
            backoff_base=backoff_base,
            backoff_max=backoff_max,
        )
        response.raise_for_status()
        _record_success("create_workflow")
        data = response.json()
        return data if isinstance(data, dict) else None
    except Exception as exc:
        _record_failure("create_workflow")
        logger.exception("n8n create_workflow failed")
        capture_exception(exc, {"service": "n8n", "operation": "create_workflow"})
        return None
