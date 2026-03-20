from __future__ import annotations

import copy
import hashlib
import json
import logging
import os
import time
from collections import defaultdict, deque
from typing import Any, Deque, Dict, Iterable, List, Optional, Sequence, Tuple
from uuid import UUID

from config.env_helpers import env_float, env_int

logger = logging.getLogger("kan_core.execution_guard")

_RECENT_ACTIONS: Dict[str, Deque[float]] = defaultdict(deque)
_IMPACT_BASELINES: Dict[str, Deque[float]] = defaultdict(lambda: deque(maxlen=20))
_SPLITTABLE_LIST_KEYS = (
    "record_ids",
    "ids",
    "customer_ids",
    "client_ids",
    "payments",
    "charges",
    "items",
    "records",
    "targets",
    "emails",
    "messages",
)


def _env_flag(name: str, default: str = "true") -> bool:
    return os.getenv(name, default).lower() in {"1", "true", "yes"}


def _compact(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        compact: Dict[str, Any] = {}
        for key in sorted(value.keys()):
            compact[str(key)[:64]] = _compact(value[key])
        return compact
    if isinstance(value, list):
        return [_compact(item) for item in value[:10]]
    return str(value)[:300]


def _iter_mapping_roots(payload: Dict[str, Any]) -> Iterable[Tuple[Tuple[str, ...], Dict[str, Any]]]:
    seen: set[Tuple[str, ...]] = set()
    queue: list[Tuple[Tuple[str, ...], Dict[str, Any]]] = [((), payload)]
    nested_keys = ("action", "arguments", "args", "data", "body", "json")

    while queue:
        path, root = queue.pop(0)
        if path in seen:
            continue
        seen.add(path)
        yield path, root

        for key in nested_keys:
            value = root.get(key)
            if isinstance(value, dict):
                next_path = (*path, key)
                if next_path not in seen:
                    queue.append((next_path, value))


def _extract_action_name(payload: Dict[str, Any]) -> str:
    action = payload.get("action")
    if isinstance(action, dict):
        token = str(action.get("action") or action.get("name") or "").strip().lower()
        if token:
            return token
    token = str(payload.get("action_name") or payload.get("action") or payload.get("source") or "unknown").strip().lower()
    return token or "unknown"


def _extract_channel(payload: Dict[str, Any], channel: str) -> str:
    token = str(payload.get("source") or channel or "unknown").strip().lower()
    return token or "unknown"


def _keyword_match(action_name: str, keywords: Sequence[str]) -> bool:
    return any(keyword and keyword in action_name for keyword in keywords)


def _classify_operation(action_name: str, payload: Dict[str, Any], channel: str) -> str:
    if _keyword_match(action_name, ("delete", "remove", "purge", "drop", "cancel", "refund")):
        return "delete"
    if _keyword_match(action_name, ("payment", "charge", "transfer", "withdraw", "refund", "invoice")):
        return "payment"
    if _keyword_match(action_name, ("send_email", "email", "mail", "campaign", "notify")):
        return "messaging"
    method = str(payload.get("method") or "").upper().strip()
    if method in {"POST", "PUT", "PATCH", "DELETE"}:
        return "external_api"
    if channel == "desktop":
        return "desktop"
    return "generic"


def _safe_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _estimate_metrics(payload: Dict[str, Any]) -> Dict[str, Any]:
    records = 0
    affected_clients = 0
    amount_usd = 0.0
    list_metrics: Dict[str, int] = {}

    count_keys = ("count", "record_count", "delete_count", "affected_count", "batch_size")
    client_keys = ("affected_clients", "client_count", "customers_affected")
    amount_keys = ("amount", "amount_usd", "total", "total_amount", "payment_amount", "charge_amount")
    client_list_keys = ("client_ids", "customer_ids", "customers")

    for _root_path, root in _iter_mapping_roots(payload):
        for key in count_keys:
            metric = _safe_int(root.get(key))
            if metric is not None:
                records = max(records, metric)
        for key in client_keys:
            metric = _safe_int(root.get(key))
            if metric is not None:
                affected_clients = max(affected_clients, metric)
        for key in amount_keys:
            metric = _safe_float(root.get(key))
            if metric is not None:
                amount_usd = max(amount_usd, metric)
        for key, value in root.items():
            if isinstance(value, list):
                list_metrics[key] = max(len(value), list_metrics.get(key, 0))
                if key in client_list_keys:
                    affected_clients = max(affected_clients, len(value))
                if key in _SPLITTABLE_LIST_KEYS:
                    records = max(records, len(value))

    impact_score = float(records) + float(affected_clients * 2) + float(amount_usd / 100.0)
    return {
        "records": int(records),
        "affected_clients": int(affected_clients),
        "amount_usd": round(float(amount_usd), 2),
        "list_metrics": list_metrics,
        "impact_score": round(float(impact_score), 4),
    }


def _build_signature(action_name: str, payload: Dict[str, Any], channel: str) -> str:
    signature_payload: Dict[str, Any] = {
        "action": action_name,
        "channel": channel,
        "source": _extract_channel(payload, channel),
    }
    for path, root in _iter_mapping_roots(payload):
        scope = ".".join(path) if path else "payload"
        scoped: Dict[str, Any] = {}
        for key in ("integration", "url", "method", "selector", "text", "key", "button", "x", "y"):
            if key in root:
                scoped[key] = _compact(root.get(key))
        for key in _SPLITTABLE_LIST_KEYS:
            if key in root and isinstance(root.get(key), list):
                scoped[f"{key}_len"] = len(root.get(key) or [])
                scoped[f"{key}_sample"] = _compact((root.get(key) or [])[:3])
        if scoped:
            signature_payload[scope] = scoped
    encoded = json.dumps(signature_payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:24]


def _is_destructive(operation_type: str, action_name: str, payload: Dict[str, Any]) -> bool:
    if operation_type in {"delete", "payment", "messaging"}:
        return True
    method = str(payload.get("method") or "").upper().strip()
    if method in {"POST", "PUT", "PATCH", "DELETE"}:
        return True
    return _keyword_match(action_name, ("delete", "remove", "purge", "drop", "charge", "transfer", "send"))


def _is_reversible(operation_type: str, action_name: str) -> bool:
    if operation_type in {"delete", "payment", "messaging"}:
        return False
    return not _keyword_match(action_name, ("delete", "remove", "purge", "drop", "charge", "transfer", "send"))


def _build_dry_run_preview(
    *,
    action_name: str,
    operation_type: str,
    metrics: Dict[str, Any],
    reversible: bool,
) -> Dict[str, Any]:
    return {
        "simulated": True,
        "action": action_name,
        "operation_type": operation_type,
        "estimated_records": int(metrics.get("records") or 0),
        "estimated_amount_usd": float(metrics.get("amount_usd") or 0.0),
        "estimated_affected_clients": int(metrics.get("affected_clients") or 0),
        "reversible": reversible,
    }


def _build_snapshot(
    *,
    action_name: str,
    operation_type: str,
    payload: Dict[str, Any],
    reversible: bool,
    metrics: Dict[str, Any],
) -> Dict[str, Any]:
    restore_payload: Dict[str, Any] = {}
    payload_metadata: Dict[str, Any] = {}
    snapshot: Dict[str, Any] = {
        "captured_at": time.time(),
        "action": action_name,
        "operation_type": operation_type,
        "reversible": reversible,
        "impact": {
            "records": int(metrics.get("records") or 0),
            "amount_usd": float(metrics.get("amount_usd") or 0.0),
            "affected_clients": int(metrics.get("affected_clients") or 0),
        },
        "targets": {},
    }
    for _path, root in _iter_mapping_roots(payload):
        for key in ("id", "record_id", "client_id", "customer_id", "workflow_id", "email", "url"):
            if key in root and len(snapshot["targets"]) < 8:
                snapshot["targets"][key] = _compact(root.get(key))
        for key in _SPLITTABLE_LIST_KEYS:
            value = root.get(key)
            if isinstance(value, list) and len(snapshot["targets"]) < 8:
                snapshot["targets"][key] = _compact(value[:5])
        for key in ("external_id", "name", "email", "phone", "status", "source", "industry", "owner", "score"):
            if key in root and restore_payload.get(key) is None:
                restore_payload[key] = _compact(root.get(key))
        if "tags" in root and "tags" not in restore_payload:
            restore_payload["tags"] = _compact(root.get("tags"))
        for key in ("metadata", "lead_metadata", "customer_metadata"):
            value = root.get(key)
            if isinstance(value, dict):
                payload_metadata.update(_compact(value))
        for key in ("payment_id", "charge_id", "transaction_id", "invoice_id", "message_id", "subject", "to"):
            if key in root and key not in snapshot["targets"] and len(snapshot["targets"]) < 8:
                snapshot["targets"][key] = _compact(root.get(key))
    if payload_metadata:
        restore_payload["metadata"] = payload_metadata
    restore_payload = {key: value for key, value in restore_payload.items() if value not in (None, "", [], {})}
    if restore_payload:
        snapshot["restore_payload"] = restore_payload
    return snapshot


def _limit_for(operation_type: str) -> Dict[str, float]:
    if operation_type == "delete":
        return {
            "records": float(env_int("AUTONOMY_DELETE_MAX_RECORDS", 25, min_value=1)),
            "affected_clients": float(env_int("AUTONOMY_DELETE_MAX_AFFECTED_CLIENTS", 10, min_value=1)),
        }
    if operation_type == "payment":
        return {
            "amount_usd": float(env_float("AUTONOMY_PAYMENT_MAX_AMOUNT_USD", 5000.0, min_value=0.0)),
            "records": float(env_int("AUTONOMY_PAYMENT_MAX_ITEMS_PER_BATCH", 10, min_value=1)),
        }
    if operation_type == "external_api":
        return {
            "records": float(env_int("AUTONOMY_EXTERNAL_API_MAX_ITEMS_PER_BATCH", 50, min_value=1)),
            "affected_clients": float(env_int("AUTONOMY_EXTERNAL_API_MAX_AFFECTED_CLIENTS", 25, min_value=1)),
        }
    if operation_type == "messaging":
        return {
            "records": float(env_int("AUTONOMY_MESSAGING_MAX_RECIPIENTS", 100, min_value=1)),
        }
    return {
        "records": float(env_int("AUTONOMY_GENERIC_MAX_ITEMS_PER_BATCH", 50, min_value=1)),
        "affected_clients": float(env_int("AUTONOMY_GENERIC_MAX_AFFECTED_CLIENTS", 25, min_value=1)),
    }


def _candidate_split_keys(operation_type: str) -> Sequence[str]:
    if operation_type == "delete":
        return ("record_ids", "ids", "customer_ids", "client_ids", "records", "items")
    if operation_type == "payment":
        return ("payments", "charges", "items")
    if operation_type == "messaging":
        return ("emails", "messages", "targets", "items")
    return _SPLITTABLE_LIST_KEYS


def _locate_splittable_list(payload: Dict[str, Any], operation_type: str) -> Tuple[Optional[Tuple[str, ...]], Optional[List[Any]]]:
    for root_path, root in _iter_mapping_roots(payload):
        for key in _candidate_split_keys(operation_type):
            value = root.get(key)
            if isinstance(value, list) and value:
                return (*root_path, key), list(value)
    return None, None


def _set_path(target: Dict[str, Any], path: Sequence[str], value: Any) -> None:
    if not path:
        return
    cursor: Any = target
    for key in path[:-1]:
        cursor = cursor[key]
    cursor[path[-1]] = value


def _chunk_payload(payload: Dict[str, Any], path: Sequence[str], items: Sequence[Any], size: int) -> List[Dict[str, Any]]:
    batched: List[Dict[str, Any]] = []
    chunk_size = max(1, size)
    total = max(1, (len(items) + chunk_size - 1) // chunk_size)
    for index, start in enumerate(range(0, len(items), chunk_size), start=1):
        cloned = copy.deepcopy(payload)
        chunk = list(items[start : start + chunk_size])
        _set_path(cloned, path, chunk)
        meta = cloned.get("_autonomy_guard")
        if not isinstance(meta, dict):
            meta = {}
        meta = dict(meta)
        meta["batch"] = {"index": index, "count": total, "size": len(chunk)}
        cloned["_autonomy_guard"] = meta
        batched.append(cloned)
    return batched


def _loop_window_seconds() -> float:
    return max(5.0, env_float("AUTONOMY_RECENT_ACTION_WINDOW_SECONDS", 300.0, min_value=0.0))


def _loop_repeat_limit() -> int:
    return max(2, env_int("AUTONOMY_RECENT_ACTION_REPEAT_LIMIT", 3, min_value=1))


def _baseline_ratio() -> float:
    return max(1.1, env_float("AUTONOMY_OUTPUT_ANOMALY_RATIO", 3.0, min_value=0.0))


def _trim_recent(signature: str, now: float) -> Deque[float]:
    bucket = _RECENT_ACTIONS[signature]
    cutoff = now - _loop_window_seconds()
    while bucket and bucket[0] < cutoff:
        bucket.popleft()
    return bucket


def _derive_observed_score(plan: Dict[str, Any], result: Dict[str, Any]) -> float:
    for key in ("observed_impact_score", "impact_score"):
        metric = _safe_float(result.get(key))
        if metric is not None:
            return max(0.0, metric)
    parsed = result.get("response_json")
    if isinstance(parsed, dict):
        for key in ("records", "record_count", "affected_count", "count"):
            metric = _safe_float(parsed.get(key))
            if metric is not None:
                return max(0.0, metric)
    impact = plan.get("estimated_impact")
    if isinstance(impact, dict):
        metric = _safe_float(impact.get("impact_score"))
        if metric is not None:
            return max(0.0, metric)
    return 0.0


def _register_attempt(signature: str, now: float) -> int:
    bucket = _trim_recent(signature, now)
    bucket.append(now)
    return len(bucket)


def reset_execution_guard_state_for_tests() -> None:
    _RECENT_ACTIONS.clear()
    _IMPACT_BASELINES.clear()


def _extract_organization_uuid(payload: Dict[str, Any]) -> Optional[UUID]:
    candidates = [
        payload.get("organization_id"),
        payload.get("client_id"),
    ]
    data = payload.get("data")
    if isinstance(data, dict):
        candidates.extend([data.get("organization_id"), data.get("client_id")])

    for candidate in candidates:
        token = str(candidate or "").strip()
        if not token:
            continue
        try:
            return UUID(token)
        except ValueError:
            continue
    return None


async def emit_execution_guard_audit(
    payload: Dict[str, Any],
    *,
    stage: str,
    guardrail: Dict[str, Any],
    tripwire: Optional[Dict[str, Any]] = None,
    result: Optional[Dict[str, Any]] = None,
) -> None:
    if not _env_flag("AUTONOMY_GUARD_AUDIT_ENABLED", "true"):
        return

    organization_id = _extract_organization_uuid(payload)
    if organization_id is None:
        return

    stage_token = str(stage or "").strip().lower()
    if stage_token not in {"preflight", "paused", "tripwire"}:
        return

    metadata: Dict[str, Any] = {
        "action_name": str(guardrail.get("action_name") or "unknown"),
        "channel": str(guardrail.get("channel") or payload.get("source") or "unknown"),
        "operation_type": str(guardrail.get("operation_type") or "generic"),
        "context_signature": str(guardrail.get("context_signature") or ""),
        "destructive": bool(guardrail.get("destructive")),
        "reversible": bool(guardrail.get("reversible", True)),
        "estimated_impact": _compact(guardrail.get("estimated_impact") or {}),
        "pause_reason": guardrail.get("pause_reason"),
        "dry_run_preview": _compact(guardrail.get("dry_run_preview")),
        "rollback_snapshot": _compact(guardrail.get("rollback_snapshot")),
        "notes": _compact(list(guardrail.get("notes") or [])),
    }
    if tripwire is not None:
        metadata["tripwire"] = _compact(tripwire)
    if result is not None:
        metadata["result"] = _compact(
            {
                "dispatched": result.get("dispatched"),
                "pending": result.get("pending"),
                "confirmed": result.get("confirmed"),
                "execution_status": result.get("execution_status"),
                "status_code": result.get("status_code"),
            }
        )

    if stage_token == "preflight" and not metadata["destructive"]:
        return

    status = "ok"
    detail = None
    if stage_token == "paused":
        status = "warning"
        detail = str(guardrail.get("pause_reason") or "paused_by_guardrail")
    elif stage_token == "tripwire":
        if not (tripwire or {}).get("triggered"):
            return
        status = "warning"
        detail = "output_anomaly_detected"

    try:
        from brain.audit import write_audit_log
        from database import AsyncSessionLocal

        async with AsyncSessionLocal() as session:
            await write_audit_log(
                session,
                organization_id=organization_id,
                actor_user_id=None,
                action=f"execution_guard.{stage_token}",
                resource="autonomy_action",
                resource_id=str(metadata.get("context_signature") or "") or None,
                status=status,
                detail=detail,
                metadata=metadata,
            )
    except Exception as exc:
        logger.debug("execution guard audit log skipped: %s", exc)


def summarize_guard_audit_entries(
    entries: Sequence[Dict[str, Any]],
    *,
    sample_limit: int = 3,
) -> Dict[str, Any]:
    summary: Dict[str, Any] = {
        "events": int(len(entries)),
        "preflight_count": 0,
        "paused_count": 0,
        "tripwire_count": 0,
        "destructive_previews": 0,
        "irreversible_actions": 0,
        "recent_pauses": [],
        "recent_tripwires": [],
        "recent_rollback_snapshots": [],
        "by_operation": {},
    }
    by_operation: Dict[str, int] = {}

    for entry in entries:
        action = str(entry.get("action") or "").strip().lower()
        metadata = dict(entry.get("metadata") or {})
        operation = str(metadata.get("operation_type") or "generic")
        by_operation[operation] = int(by_operation.get(operation) or 0) + 1
        if metadata.get("dry_run_preview"):
            summary["destructive_previews"] += 1
        if not bool(metadata.get("reversible", True)):
            summary["irreversible_actions"] += 1

        if action == "execution_guard.preflight":
            summary["preflight_count"] += 1
            rollback = metadata.get("rollback_snapshot")
            if rollback and len(summary["recent_rollback_snapshots"]) < sample_limit:
                summary["recent_rollback_snapshots"].append(_compact(rollback))
        elif action == "execution_guard.paused":
            summary["paused_count"] += 1
            if len(summary["recent_pauses"]) < sample_limit:
                summary["recent_pauses"].append(
                    {
                        "at": entry.get("created_at"),
                        "action_name": metadata.get("action_name"),
                        "reason": metadata.get("pause_reason"),
                        "operation_type": operation,
                    }
                )
        elif action == "execution_guard.tripwire":
            summary["tripwire_count"] += 1
            if len(summary["recent_tripwires"]) < sample_limit:
                summary["recent_tripwires"].append(
                    {
                        "at": entry.get("created_at"),
                        "action_name": metadata.get("action_name"),
                        "tripwire": _compact(metadata.get("tripwire") or {}),
                        "operation_type": operation,
                    }
                )

    summary["by_operation"] = by_operation
    return summary


def plan_guarded_execution(payload: Dict[str, Any], *, channel: str) -> Dict[str, Any]:
    if not _env_flag("AUTONOMY_EXECUTION_GUARDS_ENABLED", "true"):
        return {
            "enabled": False,
            "should_pause": False,
            "batch_payloads": [],
        }

    action_name = _extract_action_name(payload)
    operation_type = _classify_operation(action_name, payload, channel)
    destructive = _is_destructive(operation_type, action_name, payload)
    reversible = _is_reversible(operation_type, action_name)
    metrics = _estimate_metrics(payload)
    signature = _build_signature(action_name, payload, channel)
    now = time.monotonic()
    prior_attempts = len(_trim_recent(signature, now))
    should_pause = False
    pause_reason: Optional[str] = None
    batch_payloads: List[Dict[str, Any]] = []
    limits = _limit_for(operation_type)
    notes: List[str] = []

    if prior_attempts >= (_loop_repeat_limit() - 1):
        should_pause = True
        pause_reason = "loop_detected"
        notes.append("recent_action_loop")

    records_limit = int(limits.get("records") or 0)
    records = int(metrics.get("records") or 0)
    if not should_pause and records_limit > 0 and records > records_limit:
        split_path, split_items = _locate_splittable_list(payload, operation_type)
        if split_path and split_items and len(split_items) > records_limit:
            batch_payloads = _chunk_payload(payload, split_path, split_items, records_limit)
            notes.append("batched_by_record_limit")
        else:
            should_pause = True
            pause_reason = "blast_radius_records_exceeded"
            notes.append("record_limit_exceeded")

    clients_limit = int(limits.get("affected_clients") or 0)
    affected_clients = int(metrics.get("affected_clients") or 0)
    if not should_pause and clients_limit > 0 and affected_clients > clients_limit:
        should_pause = True
        pause_reason = "blast_radius_clients_exceeded"
        notes.append("client_limit_exceeded")

    amount_limit = float(limits.get("amount_usd") or 0.0)
    amount_usd = float(metrics.get("amount_usd") or 0.0)
    if not should_pause and amount_limit > 0.0 and amount_usd > amount_limit:
        split_path, split_items = _locate_splittable_list(payload, operation_type)
        if split_path and split_items and len(split_items) > 1:
            per_item = amount_usd / max(1, len(split_items))
            if per_item <= amount_limit:
                batch_size = max(1, int(amount_limit // max(per_item, 1.0)))
                batch_payloads = _chunk_payload(payload, split_path, split_items, batch_size)
                notes.append("batched_by_amount_limit")
            else:
                should_pause = True
                pause_reason = "blast_radius_amount_exceeded"
                notes.append("amount_limit_exceeded")
        else:
            should_pause = True
            pause_reason = "blast_radius_amount_exceeded"
            notes.append("amount_limit_exceeded")

    dry_run_preview = (
        _build_dry_run_preview(
            action_name=action_name,
            operation_type=operation_type,
            metrics=metrics,
            reversible=reversible,
        )
        if destructive
        else None
    )
    rollback_snapshot = _build_snapshot(
        action_name=action_name,
        operation_type=operation_type,
        payload=payload,
        reversible=reversible,
        metrics=metrics,
    )

    if not should_pause:
        attempt_count = _register_attempt(signature, now)
    else:
        attempt_count = prior_attempts + 1

    return {
        "enabled": True,
        "action_name": action_name,
        "channel": channel,
        "operation_type": operation_type,
        "destructive": destructive,
        "reversible": reversible,
        "dry_run_preview": dry_run_preview,
        "rollback_snapshot": rollback_snapshot,
        "estimated_impact": metrics,
        "context_signature": signature,
        "recent_attempts": attempt_count,
        "should_pause": should_pause,
        "pause_reason": pause_reason,
        "batch_payloads": batch_payloads,
        "batch_count": len(batch_payloads),
        "notes": notes,
    }


def observe_guarded_execution(plan: Dict[str, Any], result: Dict[str, Any]) -> Dict[str, Any]:
    if not plan.get("enabled"):
        return {"triggered": False}

    signature = str(plan.get("context_signature") or "").strip()
    if not signature:
        return {"triggered": False}

    bucket = _IMPACT_BASELINES[signature]
    observed_score = _derive_observed_score(plan, result)
    ratio = _baseline_ratio()
    baseline_avg = (sum(bucket) / len(bucket)) if bucket else 0.0
    triggered = len(bucket) >= 3 and observed_score > max(1.0, baseline_avg * ratio)
    bucket.append(observed_score)

    tripwire = {
        "triggered": triggered,
        "observed_score": round(observed_score, 4),
        "baseline_avg": round(baseline_avg, 4),
        "ratio_threshold": ratio,
        "sample_count": len(bucket),
    }
    if triggered:
        logger.warning(
            "execution tripwire triggered action=%s signature=%s observed=%.4f baseline=%.4f",
            plan.get("action_name"),
            signature,
            observed_score,
            baseline_avg,
        )
    return tripwire
