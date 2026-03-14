import asyncio
import hashlib
import hmac
import json
import logging
import os
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from typing import Any, Deque, Dict, Optional
from urllib.parse import urlparse
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from sqlalchemy import and_, desc, func, select, text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from brain.audit import write_audit_log
from brain.execution_guard import plan_guarded_execution, summarize_guard_audit_entries
from database import AsyncSessionLocal, get_session
from models import (
    AuditLog,
    DesktopAgent,
    DesktopCommand,
    DesktopCommandResult,
    WorkflowRecording,
    WorkflowRecordingStep,
)
from recovery.recovery_service import trigger_recovery_for_failed_command
from brain.workflow_recorder.recorder import maybe_append_step_for_result
from brain.policy_engine import (
    load_effective_policy,
    policy_requires_human_approval_for_action,
    validate_desktop_action,
)
from schemas.desktop_agent import (
    DesktopAgentRead,
    DesktopAgentsResponse,
    DesktopCommandApproveRequest,
    DesktopCommandCreateRequest,
    DesktopCommandRead,
    DesktopCommandResultRead,
    WsAckEnvelope,
    WsCommandEnvelope,
    WsHumanEventEnvelope,
    WsResultEnvelope,
)
from security import require_client_token, validate_client_credentials
from tools.n8n_client import send_to_n8n_with_response

router = APIRouter(prefix="/desktop-agent", tags=["desktop-agent"])
logger = logging.getLogger("kan_core.desktop_agent")


_ALLOWED_ACTIONS = {
    "mover_mouse",
    "click",
    "type_text",
    "press_key",
    "open_program",
    "take_screenshot",
    "browser_launch",
    "browser_goto",
    "browser_click",
    "browser_fill",
    "browser_press",
    "browser_extract",
    "browser_screenshot",
    "browser_close",
}
_RISK_LEVEL_ORDER = {"low": 1, "medium": 2, "high": 3}


def _feature_enabled() -> bool:
    return os.getenv("ENABLE_DESKTOP_AGENT", "false").lower() in {"1", "true", "yes"}


def _runner_mode() -> str:
    token = str(os.getenv("RUNNER_MODE", "local") or "local").strip().lower()
    return "remote" if token == "remote" else "local"


def _enterprise_policies_enabled() -> bool:
    return os.getenv("ENABLE_ENTERPRISE_POLICIES", "false").lower() in {"1", "true", "yes"}


def _human_recorder_enabled() -> bool:
    return os.getenv("ENABLE_HUMAN_RECORDER", "false").lower() in {"1", "true", "yes"}


def _workflow_recorder_enabled() -> bool:
    return os.getenv("ENABLE_WORKFLOW_RECORDER", "false").lower() in {"1", "true", "yes"}


def _parse_uuid(value: str, *, field: str) -> UUID:
    try:
        return UUID(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid {field}") from exc


def _parse_optional_uuid(value: Optional[str], *, field: str) -> Optional[UUID]:
    if not value:
        return None
    return _parse_uuid(value, field=field)


def _browser_domain_allowlist() -> list[str]:
    raw = os.getenv("DESKTOP_AGENT_BROWSER_DOMAIN_ALLOWLIST_JSON", "[]")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, list):
        return []
    domains: list[str] = []
    for item in payload:
        token = str(item).strip().lower()
        if token and token not in domains:
            domains.append(token)
    return domains


def _is_allowed_browser_url(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").strip().lower()
    scheme = (parsed.scheme or "").strip().lower()
    if scheme not in {"http", "https"} or not host:
        return False
    allowlist = _browser_domain_allowlist()
    if not allowlist:
        return False
    return any(host == item or host.endswith(f".{item}") for item in allowlist)


def _action_risk_level(action: str, args: Dict[str, Any]) -> str:
    action_name = str(action or "").strip().lower()
    if action_name in {"take_screenshot", "browser_screenshot", "browser_extract", "browser_goto"}:
        return "low"
    if action_name in {"browser_click", "click"}:
        has_xy = ("x" in args) or ("y" in args)
        return "high" if has_xy else "medium"
    if action_name in {"browser_fill", "browser_press", "type_text", "press_key", "browser_launch"}:
        return "medium"
    return "high"


def _normalize_human_event_action(event_type: str) -> Optional[str]:
    token = str(event_type or "").strip()
    if not token:
        return None
    allowed = {
        "click",
        "type_text",
        "press_key",
        "browser_click",
        "browser_fill",
        "browser_press",
    }
    if token not in allowed:
        return None
    return token


async def _persist_human_event_step(
    session: AsyncSession,
    *,
    organization_id: UUID,
    agent_id: UUID,
    payload: WsHumanEventEnvelope,
) -> WorkflowRecordingStep:
    if not _workflow_recorder_enabled():
        raise HTTPException(status_code=404, detail="Feature ENABLE_WORKFLOW_RECORDER disabled")
    if not _human_recorder_enabled():
        raise HTTPException(status_code=404, detail="Feature ENABLE_HUMAN_RECORDER disabled")

    recording_uuid = _parse_uuid(payload.recording_id, field="recording_id")
    rec = (
        await session.execute(
            select(WorkflowRecording).where(
                WorkflowRecording.organization_id == organization_id,
                WorkflowRecording.id == recording_uuid,
                WorkflowRecording.agent_id == agent_id,
            )
        )
    ).scalars().first()
    if not rec:
        raise HTTPException(status_code=404, detail="Recording not found for agent")
    if rec.status != "recording":
        raise HTTPException(status_code=409, detail="Recording is not active")

    action = _normalize_human_event_action(payload.event_type)
    if not action:
        raise HTTPException(status_code=400, detail="Unsupported human event_type")

    max_stmt = select(func.max(WorkflowRecordingStep.step_order)).where(
        WorkflowRecordingStep.recording_id == rec.id
    )
    max_order = (await session.execute(max_stmt)).scalars().first()
    step_order = int(max_order or 0) + 1

    args = payload.payload if isinstance(payload.payload, dict) else {}
    ctx = payload.context if isinstance(payload.context, dict) else {}
    meta: Dict[str, Any] = {
        "source": "human_event",
        "event_ts": payload.ts,
        "event_type": payload.event_type,
        "context": ctx,
    }
    if bool(args.get("redacted")):
        meta["redacted"] = True

    step = WorkflowRecordingStep(
        recording_id=rec.id,
        organization_id=organization_id,
        agent_id=agent_id,
        step_order=step_order,
        desktop_command_id=None,
        action=action,
        args=dict(args),
        result_status="success",
        duration_ms=None,
        error=None,
        step_metadata=meta,
    )
    session.add(step)
    await session.commit()
    await session.refresh(step)
    return step


def _validate_command_args(action: str, args: Dict[str, Any]) -> None:
    if action not in _ALLOWED_ACTIONS:
        raise HTTPException(status_code=400, detail="Unsupported action")

    if action == "mover_mouse":
        x = args.get("x")
        y = args.get("y")
        if not isinstance(x, int) or not isinstance(y, int):
            raise HTTPException(status_code=400, detail="mover_mouse requiere x,y enteros")
        if x < 0 or y < 0:
            raise HTTPException(status_code=400, detail="mover_mouse no acepta coordenadas negativas")
    elif action == "click":
        button = str(args.get("button", "left")).lower()
        if button not in {"left", "right", "middle"}:
            raise HTTPException(status_code=400, detail="click.button invalido")
        clicks = args.get("clicks", 1)
        if not isinstance(clicks, int) or clicks < 1 or clicks > 5:
            raise HTTPException(status_code=400, detail="click.clicks invalido")
        has_x = "x" in args
        has_y = "y" in args
        if has_x != has_y:
            raise HTTPException(status_code=400, detail="click requiere x e y juntos")
        if has_x and has_y:
            x = args.get("x")
            y = args.get("y")
            if not isinstance(x, int) or not isinstance(y, int):
                raise HTTPException(status_code=400, detail="click.x y click.y deben ser enteros")
            if x < 0 or y < 0:
                raise HTTPException(status_code=400, detail="click.x y click.y no aceptan negativos")
    elif action == "type_text":
        text = args.get("text")
        if not isinstance(text, str) or not text.strip():
            raise HTTPException(status_code=400, detail="type_text requiere text")
    elif action == "press_key":
        key = args.get("key")
        if not isinstance(key, str) or not key.strip():
            raise HTTPException(status_code=400, detail="press_key requiere key")
    elif action == "open_program":
        name = args.get("name")
        if not isinstance(name, str) or not name.strip():
            raise HTTPException(status_code=400, detail="open_program requiere name")
    elif action == "browser_launch":
        headless = args.get("headless")
        if headless is not None and not isinstance(headless, bool):
            raise HTTPException(status_code=400, detail="browser_launch.headless debe ser boolean")
        channel = args.get("channel")
        if channel is not None:
            channel_token = str(channel).strip().lower()
            if channel_token not in {"chrome", "chromium"}:
                raise HTTPException(status_code=400, detail="browser_launch.channel invalido")
        start_url = args.get("start_url")
        if start_url is not None:
            if not isinstance(start_url, str) or not start_url.strip():
                raise HTTPException(status_code=400, detail="browser_launch.start_url invalido")
            # When enterprise policies are enabled, URL allowlist validation happens in policy_engine.
            if (not _enterprise_policies_enabled()) and (not _is_allowed_browser_url(start_url)):
                raise HTTPException(status_code=400, detail="browser_launch.start_url fuera de allowlist")
    elif action == "browser_goto":
        url = args.get("url")
        if not isinstance(url, str) or not url.strip():
            raise HTTPException(status_code=400, detail="browser_goto requiere url")
        # When enterprise policies are enabled, URL allowlist validation happens in policy_engine.
        if (not _enterprise_policies_enabled()) and (not _is_allowed_browser_url(url)):
            raise HTTPException(status_code=400, detail="browser_goto.url fuera de allowlist")
    elif action == "browser_click":
        selector = args.get("selector")
        text = args.get("text")
        role = args.get("role")
        has_pointer_mode = any(
            isinstance(item, str) and item.strip() for item in (selector, text, role)
        )
        has_x = "x" in args
        has_y = "y" in args
        has_coordinate_mode = has_x and has_y
        if has_x != has_y:
            raise HTTPException(status_code=400, detail="browser_click requiere x e y juntos")
        if has_coordinate_mode:
            x = args.get("x")
            y = args.get("y")
            if not isinstance(x, int) or not isinstance(y, int):
                raise HTTPException(status_code=400, detail="browser_click.x y y deben ser enteros")
            if x < 0 or y < 0:
                raise HTTPException(status_code=400, detail="browser_click.x y y no aceptan negativos")
        if not has_pointer_mode and not has_coordinate_mode:
            raise HTTPException(status_code=400, detail="browser_click requiere selector|text|role o x/y")
        timeout_ms = args.get("timeout_ms")
        if timeout_ms is not None and (not isinstance(timeout_ms, int) or timeout_ms < 500):
            raise HTTPException(status_code=400, detail="browser_click.timeout_ms invalido")
    elif action == "browser_fill":
        selector = args.get("selector")
        text = args.get("text")
        if not isinstance(selector, str) or not selector.strip():
            raise HTTPException(status_code=400, detail="browser_fill requiere selector")
        if not isinstance(text, str):
            raise HTTPException(status_code=400, detail="browser_fill requiere text")
    elif action == "browser_press":
        selector = args.get("selector")
        key = args.get("key")
        if not isinstance(selector, str) or not selector.strip():
            raise HTTPException(status_code=400, detail="browser_press requiere selector")
        if not isinstance(key, str) or not key.strip():
            raise HTTPException(status_code=400, detail="browser_press requiere key")
    elif action == "browser_extract":
        selector = args.get("selector")
        if not isinstance(selector, str) or not selector.strip():
            raise HTTPException(status_code=400, detail="browser_extract requiere selector")


def _is_human_approval_required(command: DesktopCommand) -> bool:
    metadata = command.command_metadata or {}
    required = bool(metadata.get("requires_human_approval"))
    approval = metadata.get("approval")
    if isinstance(approval, dict) and bool(approval.get("required")):
        required = True
    nested = metadata.get("metadata")
    if isinstance(nested, dict):
        nested_approval = nested.get("approval")
        if isinstance(nested_approval, dict) and bool(nested_approval.get("required")):
            required = True
    if not required:
        return False
    return command.approved_at is None


def _is_recovery_managed_command(command: DesktopCommand) -> bool:
    metadata = command.command_metadata or {}
    return str(metadata.get("recovery_control") or "").strip().lower() == "managed"


def _should_trigger_recovery(command: DesktopCommand, result: DesktopCommandResult) -> bool:
    if result.status == "success":
        return False
    if _is_recovery_managed_command(command):
        return False
    if _is_human_approval_required(command):
        return False
    return True


class DesktopWsManager:
    def __init__(self) -> None:
        self._connections: Dict[UUID, WebSocket] = {}
        self._rate_window: Dict[UUID, Deque[float]] = defaultdict(deque)

    def register(self, agent_id: UUID, websocket: WebSocket) -> None:
        self._connections[agent_id] = websocket

    async def unregister(self, agent_id: UUID) -> None:
        ws = self._connections.pop(agent_id, None)
        if ws is not None:
            try:
                await ws.close()
            except Exception:
                pass

    def is_connected(self, agent_id: UUID) -> bool:
        return agent_id in self._connections

    def allow_dispatch(self, agent_id: UUID) -> bool:
        max_per_minute = max(1, int(os.getenv("DESKTOP_AGENT_RATE_LIMIT_PER_MINUTE", "30")))
        now = time.time()
        window = self._rate_window[agent_id]
        while window and (now - window[0]) > 60.0:
            window.popleft()
        if len(window) >= max_per_minute:
            return False
        window.append(now)
        return True

    async def send_json(self, agent_id: UUID, payload: Dict[str, Any]) -> bool:
        ws = self._connections.get(agent_id)
        if ws is None:
            return False
        try:
            await ws.send_json(payload)
            return True
        except Exception:
            await self.unregister(agent_id)
            return False


_ws_manager = DesktopWsManager()


class DesktopOpsTelemetry:
    def __init__(self) -> None:
        self._ws_events: Deque[Dict[str, Any]] = deque()
        self._last_disconnect_ts: Dict[UUID, float] = {}
        self._retention_seconds = max(300, int(os.getenv("DESKTOP_OPS_RETENTION_SECONDS", "86400")))

    def _prune(self, *, now_ts: float) -> None:
        cutoff = now_ts - float(self._retention_seconds)
        while self._ws_events and float(self._ws_events[0].get("ts") or 0.0) < cutoff:
            self._ws_events.popleft()

    def on_ws_connected(self, *, agent_id: UUID) -> None:
        now_ts = time.time()
        self._prune(now_ts=now_ts)
        reconnect = agent_id in self._last_disconnect_ts
        self._ws_events.append(
            {
                "ts": now_ts,
                "agent_id": str(agent_id),
                "event": "connect",
                "reconnect": reconnect,
            }
        )

    def on_ws_disconnected(self, *, agent_id: UUID) -> None:
        now_ts = time.time()
        self._prune(now_ts=now_ts)
        self._last_disconnect_ts[agent_id] = now_ts
        self._ws_events.append(
            {
                "ts": now_ts,
                "agent_id": str(agent_id),
                "event": "disconnect",
                "reconnect": False,
            }
        )

    def ws_reconnect_metrics(self, *, window_minutes: int) -> Dict[str, Any]:
        now_ts = time.time()
        self._prune(now_ts=now_ts)
        cutoff = now_ts - (max(1, int(window_minutes)) * 60.0)
        connects = [x for x in self._ws_events if x.get("event") == "connect" and float(x.get("ts") or 0.0) >= cutoff]
        reconnects = [x for x in connects if bool(x.get("reconnect"))]
        total_connects = len(connects)
        reconnect_count = len(reconnects)
        reconnect_rate = (reconnect_count / total_connects) if total_connects > 0 else 0.0
        return {
            "window_minutes": int(window_minutes),
            "total_connects": total_connects,
            "reconnect_count": reconnect_count,
            "reconnect_rate": round(float(reconnect_rate), 4),
        }


_ops_telemetry = DesktopOpsTelemetry()


class DesktopOpsControlPlane:
    def __init__(self) -> None:
        self._last_persist_at: Dict[str, float] = {}
        self._last_alert_at: Dict[str, float] = {}

    def should_persist(self, *, organization_id: UUID) -> bool:
        interval = max(10.0, float(os.getenv("DESKTOP_OPS_PERSIST_INTERVAL_SECONDS", "60")))
        now_ts = time.time()
        key = str(organization_id)
        last = float(self._last_persist_at.get(key) or 0.0)
        if (now_ts - last) < interval:
            return False
        self._last_persist_at[key] = now_ts
        return True

    def should_emit_alert(self, *, alert_key: str) -> bool:
        cooldown = max(30.0, float(os.getenv("DESKTOP_OPS_ALERT_COOLDOWN_SECONDS", "300")))
        now_ts = time.time()
        last = float(self._last_alert_at.get(alert_key) or 0.0)
        if (now_ts - last) < cooldown:
            return False
        self._last_alert_at[alert_key] = now_ts
        return True


_ops_control_plane = DesktopOpsControlPlane()


def _is_timeout_error(value: Optional[str]) -> bool:
    token = str(value or "").strip().lower()
    return "timeout" in token


def _selector_from_args(args: Dict[str, Any]) -> str:
    selector = args.get("selector")
    if isinstance(selector, str) and selector.strip():
        return selector.strip()[:240]
    text = args.get("text")
    if isinstance(text, str) and text.strip():
        return f"text:{text.strip()[:220]}"
    return "__unknown__"


def _ops_alerting_enabled() -> bool:
    return os.getenv("ENABLE_DESKTOP_OPS_ALERTS", "true").lower() in {"1", "true", "yes"}


def _ops_persistence_enabled() -> bool:
    return os.getenv("ENABLE_DESKTOP_OPS_PERSISTENCE", "true").lower() in {"1", "true", "yes"}


def _ops_alert_thresholds() -> Dict[str, Any]:
    return {
        "ws_reconnect_rate_max": float(os.getenv("DESKTOP_OPS_ALERT_WS_RECONNECT_RATE_MAX", "0.25")),
        "mttr_seconds_max": float(os.getenv("DESKTOP_OPS_ALERT_MTTR_SECONDS_MAX", "120")),
        "selector_timeouts_max": int(os.getenv("DESKTOP_OPS_ALERT_SELECTOR_TIMEOUTS_MAX", "25")),
        "action_success_rate_min": float(os.getenv("DESKTOP_OPS_ALERT_ACTION_SUCCESS_RATE_MIN", "0.85")),
        "actions": [
            x.strip().lower()
            for x in str(os.getenv("DESKTOP_OPS_ALERT_ACTIONS", "browser_click,browser_fill,click,type_text")).split(",")
            if x.strip()
        ],
    }


def _build_ops_breaches(snapshot: Dict[str, Any]) -> list[Dict[str, Any]]:
    thresholds = _ops_alert_thresholds()
    breaches: list[Dict[str, Any]] = []

    ws = snapshot.get("ws") if isinstance(snapshot.get("ws"), dict) else {}
    reconnect_rate = float(ws.get("reconnect_rate") or 0.0)
    if reconnect_rate > float(thresholds["ws_reconnect_rate_max"]):
        breaches.append(
            {
                "key": "ws.reconnect_rate",
                "severity": "high",
                "message": (
                    f"WS reconnect_rate={reconnect_rate:.4f} exceeds max "
                    f"{float(thresholds['ws_reconnect_rate_max']):.4f}"
                ),
                "current_value": reconnect_rate,
                "threshold": float(thresholds["ws_reconnect_rate_max"]),
            }
        )

    mttr_seconds = float(snapshot.get("mttr_seconds") or 0.0)
    if mttr_seconds > float(thresholds["mttr_seconds_max"]):
        breaches.append(
            {
                "key": "ops.mttr_seconds",
                "severity": "medium",
                "message": (
                    f"MTTR={mttr_seconds:.3f}s exceeds max "
                    f"{float(thresholds['mttr_seconds_max']):.3f}s"
                ),
                "current_value": mttr_seconds,
                "threshold": float(thresholds["mttr_seconds_max"]),
            }
        )

    selector_timeouts = snapshot.get("selector_timeouts") if isinstance(snapshot.get("selector_timeouts"), dict) else {}
    timeout_events = int(selector_timeouts.get("total_timeout_events") or 0)
    if timeout_events > int(thresholds["selector_timeouts_max"]):
        breaches.append(
            {
                "key": "ops.selector_timeouts.total",
                "severity": "medium",
                "message": (
                    f"selector timeout events={timeout_events} exceeds max "
                    f"{int(thresholds['selector_timeouts_max'])}"
                ),
                "current_value": timeout_events,
                "threshold": int(thresholds["selector_timeouts_max"]),
            }
        )

    by_action = (
        snapshot.get("command_success_rate_by_action")
        if isinstance(snapshot.get("command_success_rate_by_action"), dict)
        else {}
    )
    for action in thresholds["actions"]:
        stats = by_action.get(action)
        if not isinstance(stats, dict):
            continue
        total = int(stats.get("total") or 0)
        if total <= 0:
            continue
        rate = float(stats.get("success_rate") or 0.0)
        if rate < float(thresholds["action_success_rate_min"]):
            breaches.append(
                {
                    "key": f"action_success_rate.{action}",
                    "severity": "high",
                    "message": (
                        f"{action} success_rate={rate:.4f} below min "
                        f"{float(thresholds['action_success_rate_min']):.4f}"
                    ),
                    "current_value": rate,
                    "threshold": float(thresholds["action_success_rate_min"]),
                }
            )
    return breaches


async def _persist_ops_snapshot_if_due(
    session: AsyncSession,
    *,
    organization_id: UUID,
    snapshot: Dict[str, Any],
) -> bool:
    if not _ops_persistence_enabled():
        return False
    if not _ops_control_plane.should_persist(organization_id=organization_id):
        return False
    compact = {
        "window_minutes": int(snapshot.get("window_minutes") or 0),
        "as_of": snapshot.get("as_of"),
        "ws": snapshot.get("ws", {}),
        "mttr_seconds": float(snapshot.get("mttr_seconds") or 0.0),
        "mttr_sample_count": int(snapshot.get("mttr_sample_count") or 0),
        "sampled_results": int(snapshot.get("sampled_results") or 0),
        "selector_timeouts": snapshot.get("selector_timeouts", {}),
        "command_success_rate_by_action": snapshot.get("command_success_rate_by_action", {}),
        "execution_guard": snapshot.get("execution_guard", {}),
    }
    await write_audit_log(
        session,
        organization_id=organization_id,
        actor_user_id=None,
        action="desktop_agent.ops.health_snapshot",
        resource="desktop_ops",
        resource_id=None,
        status="ok",
        metadata=compact,
    )
    return True


async def _emit_ops_alerts_if_needed(
    session: AsyncSession,
    *,
    organization_id: UUID,
    snapshot: Dict[str, Any],
) -> list[Dict[str, Any]]:
    if not _ops_alerting_enabled():
        return []
    breaches = _build_ops_breaches(snapshot)
    emitted: list[Dict[str, Any]] = []
    for breach in breaches:
        alert_key = f"{organization_id}:{str(breach.get('key') or '')}"
        if not _ops_control_plane.should_emit_alert(alert_key=alert_key):
            continue
        emitted.append(breach)
        await write_audit_log(
            session,
            organization_id=organization_id,
            actor_user_id=None,
            action="desktop_agent.ops.alert",
            resource="desktop_ops",
            resource_id=None,
            status="warning",
            detail=str(breach.get("message") or ""),
            metadata={**breach, "source": "desktop_ops_health"},
        )
        await send_to_n8n_with_response(
            {
                "source": "desktop_ops_health",
                "organization_id": str(organization_id),
                "severity": str(breach.get("severity") or "medium"),
                "title": f"Desktop Ops Alert: {breach.get('key')}",
                "description": str(breach.get("message") or ""),
                "metric_key": str(breach.get("key") or ""),
                "current_value": breach.get("current_value"),
                "threshold": breach.get("threshold"),
                "channels": ["n8n"],
            }
        )
    return emitted


async def _sync_ops_incidents(
    session: AsyncSession,
    *,
    organization_id: UUID,
    breaches: list[Dict[str, Any]],
) -> Dict[str, int]:
    """Mirror desktop ops breaches into alert_incidents for formal SLA tracking.

    Fails closed: any DB/schema mismatch must not break autonomy runtime.
    """
    try:
        now = datetime.now(timezone.utc)
        current_by_key = {
            f"desktop_ops:{str(item.get('key') or '').strip()}": item
            for item in breaches
            if str(item.get("key") or "").strip()
        }
        open_rows = list(
            (
                await session.execute(
                    sql_text(
                        """
                        SELECT id, incident_key, "metadata"
                        FROM alert_incidents
                        WHERE organization_id = :org_id
                          AND status = 'open'
                          AND incident_key LIKE 'desktop_ops:%'
                        """
                    ),
                    {"org_id": organization_id},
                )
            ).all()
        )

        open_by_key: Dict[str, Dict[str, Any]] = {}
        for row in open_rows:
            key = str(getattr(row, "incident_key", "") or "")
            open_by_key[key] = {
                "id": getattr(row, "id", None),
                "metadata": dict(getattr(row, "metadata", {}) or {}),
            }

        created = 0
        updated = 0
        resolved = 0

        # Resolve incidents no longer breaching.
        for incident_key, item in open_by_key.items():
            if incident_key in current_by_key:
                continue
            meta = dict(item.get("metadata") or {})
            meta["resolved_reason"] = "threshold_recovered"
            await session.execute(
                sql_text(
                    """
                    UPDATE alert_incidents
                    SET status = 'resolved',
                        resolved_at = :now_ts,
                        last_seen_at = :now_ts,
                        "metadata" = CAST(:metadata_json AS JSONB)
                    WHERE id = :incident_id
                    """
                ),
                {
                    "now_ts": now,
                    "incident_id": item.get("id"),
                    "metadata_json": json.dumps(meta, ensure_ascii=True),
                },
            )
            resolved += 1

        # Upsert active breaches as open incidents.
        for incident_key, breach in current_by_key.items():
            title = f"Desktop Ops breach: {breach.get('key')}"
            description = str(breach.get("message") or "Desktop ops threshold breached")
            severity = str(breach.get("severity") or "warning")
            meta = {
                "source": "desktop_ops_health",
                "metric_key": str(breach.get("key") or ""),
                "current_value": breach.get("current_value"),
                "threshold": breach.get("threshold"),
            }
            existing = open_by_key.get(incident_key)
            if existing:
                merged_meta = {**dict(existing.get("metadata") or {}), **meta}
                await session.execute(
                    sql_text(
                        """
                        UPDATE alert_incidents
                        SET title = :title,
                            description = :description,
                            severity = :severity,
                            last_seen_at = :now_ts,
                            "metadata" = CAST(:metadata_json AS JSONB)
                        WHERE id = :incident_id
                        """
                    ),
                    {
                        "title": title,
                        "description": description,
                        "severity": severity,
                        "now_ts": now,
                        "incident_id": existing.get("id"),
                        "metadata_json": json.dumps(merged_meta, ensure_ascii=True),
                    },
                )
                updated += 1
                continue

            await session.execute(
                sql_text(
                    """
                    INSERT INTO alert_incidents (
                        id, organization_id, rule_id, incident_key, title, description, severity,
                        status, first_seen_at, last_seen_at, resolved_at, "metadata", created_at, updated_at
                    ) VALUES (
                        :incident_id, :org_id, NULL, :incident_key, :title, :description, :severity,
                        'open', :now_ts, :now_ts, NULL, CAST(:metadata_json AS JSONB), :now_ts, :now_ts
                    )
                    """
                ),
                {
                    "org_id": organization_id,
                    "incident_id": uuid4(),
                    "incident_key": incident_key,
                    "title": title,
                    "description": description,
                    "severity": severity,
                    "now_ts": now,
                    "metadata_json": json.dumps(meta, ensure_ascii=True),
                },
            )
            created += 1

        await session.commit()
        return {"created": created, "updated": updated, "resolved": resolved}
    except Exception:
        # Ops incident sync is best-effort; never break request/runtime flow.
        return {"created": 0, "updated": 0, "resolved": 0}


async def _postprocess_ops_snapshot(
    session: AsyncSession,
    *,
    organization_id: UUID,
    snapshot: Dict[str, Any],
) -> Dict[str, Any]:
    persisted = await _persist_ops_snapshot_if_due(
        session,
        organization_id=organization_id,
        snapshot=snapshot,
    )
    alerts = await _emit_ops_alerts_if_needed(
        session,
        organization_id=organization_id,
        snapshot=snapshot,
    )
    incident_sync = await _sync_ops_incidents(
        session,
        organization_id=organization_id,
        breaches=_build_ops_breaches(snapshot),
    )
    enriched = dict(snapshot)
    enriched["ops_pipeline"] = {
        "persisted": bool(persisted),
        "alerts_emitted": len(alerts),
        "incidents_created": int(incident_sync.get("created") or 0),
        "incidents_updated": int(incident_sync.get("updated") or 0),
        "incidents_resolved": int(incident_sync.get("resolved") or 0),
    }
    if alerts:
        enriched["alerts"] = alerts
    return enriched


async def _build_ops_health_snapshot(
    session: AsyncSession,
    *,
    organization_id: UUID,
    window_minutes: int,
) -> Dict[str, Any]:
    effective_window = max(1, int(window_minutes))
    now = datetime.now(timezone.utc)
    since = now - timedelta(minutes=effective_window)
    row_limit = max(50, min(5000, int(os.getenv("DESKTOP_OPS_ROWS_LIMIT", "2000"))))

    stmt = (
        select(
            DesktopCommand.action,
            DesktopCommand.args,
            DesktopCommand.issued_at,
            DesktopCommandResult.status,
            DesktopCommandResult.error,
            DesktopCommandResult.received_at,
        )
        .join(DesktopCommand, DesktopCommand.id == DesktopCommandResult.command_id)
        .where(
            DesktopCommandResult.organization_id == organization_id,
            DesktopCommandResult.received_at >= since,
        )
        .order_by(DesktopCommandResult.received_at.asc())
        .limit(row_limit)
    )
    rows = list((await session.execute(stmt)).all())

    by_action: Dict[str, Dict[str, Any]] = {}
    timeout_by_selector: Dict[str, int] = {}
    pending_failure_at_by_action: Dict[str, datetime] = {}
    mttr_samples_seconds: list[float] = []

    for row in rows:
        action = str(row.action or "unknown").strip().lower() or "unknown"
        args = dict(row.args or {})
        status = str(row.status or "").strip().lower()
        error = row.error
        received_at = row.received_at

        stats = by_action.setdefault(action, {"total": 0, "success": 0, "failed": 0, "success_rate": 0.0})
        stats["total"] += 1
        if status == "success":
            stats["success"] += 1
        else:
            stats["failed"] += 1

        if _is_timeout_error(error):
            selector_key = _selector_from_args(args)
            timeout_by_selector[selector_key] = int(timeout_by_selector.get(selector_key) or 0) + 1

        if status != "success":
            if action not in pending_failure_at_by_action:
                pending_failure_at_by_action[action] = received_at
        else:
            failed_at = pending_failure_at_by_action.pop(action, None)
            if failed_at is not None:
                delta = (received_at - failed_at).total_seconds()
                if delta >= 0:
                    mttr_samples_seconds.append(float(delta))

    for action, stats in by_action.items():
        total = int(stats["total"])
        success = int(stats["success"])
        stats["success_rate"] = round((success / total), 4) if total > 0 else 0.0
        by_action[action] = stats

    mttr_seconds = round((sum(mttr_samples_seconds) / len(mttr_samples_seconds)), 3) if mttr_samples_seconds else 0.0
    top_timeout_selectors = [
        {"selector": key, "timeouts": count}
        for key, count in sorted(timeout_by_selector.items(), key=lambda kv: kv[1], reverse=True)[:25]
    ]

    guard_stmt = (
        select(
            AuditLog.action,
            AuditLog.status,
            AuditLog.created_at,
            AuditLog.audit_metadata,
        )
        .where(
            AuditLog.organization_id == organization_id,
            AuditLog.created_at >= since,
            AuditLog.action.in_(["execution_guard.preflight", "execution_guard.paused", "execution_guard.tripwire"]),
        )
        .order_by(desc(AuditLog.created_at))
        .limit(200)
    )
    guard_rows = list((await session.execute(guard_stmt)).all())
    guard_entries = [
        {
            "action": row.action,
            "status": row.status,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "metadata": dict(row.audit_metadata or {}),
        }
        for row in guard_rows
    ]
    execution_guard = summarize_guard_audit_entries(guard_entries)

    return {
        "window_minutes": effective_window,
        "as_of": now.isoformat(),
        "ws": {
            **_ops_telemetry.ws_reconnect_metrics(window_minutes=effective_window),
            "connected_agents": len(_ws_manager._connections),
        },
        "command_success_rate_by_action": by_action,
        "selector_timeouts": {
            "total_timeout_events": int(sum(timeout_by_selector.values())),
            "by_selector": top_timeout_selectors,
        },
        "mttr_seconds": mttr_seconds,
        "mttr_sample_count": len(mttr_samples_seconds),
        "sampled_results": len(rows),
        "execution_guard": execution_guard,
    }


def _to_result_read(row: DesktopCommandResult) -> DesktopCommandResultRead:
    return DesktopCommandResultRead(
        id=str(row.id),
        command_id=str(row.command_id),
        status="success" if row.status == "success" else "failed",
        duration_ms=row.duration_ms,
        error=row.error,
        data=row.data or {},
        received_at=row.received_at,
    )


def _to_command_read(
    row: DesktopCommand,
    *,
    result: Optional[DesktopCommandResult] = None,
) -> DesktopCommandRead:
    return DesktopCommandRead(
        id=str(row.id),
        organization_id=str(row.organization_id),
        agent_id=str(row.agent_id),
        action=row.action,  # type: ignore[arg-type]
        args=row.args or {},
        status=row.status,  # type: ignore[arg-type]
        timeout_ms=row.timeout_ms,
        requested_by_user_id=(str(row.requested_by_user_id) if row.requested_by_user_id else None),
        approved_by_user_id=(str(row.approved_by_user_id) if row.approved_by_user_id else None),
        approved_at=row.approved_at,
        issued_at=row.issued_at,
        error=row.error,
        metadata=row.command_metadata or {},
        created_at=row.created_at,
        updated_at=row.updated_at,
        result=_to_result_read(result) if result else None,
    )


def _to_agent_read(row: DesktopAgent) -> DesktopAgentRead:
    status = "online" if row.status == "online" else "offline"
    env = str(getattr(row, "environment", "production") or "production").strip().lower()
    if env not in {"sandbox", "production"}:
        env = "production"
    return DesktopAgentRead(
        id=str(row.id),
        organization_id=str(row.organization_id),
        agent_name=row.agent_name,
        environment=env,  # type: ignore[arg-type]
        status=status,  # type: ignore[arg-type]
        capabilities=row.capabilities or {},
        last_heartbeat_at=row.last_heartbeat_at,
        connected_at=row.connected_at,
        disconnected_at=row.disconnected_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


async def _get_agent(
    session: AsyncSession,
    *,
    organization_id: UUID,
    agent_id: UUID,
) -> Optional[DesktopAgent]:
    stmt = select(DesktopAgent).where(
        DesktopAgent.organization_id == organization_id,
        DesktopAgent.id == agent_id,
    )
    return (await session.execute(stmt)).scalars().first()


async def _get_command(
    session: AsyncSession,
    *,
    organization_id: UUID,
    command_id: UUID,
) -> Optional[DesktopCommand]:
    stmt = select(DesktopCommand).where(
        DesktopCommand.organization_id == organization_id,
        DesktopCommand.id == command_id,
    )
    return (await session.execute(stmt)).scalars().first()


async def _get_command_result(
    session: AsyncSession,
    *,
    command_id: UUID,
) -> Optional[DesktopCommandResult]:
    stmt = select(DesktopCommandResult).where(DesktopCommandResult.command_id == command_id)
    return (await session.execute(stmt)).scalars().first()


async def _upsert_agent(
    session: AsyncSession,
    *,
    organization_id: UUID,
    agent_name: str,
    environment: str = "production",
    capabilities: Optional[Dict[str, Any]] = None,
) -> DesktopAgent:
    stmt = select(DesktopAgent).where(
        DesktopAgent.organization_id == organization_id,
        DesktopAgent.agent_name == agent_name,
    )
    row = (await session.execute(stmt)).scalars().first()
    now = datetime.now(timezone.utc)

    if row:
        row.status = "online"
        row.last_heartbeat_at = now
        row.connected_at = now
        row.disconnected_at = None
        env = str(environment or "production").strip().lower()
        if env in {"sandbox", "production"}:
            row.environment = env
        if capabilities:
            row.capabilities = capabilities
        await session.commit()
        await session.refresh(row)
        return row

    row = DesktopAgent(
        organization_id=organization_id,
        agent_name=agent_name,
        environment=(
            str(environment or "production").strip().lower()
            if str(environment or "").strip().lower() in {"sandbox", "production"}
            else "production"
        ),
        status="online",
        capabilities=capabilities or {},
        last_heartbeat_at=now,
        connected_at=now,
        disconnected_at=None,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def _mark_agent_offline(session: AsyncSession, *, agent: DesktopAgent) -> None:
    agent.status = "offline"
    agent.disconnected_at = datetime.now(timezone.utc)
    await session.commit()


async def _mark_agent_heartbeat(session: AsyncSession, *, agent: DesktopAgent) -> None:
    now = datetime.now(timezone.utc)
    agent.status = "online"
    agent.last_heartbeat_at = now
    await session.commit()


def _build_ws_command_payload(command: DesktopCommand) -> Dict[str, Any]:
    if _runner_mode() == "remote":
        return {
            "type": "execute",
            "run_id": str(command.organization_id),
            "step_id": str(command.id),
            "command_id": str(command.id),
            "tool": ("browser" if str(command.action or "").startswith("browser_") else "desktop"),
            "action": command.action,
            "args": command.args or {},
            "timeout_ms": command.timeout_ms,
            "issued_at": datetime.now(timezone.utc).isoformat(),
        }

    payload = WsCommandEnvelope(
        command_id=str(command.id),
        action=command.action,  # type: ignore[arg-type]
        args=command.args or {},
        approved=True,
        issued_at=datetime.now(timezone.utc).isoformat(),
        timeout_ms=command.timeout_ms,
    )
    return payload.model_dump()


async def _dispatch_command_if_online(
    session: AsyncSession,
    *,
    command: DesktopCommand,
) -> bool:
    # Dispatch is intentionally idempotent: we only ever send "approved" commands once.
    if command.status != "approved":
        return False
    if not _ws_manager.is_connected(command.agent_id):
        return False
    if not _ws_manager.allow_dispatch(command.agent_id):
        command.error = "rate_limited"
        await session.commit()
        return False
    guardrail = plan_guarded_execution(
        {
            "source": "desktop_agent",
            "action_name": command.action,
            "action": {
                "action": command.action,
                "arguments": dict(command.args or {}),
            },
            "args": dict(command.args or {}),
        },
        channel="desktop",
    )
    if guardrail.get("should_pause"):
        command.status = "blocked"
        command.error = f"paused_by_guardrail:{guardrail.get('pause_reason') or 'unknown'}"
        meta = dict(command.command_metadata or {})
        meta["guardrail"] = guardrail
        command.command_metadata = meta
        await session.commit()
        await write_audit_log(
            session,
            organization_id=command.organization_id,
            actor_user_id=None,
            action="execution_guard.paused",
            resource="desktop_command",
            resource_id=str(command.id),
            status="warning",
            detail=str(guardrail.get("pause_reason") or "paused_by_guardrail"),
            metadata={
                "action_name": command.action,
                "channel": "desktop",
                "operation_type": guardrail.get("operation_type"),
                "context_signature": guardrail.get("context_signature"),
                "pause_reason": guardrail.get("pause_reason"),
                "dry_run_preview": guardrail.get("dry_run_preview"),
                "rollback_snapshot": guardrail.get("rollback_snapshot"),
                "reversible": guardrail.get("reversible"),
                "estimated_impact": guardrail.get("estimated_impact"),
            },
        )
        return False
    sent = await _ws_manager.send_json(command.agent_id, _build_ws_command_payload(command))
    if sent:
        command.status = "sent"
        command.issued_at = datetime.now(timezone.utc)
        meta = dict(command.command_metadata or {})
        if guardrail.get("dry_run_preview") or not guardrail.get("reversible", True):
            meta["guardrail"] = guardrail
            command.command_metadata = meta
            await write_audit_log(
                session,
                organization_id=command.organization_id,
                actor_user_id=None,
                action="execution_guard.preflight",
                resource="desktop_command",
                resource_id=str(command.id),
                status="ok",
                detail=None,
                metadata={
                    "action_name": command.action,
                    "channel": "desktop",
                    "operation_type": guardrail.get("operation_type"),
                    "context_signature": guardrail.get("context_signature"),
                    "dry_run_preview": guardrail.get("dry_run_preview"),
                    "rollback_snapshot": guardrail.get("rollback_snapshot"),
                    "reversible": guardrail.get("reversible"),
                    "estimated_impact": guardrail.get("estimated_impact"),
                },
            )
        await session.commit()
    return sent


async def _dispatch_approved_backlog(
    session: AsyncSession,
    *,
    organization_id: UUID,
    agent_id: UUID,
) -> None:
    stmt = (
        select(DesktopCommand)
        .where(
            DesktopCommand.organization_id == organization_id,
            DesktopCommand.agent_id == agent_id,
            DesktopCommand.status == "approved",
        )
        .order_by(DesktopCommand.created_at.asc())
        .limit(100)
    )
    rows = list((await session.execute(stmt)).scalars().all())
    for command in rows:
        await _dispatch_command_if_online(session, command=command)


async def _run_recovery_background(
    *,
    organization_id: UUID,
    command_id: UUID,
) -> None:
    retry = await trigger_recovery_for_failed_command(
        organization_id=str(organization_id),
        failed_command_id=str(command_id),
        dispatch_command=_dispatch_command_if_online,
    )
    if retry.success:
        logger.info(
            "Auto-recovery succeeded for command %s in %s attempts",
            command_id,
            retry.attempts_used,
        )
    else:
        logger.info(
            "Auto-recovery skipped/failed for command %s: %s",
            command_id,
            retry.final_reason,
        )


async def _persist_result(
    session: AsyncSession,
    *,
    organization_id: UUID,
    agent_id: UUID,
    payload: WsResultEnvelope,
) -> tuple[DesktopCommand, DesktopCommandResult]:
    command_id = _parse_uuid(payload.command_id, field="command_id")
    command = await _get_command(
        session,
        organization_id=organization_id,
        command_id=command_id,
    )
    if not command:
        raise HTTPException(status_code=404, detail="Command not found")
    if command.agent_id != agent_id:
        raise HTTPException(status_code=403, detail="Command-agent mismatch")

    existing = await _get_command_result(session, command_id=command_id)
    if existing:
        existing.status = payload.status
        existing.duration_ms = payload.duration_ms
        existing.error = payload.error
        existing.data = payload.data
        existing.received_at = datetime.now(timezone.utc)
        result_row = existing
    else:
        result_row = DesktopCommandResult(
            organization_id=organization_id,
            agent_id=agent_id,
            command_id=command_id,
            status=payload.status,
            duration_ms=payload.duration_ms,
            error=payload.error,
            data=payload.data,
            received_at=datetime.now(timezone.utc),
        )
        session.add(result_row)

    command.status = "completed" if payload.status == "success" else "failed"
    command.error = payload.error
    await session.commit()
    await session.refresh(result_row)
    await session.refresh(command)
    return command, result_row


def _load_runner_tokens() -> dict[str, str]:
    raw = os.getenv("RUNNER_TOKENS_JSON", "").strip()
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return {str(k): str(v) for k, v in parsed.items() if str(k).strip() and str(v).strip()}
        except json.JSONDecodeError:
            pass
    single_client_id = (
        str(os.getenv("CLIENT_ID", "") or "").strip()
        or str(os.getenv("JARVIS_CLIENT_ID", "") or "").strip()
        or str(os.getenv("TELEGRAM_DEFAULT_CLIENT_ID", "") or "").strip()
    )
    single_token = str(os.getenv("RUNNER_TOKEN", "") or "").strip()
    if single_client_id and single_token:
        return {single_client_id: single_token}
    return {}


def _token_matches(expected: str, provided: str) -> bool:
    expected_norm = str(expected or "").strip()
    provided_norm = str(provided or "").strip()
    if not expected_norm or not provided_norm:
        return False
    if hmac.compare_digest(expected_norm, provided_norm):
        return True
    expected_hash = hashlib.sha256(expected_norm.encode()).hexdigest()
    provided_hash = hashlib.sha256(provided_norm.encode()).hexdigest()
    return hmac.compare_digest(expected_hash, provided_norm) or hmac.compare_digest(expected_norm, provided_hash)


def _validate_runner_token(client_id: str, token: str) -> bool:
    configured = _load_runner_tokens().get(client_id)
    if not configured:
        return False
    return _token_matches(configured, token)


async def _resolve_ws_auth(websocket: WebSocket) -> tuple[UUID, str, str, str]:
    x_client_id = websocket.headers.get("x-client-id")
    x_client_token = websocket.headers.get("x-client-token")
    x_runner_token = websocket.headers.get("x-runner-token")
    agent_name = websocket.headers.get("x-agent-name") or websocket.query_params.get("agent_name")
    agent_env = websocket.headers.get("x-agent-environment") or websocket.query_params.get("environment")

    if x_client_id and x_runner_token and _validate_runner_token(x_client_id, x_runner_token):
        org_uuid = _parse_uuid(x_client_id, field="client_id")
        resolved_name = (agent_name or f"runner-{x_client_id[:8]}").strip()[:160]
        if not resolved_name:
            resolved_name = f"runner-{x_client_id[:8]}"
        env = (agent_env or "production").strip().lower()
        if env not in {"sandbox", "production"}:
            env = "production"
        return org_uuid, x_client_id, resolved_name, env

    if not x_client_id or not x_client_token:
        raise HTTPException(status_code=401, detail="Missing auth headers")

    async with AsyncSessionLocal() as session:
        client_id = await validate_client_credentials(
            session,
            x_client_id=x_client_id,
            x_client_token=x_client_token,
        )

    org_uuid = _parse_uuid(client_id, field="client_id")
    resolved_name = (agent_name or f"desktop-{client_id[:8]}").strip()[:160]
    if not resolved_name:
        resolved_name = f"desktop-{client_id[:8]}"
    env = (agent_env or "production").strip().lower()
    if env not in {"sandbox", "production"}:
        env = "production"
    return org_uuid, client_id, resolved_name, env


def _normalize_ws_result_payload(data: Dict[str, Any]) -> WsResultEnvelope:
    command_id = str(data.get("command_id") or data.get("step_id") or "").strip()
    if not command_id:
        raise ValueError("missing command_id")
    status_raw = str(data.get("status") or "").strip().lower()
    status = "success" if status_raw in {"success", "completed", "ok"} else "failed"
    return WsResultEnvelope(
        command_id=command_id,
        status=status,  # type: ignore[arg-type]
        duration_ms=(int(data["duration_ms"]) if isinstance(data.get("duration_ms"), int) else None),
        error=(str(data.get("error")) if data.get("error") is not None else None),
        data=(data.get("data") if isinstance(data.get("data"), dict) else {}),
    )


async def _resolve_dashboard_ws_org(websocket: WebSocket) -> UUID:
    x_client_id = websocket.headers.get("x-client-id")
    x_client_token = websocket.headers.get("x-client-token")
    if not x_client_id or not x_client_token:
        raise HTTPException(status_code=401, detail="Missing client headers")
    async with AsyncSessionLocal() as session:
        client_id = await validate_client_credentials(
            session,
            x_client_id=x_client_id,
            x_client_token=x_client_token,
        )
    return _parse_uuid(client_id, field="client_id")


@router.post("/commands", response_model=DesktopCommandRead)
async def create_command(
    req: DesktopCommandCreateRequest,
    client_id: str = Depends(require_client_token),
    session: AsyncSession = Depends(get_session),
):
    if not _feature_enabled():
        raise HTTPException(status_code=404, detail="Feature ENABLE_DESKTOP_AGENT disabled")

    org_id = _parse_uuid(client_id, field="client_id")
    agent_id = _parse_uuid(req.agent_id, field="agent_id")
    policy = None
    if _enterprise_policies_enabled():
        try:
            policy = await load_effective_policy(session, organization_id=org_id)
            policy_error = validate_desktop_action(action=req.action, args=req.args, policy=policy)
            if policy_error:
                raise HTTPException(status_code=403, detail=policy_error)
        except HTTPException:
            raise
        except Exception:
            # Policy evaluation must never break baseline behavior.
            pass
    _validate_command_args(req.action, req.args)
    command_metadata = dict(req.metadata or {})
    action_name = str(req.action or "").strip().lower()
    risk_level = _action_risk_level(action_name, dict(req.args or {}))
    approval_required = True
    if policy and getattr(policy, "enforced", False):
        required_by_rule = policy_requires_human_approval_for_action(
            action=action_name,
            args=dict(req.args or {}),
            policy=policy,
        )
        auto_approve = False
        if (
            policy.autonomy_execution_mode() in {"semi_auto", "full_auto"}
            and policy.auto_approve_enabled_for(action_name)
        ):
            max_risk = policy.max_auto_risk()
            auto_approve = _RISK_LEVEL_ORDER.get(risk_level, 3) <= _RISK_LEVEL_ORDER.get(max_risk, 1)
        approval_required = bool(required_by_rule or (not auto_approve))
    else:
        # Backward-compatible behavior when enterprise policies are disabled.
        approval_required = True

    approval = command_metadata.get("approval")
    approval_payload = dict(approval) if isinstance(approval, dict) else {}
    approval_payload["required"] = approval_required
    approval_payload["status"] = "pending" if approval_required else "approved"
    command_metadata["approval"] = approval_payload
    command_metadata["requires_human_approval"] = approval_required
    command_metadata["risk_level"] = risk_level
    command_metadata["auto_approval_applied"] = not approval_required

    agent = await _get_agent(session, organization_id=org_id, agent_id=agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    command = DesktopCommand(
        organization_id=org_id,
        agent_id=agent_id,
        action=req.action,
        args=req.args,
        status=("pending_approval" if approval_required else "approved"),
        timeout_ms=req.timeout_ms,
        requested_by_user_id=_parse_optional_uuid(req.requested_by_user_id, field="requested_by_user_id"),
        approved_by_user_id=(
            _parse_optional_uuid(req.requested_by_user_id, field="requested_by_user_id")
            if not approval_required
            else None
        ),
        approved_at=(datetime.now(timezone.utc) if not approval_required else None),
        command_metadata=command_metadata,
    )
    session.add(command)
    await session.commit()
    await session.refresh(command)

    await write_audit_log(
        session,
        organization_id=org_id,
        actor_user_id=_parse_optional_uuid(req.requested_by_user_id, field="requested_by_user_id"),
        action="desktop_agent.command.create",
        resource="desktop_command",
        resource_id=str(command.id),
        status="ok",
        metadata={"action": req.action, "agent_id": req.agent_id},
    )
    return _to_command_read(command)


@router.post("/commands/{command_id}/approve", response_model=DesktopCommandRead)
async def approve_command(
    command_id: str,
    req: DesktopCommandApproveRequest,
    client_id: str = Depends(require_client_token),
    session: AsyncSession = Depends(get_session),
):
    if not _feature_enabled():
        raise HTTPException(status_code=404, detail="Feature ENABLE_DESKTOP_AGENT disabled")

    org_id = _parse_uuid(client_id, field="client_id")
    command_uuid = _parse_uuid(command_id, field="command_id")

    command = await _get_command(session, organization_id=org_id, command_id=command_uuid)
    if not command:
        raise HTTPException(status_code=404, detail="Command not found")
    if command.status != "pending_approval":
        raise HTTPException(status_code=409, detail="Command is not pending approval")

    command.status = "approved"
    command.approved_by_user_id = _parse_optional_uuid(req.approver_user_id, field="approver_user_id")
    command.approved_at = datetime.now(timezone.utc)
    command.error = None
    await session.commit()
    await session.refresh(command)

    await _dispatch_command_if_online(session, command=command)

    await write_audit_log(
        session,
        organization_id=org_id,
        actor_user_id=_parse_optional_uuid(req.approver_user_id, field="approver_user_id"),
        action="desktop_agent.command.approve",
        resource="desktop_command",
        resource_id=str(command.id),
        status="ok",
        detail=req.note,
        metadata={"agent_id": str(command.agent_id), "action": command.action},
    )
    result = await _get_command_result(session, command_id=command.id)
    return _to_command_read(command, result=result)


@router.get("/commands/{command_id}", response_model=DesktopCommandRead)
async def get_command(
    command_id: str,
    client_id: str = Depends(require_client_token),
    session: AsyncSession = Depends(get_session),
):
    if not _feature_enabled():
        raise HTTPException(status_code=404, detail="Feature ENABLE_DESKTOP_AGENT disabled")

    org_id = _parse_uuid(client_id, field="client_id")
    command_uuid = _parse_uuid(command_id, field="command_id")

    command = await _get_command(session, organization_id=org_id, command_id=command_uuid)
    if not command:
        raise HTTPException(status_code=404, detail="Command not found")
    result = await _get_command_result(session, command_id=command.id)
    return _to_command_read(command, result=result)


@router.get("/agents", response_model=DesktopAgentsResponse)
async def list_agents(
    limit: int = Query(default=100, ge=1, le=500),
    client_id: str = Depends(require_client_token),
    session: AsyncSession = Depends(get_session),
):
    if not _feature_enabled():
        raise HTTPException(status_code=404, detail="Feature ENABLE_DESKTOP_AGENT disabled")

    org_id = _parse_uuid(client_id, field="client_id")
    stmt = (
        select(DesktopAgent)
        .where(DesktopAgent.organization_id == org_id)
        .order_by(desc(DesktopAgent.updated_at))
        .limit(limit)
    )
    rows = list((await session.execute(stmt)).scalars().all())
    return DesktopAgentsResponse(items=[_to_agent_read(item) for item in rows])


@router.get("/ops/health")
async def desktop_ops_health(
    window_minutes: int = Query(default=60, ge=1, le=1440),
    client_id: str = Depends(require_client_token),
    session: AsyncSession = Depends(get_session),
):
    if not _feature_enabled():
        raise HTTPException(status_code=404, detail="Feature ENABLE_DESKTOP_AGENT disabled")
    org_id = _parse_uuid(client_id, field="client_id")
    snapshot = await _build_ops_health_snapshot(
        session,
        organization_id=org_id,
        window_minutes=window_minutes,
    )
    return await _postprocess_ops_snapshot(
        session,
        organization_id=org_id,
        snapshot=snapshot,
    )


@router.get("/ops/health/history")
async def desktop_ops_health_history(
    limit: int = Query(default=120, ge=1, le=1000),
    client_id: str = Depends(require_client_token),
    session: AsyncSession = Depends(get_session),
):
    if not _feature_enabled():
        raise HTTPException(status_code=404, detail="Feature ENABLE_DESKTOP_AGENT disabled")
    org_id = _parse_uuid(client_id, field="client_id")
    stmt = (
        select(AuditLog)
        .where(
            AuditLog.organization_id == org_id,
            AuditLog.action == "desktop_agent.ops.health_snapshot",
        )
        .order_by(desc(AuditLog.created_at))
        .limit(limit)
    )
    rows = list((await session.execute(stmt)).scalars().all())
    items = [
        {
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "status": row.status,
            "metrics": dict(row.audit_metadata or {}),
        }
        for row in rows
    ]
    return {"items": items, "count": len(items)}


@router.get("/ops/scorecard")
async def desktop_ops_scorecard(
    window_minutes: int = Query(default=60, ge=1, le=1440),
    history_limit: int = Query(default=120, ge=10, le=1000),
    client_id: str = Depends(require_client_token),
    session: AsyncSession = Depends(get_session),
):
    if not _feature_enabled():
        raise HTTPException(status_code=404, detail="Feature ENABLE_DESKTOP_AGENT disabled")
    org_id = _parse_uuid(client_id, field="client_id")
    now = datetime.now(timezone.utc)
    since = now - timedelta(minutes=max(1, int(window_minutes)))

    snapshot_stmt = (
        select(AuditLog)
        .where(
            AuditLog.organization_id == org_id,
            AuditLog.action == "desktop_agent.ops.health_snapshot",
        )
        .order_by(desc(AuditLog.created_at))
        .limit(history_limit)
    )
    alert_stmt = (
        select(AuditLog)
        .where(
            AuditLog.organization_id == org_id,
            AuditLog.action == "desktop_agent.ops.alert",
            AuditLog.created_at >= since,
        )
        .order_by(desc(AuditLog.created_at))
        .limit(history_limit)
    )
    snapshots = list((await session.execute(snapshot_stmt)).scalars().all())
    alerts = list((await session.execute(alert_stmt)).scalars().all())

    points = []
    reconnect_values: list[float] = []
    mttr_values: list[float] = []
    for row in reversed(snapshots):
        payload = dict(row.audit_metadata or {})
        ws = payload.get("ws") if isinstance(payload.get("ws"), dict) else {}
        reconnect_rate = float(ws.get("reconnect_rate") or 0.0)
        mttr = float(payload.get("mttr_seconds") or 0.0)
        reconnect_values.append(reconnect_rate)
        mttr_values.append(mttr)
        points.append(
            {
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "ws_reconnect_rate": reconnect_rate,
                "mttr_seconds": mttr,
                "timeout_events": int((payload.get("selector_timeouts") or {}).get("total_timeout_events") or 0),
                "sampled_results": int(payload.get("sampled_results") or 0),
            }
        )

    action_success_aggregate: Dict[str, Dict[str, float]] = {}
    for row in snapshots:
        payload = dict(row.audit_metadata or {})
        by_action = payload.get("command_success_rate_by_action")
        if not isinstance(by_action, dict):
            continue
        for action, stats in by_action.items():
            if not isinstance(stats, dict):
                continue
            current = action_success_aggregate.setdefault(action, {"total": 0.0, "success": 0.0})
            current["total"] += float(stats.get("total") or 0.0)
            current["success"] += float(stats.get("success") or 0.0)

    action_success_rate: Dict[str, float] = {}
    for action, agg in action_success_aggregate.items():
        total = float(agg.get("total") or 0.0)
        success = float(agg.get("success") or 0.0)
        action_success_rate[action] = round((success / total), 4) if total > 0 else 0.0

    recent_alerts = [
        {
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "status": row.status,
            "detail": row.detail,
            "metadata": dict(row.audit_metadata or {}),
        }
        for row in alerts[:50]
    ]

    return {
        "window_minutes": int(window_minutes),
        "as_of": now.isoformat(),
        "summary": {
            "snapshots": len(snapshots),
            "alerts_in_window": len(alerts),
            "avg_ws_reconnect_rate": round((sum(reconnect_values) / len(reconnect_values)), 4) if reconnect_values else 0.0,
            "avg_mttr_seconds": round((sum(mttr_values) / len(mttr_values)), 3) if mttr_values else 0.0,
        },
        "action_success_rate_aggregate": action_success_rate,
        "trend_points": points[-60:],
        "recent_alerts": recent_alerts,
    }


@router.websocket("/ops/health/ws")
async def desktop_ops_health_ws(websocket: WebSocket):
    if not _feature_enabled():
        await websocket.close(code=4404, reason="Feature disabled")
        return
    try:
        organization_id = await _resolve_dashboard_ws_org(websocket)
    except HTTPException:
        await websocket.close(code=4401, reason="Unauthorized")
        return

    await websocket.accept()
    interval_seconds = max(1.0, float(os.getenv("DESKTOP_OPS_HEALTH_WS_INTERVAL_SECONDS", "2.0")))
    window_minutes = max(1, int(os.getenv("DESKTOP_OPS_DEFAULT_WINDOW_MINUTES", "60")))
    try:
        while True:
            async with AsyncSessionLocal() as session:
                payload = await _build_ops_health_snapshot(
                    session,
                    organization_id=organization_id,
                    window_minutes=window_minutes,
                )
                payload = await _postprocess_ops_snapshot(
                    session,
                    organization_id=organization_id,
                    snapshot=payload,
                )
            await websocket.send_json({"type": "ops_health", "payload": payload})
            await asyncio.sleep(interval_seconds)
    except WebSocketDisconnect:
        return
    except Exception:
        try:
            await websocket.close(code=1011)
        except Exception:
            return


@router.websocket("/ws")
async def desktop_agent_ws(websocket: WebSocket):
    if not _feature_enabled():
        await websocket.close(code=4404, reason="Feature disabled")
        return

    try:
        organization_id, client_id, agent_name, agent_env = await _resolve_ws_auth(websocket)
    except HTTPException:
        await websocket.close(code=4401, reason="Unauthorized")
        return

    async with AsyncSessionLocal() as session:
        agent = await _upsert_agent(
            session,
            organization_id=organization_id,
            agent_name=agent_name,
            environment=agent_env,
            capabilities={"os": "windows", "transport": "websocket"},
        )

    await websocket.accept()
    _ws_manager.register(agent.id, websocket)
    _ops_telemetry.on_ws_connected(agent_id=agent.id)

    async with AsyncSessionLocal() as session:
        await _dispatch_approved_backlog(
            session,
            organization_id=organization_id,
            agent_id=agent.id,
        )

    try:
        while True:
            data = await websocket.receive_json()
            msg_type = str(data.get("type") or "").strip().lower()

            if msg_type == "heartbeat":
                async with AsyncSessionLocal() as session:
                    refreshed_agent = await _get_agent(
                        session,
                        organization_id=organization_id,
                        agent_id=agent.id,
                    )
                    if refreshed_agent:
                        await _mark_agent_heartbeat(session, agent=refreshed_agent)
                        await _dispatch_approved_backlog(
                            session,
                            organization_id=organization_id,
                            agent_id=agent.id,
                        )
                ack = WsAckEnvelope(status="received", detail="heartbeat")
                await websocket.send_json(ack.model_dump())
                continue

            if msg_type == "result":
                try:
                    result_payload = _normalize_ws_result_payload(data)
                except Exception as exc:
                    ack = WsAckEnvelope(status="error", detail=f"invalid_result:{exc!s}")
                    await websocket.send_json(ack.model_dump())
                    continue

                try:
                    async with AsyncSessionLocal() as session:
                        command, result = await _persist_result(
                            session,
                            organization_id=organization_id,
                            agent_id=agent.id,
                            payload=result_payload,
                        )
                        await write_audit_log(
                            session,
                            organization_id=organization_id,
                            actor_user_id=None,
                            action="desktop_agent.command.execute",
                            resource="desktop_command",
                            resource_id=str(command.id),
                            status=("ok" if result.status == "success" else "failed"),
                            detail=result.error,
                            metadata={
                                "agent_id": str(agent.id),
                                "duration_ms": result.duration_ms,
                            },
                        )
                        try:
                            await maybe_append_step_for_result(
                                session,
                                organization_id=organization_id,
                                agent_id=agent.id,
                                command=command,
                                result=result,
                            )
                        except Exception:
                            pass
                        should_recover = _should_trigger_recovery(command, result)
                except HTTPException as exc:
                    ack = WsAckEnvelope(status="error", detail=exc.detail)
                    await websocket.send_json(ack.model_dump())
                    continue

                ack = WsAckEnvelope(
                    command_id=result_payload.command_id,
                    status="persisted",
                    detail=None,
                )
                await websocket.send_json(ack.model_dump())

                if should_recover:
                    asyncio.create_task(
                        _run_recovery_background(
                            organization_id=organization_id,
                            command_id=command.id,
                        )
                    )
                continue

            if msg_type == "human_event":
                try:
                    human_payload = WsHumanEventEnvelope.model_validate(data)
                except Exception as exc:
                    ack = WsAckEnvelope(status="error", detail=f"invalid_human_event:{exc!s}")
                    await websocket.send_json(ack.model_dump())
                    continue

                try:
                    async with AsyncSessionLocal() as session:
                        await _persist_human_event_step(
                            session,
                            organization_id=organization_id,
                            agent_id=agent.id,
                            payload=human_payload,
                        )
                except HTTPException as exc:
                    ack = WsAckEnvelope(status="error", detail=str(exc.detail))
                    await websocket.send_json(ack.model_dump())
                    continue
                except Exception as exc:
                    ack = WsAckEnvelope(status="error", detail=f"persist_human_event_failed:{exc!s}")
                    await websocket.send_json(ack.model_dump())
                    continue

                ack = WsAckEnvelope(status="persisted", detail="human_event")
                await websocket.send_json(ack.model_dump())
                continue

            if msg_type == "ack":
                command_id_raw = data.get("command_id")
                if isinstance(command_id_raw, str):
                    try:
                        command_uuid = _parse_uuid(command_id_raw, field="command_id")
                        async with AsyncSessionLocal() as session:
                            command = await _get_command(
                                session,
                                organization_id=organization_id,
                                command_id=command_uuid,
                            )
                            if command and command.status == "sent":
                                command.status = "received"
                                await session.commit()
                    except HTTPException:
                        pass
                ack = WsAckEnvelope(status="received", detail="ack")
                await websocket.send_json(ack.model_dump())
                continue

            if msg_type == "agent_hello":
                ack = WsAckEnvelope(status="received", detail=f"hello:{client_id}")
                await websocket.send_json(ack.model_dump())
                continue

            ack = WsAckEnvelope(status="error", detail="unknown_message_type")
            await websocket.send_json(ack.model_dump())

    except WebSocketDisconnect:
        pass
    finally:
        _ops_telemetry.on_ws_disconnected(agent_id=agent.id)
        await _ws_manager.unregister(agent.id)
        async with AsyncSessionLocal() as session:
            row = await _get_agent(
                session,
                organization_id=organization_id,
                agent_id=agent.id,
            )
            if row:
                await _mark_agent_offline(session, agent=row)
