import os
import json
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from urllib.parse import urlparse
from uuid import UUID

from sqlalchemy import desc, select

from brain.audit import write_audit_log
from brain.policy_engine import (
    load_effective_policy,
    policy_requires_human_approval_for_action,
    validate_desktop_action,
)
from database import AsyncSessionLocal
from models import DesktopAgent, DesktopCommand
from schemas.action_contract import ActionContract

_DESKTOP_ACTIONS = {
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

def _enterprise_policies_enabled() -> bool:
    return os.getenv("ENABLE_ENTERPRISE_POLICIES", "false").lower() in {"1", "true", "yes"}


def _runner_mode() -> str:
    token = str(os.getenv("RUNNER_MODE", "local") or "local").strip().lower()
    return "remote" if token == "remote" else "local"


def _desktop_requires_online_runner() -> bool:
    """When backend runs in cloud, all desktop actions must go through a connected runner."""
    if os.getenv("RAILWAY_ENVIRONMENT") or os.getenv("HEROKU_APP_NAME") or os.getenv("KUBERNETES_SERVICE_HOST"):
        return True
    return _runner_mode() == "remote"


def _parse_optional_uuid(value: Optional[str]) -> Optional[UUID]:
    if not value:
        return None
    try:
        return UUID(value)
    except ValueError:
        return None


def _coerce_timeout_ms(parameters: Dict[str, Any]) -> int:
    raw = parameters.get("timeout_ms", 10000)
    if not isinstance(raw, int):
        return 10000
    if raw < 500:
        return 500
    if raw > 120000:
        return 120000
    return raw


def _sanitize_parameters(parameters: Dict[str, Any]) -> Dict[str, Any]:
    cleaned = dict(parameters)
    cleaned.pop("agent_id", None)
    cleaned.pop("timeout_ms", None)
    return cleaned


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
    return any(host == domain or host.endswith(f".{domain}") for domain in allowlist)


def _is_browser_action(action: str) -> bool:
    return action.startswith("browser_")


def _auto_approval_enabled() -> bool:
    return os.getenv("ENABLE_DESKTOP_AUTO_APPROVAL", "false").lower() in {"1", "true", "yes"}


def _auto_approval_max_risk() -> str:
    token = os.getenv("DESKTOP_AUTO_APPROVAL_MAX_RISK", "low").strip().lower()
    if token not in _RISK_LEVEL_ORDER:
        return "low"
    return token


def _action_risk_level(action: str, parameters: Dict[str, Any]) -> str:
    if action in {"take_screenshot", "browser_screenshot", "browser_extract", "browser_goto"}:
        return "low"

    if action == "browser_click":
        has_x = "x" in parameters
        has_y = "y" in parameters
        if has_x or has_y:
            return "high"
        return "medium"

    if action in {
        "browser_launch",
        "browser_fill",
        "browser_press",
        "browser_close",
        "type_text",
        "press_key",
    }:
        return "medium"

    return "high"


def _is_auto_approvable(action: str, parameters: Dict[str, Any], metadata: Dict[str, Any]) -> bool:
    if not _auto_approval_enabled():
        return False
    if bool(metadata.get("force_human_approval")):
        return False
    risk = _action_risk_level(action, parameters)
    max_risk = _auto_approval_max_risk()
    return _RISK_LEVEL_ORDER[risk] <= _RISK_LEVEL_ORDER[max_risk]


def _validate_parameters(action: str, parameters: Dict[str, Any]) -> Optional[str]:
    if action == "mover_mouse":
        x = parameters.get("x")
        y = parameters.get("y")
        if not isinstance(x, int) or not isinstance(y, int):
            return "mover_mouse requires integer x,y"
        if x < 0 or y < 0:
            return "mover_mouse does not allow negative x,y"
        return None

    if action == "click":
        button = str(parameters.get("button", "left")).lower()
        if button not in {"left", "right", "middle"}:
            return "click.button must be left|right|middle"
        clicks = parameters.get("clicks", 1)
        if not isinstance(clicks, int) or clicks < 1 or clicks > 5:
            return "click.clicks must be int between 1 and 5"
        has_x = "x" in parameters
        has_y = "y" in parameters
        if has_x != has_y:
            return "click requires x and y together"
        if has_x and has_y:
            x = parameters.get("x")
            y = parameters.get("y")
            if not isinstance(x, int) or not isinstance(y, int):
                return "click.x and click.y must be integers"
            if x < 0 or y < 0:
                return "click.x and click.y do not allow negative values"
        return None

    if action == "type_text":
        text = parameters.get("text")
        if not isinstance(text, str) or not text.strip():
            return "type_text requires non-empty text"
        return None

    if action == "press_key":
        key = parameters.get("key")
        if not isinstance(key, str) or not key.strip():
            return "press_key requires non-empty key"
        return None

    if action == "open_program":
        name = parameters.get("name")
        if not isinstance(name, str) or not name.strip():
            return "open_program requires name"
        return None

    if action == "take_screenshot":
        max_width = parameters.get("max_width")
        if max_width is not None and (not isinstance(max_width, int) or max_width < 320):
            return "take_screenshot.max_width must be integer >= 320"
        return None

    if action == "browser_launch":
        headless = parameters.get("headless")
        if headless is not None and not isinstance(headless, bool):
            return "browser_launch.headless must be boolean"
        channel = parameters.get("channel")
        if channel is not None and str(channel).strip().lower() not in {"chrome", "chromium"}:
            return "browser_launch.channel must be chrome or chromium"
        start_url = parameters.get("start_url")
        if start_url is not None:
            if not isinstance(start_url, str) or not start_url.strip():
                return "browser_launch.start_url must be non-empty URL"
            # When enterprise policies are enabled, URL allowlist validation happens in policy_engine.
            if (not _enterprise_policies_enabled()) and (not _is_allowed_browser_url(start_url)):
                return "browser_launch.start_url domain is not allowed"
        return None

    if action == "browser_goto":
        url = parameters.get("url")
        if not isinstance(url, str) or not url.strip():
            return "browser_goto requires url"
        # When enterprise policies are enabled, URL allowlist validation happens in policy_engine.
        if (not _enterprise_policies_enabled()) and (not _is_allowed_browser_url(url)):
            return "browser_goto.url domain is not allowed"
        return None

    if action == "browser_click":
        selector = parameters.get("selector")
        text = parameters.get("text")
        role = parameters.get("role")
        has_target = any(isinstance(item, str) and item.strip() for item in (selector, text, role))
        has_x = "x" in parameters
        has_y = "y" in parameters
        if has_x != has_y:
            return "browser_click requires x and y together"
        if has_x and has_y:
            x = parameters.get("x")
            y = parameters.get("y")
            if not isinstance(x, int) or not isinstance(y, int):
                return "browser_click.x and browser_click.y must be integers"
            if x < 0 or y < 0:
                return "browser_click.x and browser_click.y cannot be negative"
        if not has_target and not (has_x and has_y):
            return "browser_click requires selector|text|role or x/y"
        timeout_ms = parameters.get("timeout_ms")
        if timeout_ms is not None and (not isinstance(timeout_ms, int) or timeout_ms < 500):
            return "browser_click.timeout_ms must be integer >= 500"
        return None

    if action == "browser_fill":
        selector = parameters.get("selector")
        text = parameters.get("text")
        if not isinstance(selector, str) or not selector.strip():
            return "browser_fill requires selector"
        if not isinstance(text, str):
            return "browser_fill requires text"
        return None

    if action == "browser_press":
        selector = parameters.get("selector")
        key = parameters.get("key")
        if not isinstance(selector, str) or not selector.strip():
            return "browser_press requires selector"
        if not isinstance(key, str) or not key.strip():
            return "browser_press requires key"
        return None

    if action == "browser_extract":
        selector = parameters.get("selector")
        if not isinstance(selector, str) or not selector.strip():
            return "browser_extract requires selector"
        return None

    if action in {"browser_screenshot", "browser_close"}:
        return None

    return None


async def _find_agent_id(
    *,
    organization_id: UUID,
    hinted_agent_id: Optional[str],
    require_online: bool = False,
) -> Optional[UUID]:
    async with AsyncSessionLocal() as session:
        if hinted_agent_id:
            parsed_hint = _parse_optional_uuid(hinted_agent_id)
            if parsed_hint:
                hinted_stmt = select(DesktopAgent.id).where(
                    DesktopAgent.organization_id == organization_id,
                    DesktopAgent.id == parsed_hint,
                )
                hinted = (await session.execute(hinted_stmt)).scalars().first()
                if hinted:
                    return hinted

        online_stmt = (
            select(DesktopAgent.id)
            .where(
                DesktopAgent.organization_id == organization_id,
                DesktopAgent.status == "online",
            )
            .order_by(
                desc(DesktopAgent.last_heartbeat_at),
                desc(DesktopAgent.updated_at),
            )
            .limit(1)
        )
        online = (await session.execute(online_stmt)).scalars().first()
        if online:
            return online
        if require_online:
            return None

        fallback_stmt = (
            select(DesktopAgent.id)
            .where(DesktopAgent.organization_id == organization_id)
            .order_by(desc(DesktopAgent.updated_at))
            .limit(1)
        )
        return (await session.execute(fallback_stmt)).scalars().first()


def is_desktop_action(action: ActionContract, metadata: Optional[Dict[str, Any]] = None) -> bool:
    if action.action_type == "desktop":
        return True
    if action.integration == "desktop":
        return True
    if action.integration == "browser":
        return True
    if isinstance(metadata, dict) and str(metadata.get("action_type") or "").strip().lower() == "desktop":
        return True
    if isinstance(metadata, dict) and str(metadata.get("integration") or "").strip().lower() == "browser":
        return True
    return False


async def send_to_desktop_agent(
    *,
    client_id: str,
    action: str,
    parameters: Optional[Dict[str, Any]] = None,
    metadata: Optional[Dict[str, Any]] = None,
    requested_by_user_id: Optional[str] = None,
) -> Dict[str, Any]:
    if not _feature_enabled():
        return {"accepted": False, "command_id": None, "reason": "feature_disabled"}

    try:
        organization_id = UUID(client_id)
    except ValueError:
        return {"accepted": False, "command_id": None, "reason": "invalid_client_id"}

    action_name = str(action or "").strip().lower()
    if action_name not in _DESKTOP_ACTIONS:
        return {"accepted": False, "command_id": None, "reason": "unsupported_action"}

    parameters_dict = dict(parameters or {})
    validation_error = _validate_parameters(action_name, parameters_dict)
    if validation_error:
        return {
            "accepted": False,
            "command_id": None,
            "reason": "invalid_parameters",
            "detail": validation_error,
        }

    metadata_payload = dict(metadata or {})
    existing_approval = metadata_payload.get("approval")
    approval_payload = dict(existing_approval) if isinstance(existing_approval, dict) else {}
    requested_required: Optional[bool] = None
    if isinstance(approval_payload.get("required"), bool):
        requested_required = bool(approval_payload.get("required"))
    elif isinstance(metadata_payload.get("requires_human_approval"), bool):
        requested_required = bool(metadata_payload.get("requires_human_approval"))

    risk_level = _action_risk_level(action_name, parameters_dict)

    # Environment-based auto-approval (legacy path). When enterprise policies are enabled,
    # policy-based decision will override this.
    auto_approvable_env = _is_auto_approvable(action_name, parameters_dict, metadata_payload)
    if requested_required is False and (not _enterprise_policies_enabled()) and (not auto_approvable_env):
        return {
            "accepted": False,
            "command_id": None,
            "reason": (
                "browser_requires_human_approval"
                if _is_browser_action(action_name)
                else "auto_approval_not_allowed"
            ),
        }

    hinted_agent_id = None
    if isinstance(metadata_payload, dict):
        hinted_agent_id = (
            metadata_payload.get("agent_id")
            or metadata_payload.get("desktop_agent_id")
            or parameters_dict.get("agent_id")
        )
    elif "agent_id" in parameters_dict:
        hinted_agent_id = parameters_dict.get("agent_id")

    require_online = _runner_mode() == "remote" or _desktop_requires_online_runner()
    agent_id = await _find_agent_id(
        organization_id=organization_id,
        hinted_agent_id=(str(hinted_agent_id) if hinted_agent_id is not None else None),
        require_online=require_online,
    )
    if not agent_id:
        reason = "runner_offline" if require_online else "agent_not_found"
        return {"accepted": False, "command_id": None, "reason": reason}

    timeout_ms = _coerce_timeout_ms(parameters_dict)
    command_args = _sanitize_parameters(parameters_dict)
    async with AsyncSessionLocal() as session:
        policy = None
        if _enterprise_policies_enabled():
            try:
                policy = await load_effective_policy(session, organization_id=organization_id)
                policy_error = validate_desktop_action(
                    action=action_name,
                    args=dict(command_args),
                    policy=policy,
                )
                if policy_error:
                    return {
                        "accepted": False,
                        "command_id": None,
                        "reason": "blocked_by_policy",
                        "detail": policy_error,
                    }
            except Exception:
                # Policy engine is best-effort in this path; don't block baseline command flow.
                await session.rollback()
                policy = None

        # Policy rule: force human approval when required (cannot be bypassed by callers).
        if policy and policy_requires_human_approval_for_action(
            action=action_name,
            args=dict(command_args),
            policy=policy,
        ):
            requested_required = True
            metadata_payload["policy_required_human_approval"] = True

        # Policy-based auto-approval decision (authoritative when policies are enforced).
        auto_approvable_policy = False
        if policy and policy.enforced:
            exec_mode = policy.autonomy_execution_mode()
            if exec_mode in {"semi_auto", "full_auto"} and policy.auto_approve_enabled_for(action_name):
                max_risk = policy.max_auto_risk()
                auto_approvable_policy = _RISK_LEVEL_ORDER[risk_level] <= _RISK_LEVEL_ORDER.get(max_risk, 1)

        auto_approvable = auto_approvable_policy if (policy and policy.enforced) else auto_approvable_env

        if requested_required is False and not auto_approvable:
            return {
                "accepted": False,
                "command_id": None,
                "reason": (
                    "browser_requires_human_approval"
                    if _is_browser_action(action_name)
                    else "auto_approval_not_allowed"
                ),
            }

        if requested_required is True:
            approval_required = True
        elif requested_required is False:
            approval_required = False
        else:
            approval_required = not auto_approvable

        if _is_browser_action(action_name) and not auto_approvable and not approval_required:
            return {
                "accepted": False,
                "command_id": None,
                "reason": "browser_requires_human_approval",
            }

        approval_payload["required"] = approval_required
        approval_payload["status"] = "pending" if approval_required else "approved"
        metadata_payload["approval"] = approval_payload
        metadata_payload["requires_human_approval"] = approval_required
        metadata_payload["risk_level"] = risk_level
        metadata_payload["auto_approval_applied"] = not approval_required

        recovery_control = str(metadata_payload.get("recovery_control") or "").strip().lower()
        if recovery_control not in {"managed", ""}:
            recovery_control = ""

        initial_status = "pending_approval" if approval_required else "approved"
        approved_at = datetime.now(timezone.utc) if not approval_required else None
        approved_by_user_id = _parse_optional_uuid(requested_by_user_id) if not approval_required else None

        command = DesktopCommand(
            organization_id=organization_id,
            agent_id=agent_id,
            action=action_name,
            args=command_args,
            status=initial_status,
            timeout_ms=timeout_ms,
            requested_by_user_id=_parse_optional_uuid(requested_by_user_id),
            approved_by_user_id=approved_by_user_id,
            approved_at=approved_at,
            command_metadata={
                "source": str(metadata_payload.get("source") or "chat_action"),
                **({"recovery_control": "managed"} if recovery_control == "managed" else {}),
                "metadata": metadata_payload,
                "approval": approval_payload,
                "requires_human_approval": approval_required,
                "risk_level": risk_level,
                "auto_approval_applied": (not approval_required),
            },
        )
        session.add(command)
        await session.commit()
        await session.refresh(command)

        await write_audit_log(
            session,
            organization_id=organization_id,
            actor_user_id=_parse_optional_uuid(requested_by_user_id),
            action="desktop_agent.command.enqueue",
            resource="desktop_command",
            resource_id=str(command.id),
            status="ok",
            metadata={
                "agent_id": str(agent_id),
                "desktop_action": action_name,
                "timeout_ms": timeout_ms,
            },
        )

    return {
        "accepted": True,
        "command_id": str(command.id),
        "status": initial_status,
        "approval_required": approval_required,
        "risk_level": risk_level,
    }


def runner_offline_instructions(base_url: Optional[str] = None) -> str:
    """Instructions to show when desktop actions fail because no runner is connected."""
    url = (base_url or os.getenv("BACKEND_URL") or os.getenv("API_URL") or "https://tu-backend.up.railway.app").rstrip("/")
    ws_url = f"{url}/desktop-agent/ws"
    client_id = os.getenv("JARVIS_CLIENT_ID") or os.getenv("TELEGRAM_DEFAULT_CLIENT_ID") or "<CLIENT_ID>"
    token_placeholder = "<RUNNER_TOKEN>" if not os.getenv("RUNNER_TOKEN") else "(configurado)"
    return (
        "⚠️ No hay Runner conectado en tu Mac. Las acciones de escritorio se ejecutan en tu PC mediante un Runner local.\n\n"
        "1. En tu Mac, abre Terminal.\n"
        "2. Configura y ejecuta el Runner:\n"
        f"   export WS_URL=\"{ws_url}\"\n"
        f"   export CLIENT_ID=\"{client_id}\"\n"
        f"   export RUNNER_TOKEN={token_placeholder}\n"
        "   python -m runner.local_runner\n\n"
        "El token RUNNER_TOKEN debe coincidir con el del servidor (RUNNER_TOKEN o RUNNER_TOKENS_JSON). "
        "Cuando el Runner esté conectado, vuelve a intentar."
    )
