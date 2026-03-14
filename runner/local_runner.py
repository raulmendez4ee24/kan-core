import asyncio
import contextlib
import inspect
import json
import logging
import os
import random
from datetime import datetime, timezone
from typing import Any, Dict, Tuple

from config.env_loader import load_environment
from desktop_agent.controller import DesktopController

logger = logging.getLogger("kan_core.local_runner")

load_environment(context="local_runner")


def _env(name: str, default: str = "") -> str:
    return str(os.getenv(name, default) or "").strip()


def _required_env(name: str) -> str:
    value = _env(name)
    if not value:
        raise RuntimeError(f"Missing required env {name}")
    return value


def _heartbeat_seconds() -> float:
    raw = _env("RUNNER_HEARTBEAT_SECONDS", "5")
    try:
        value = float(raw)
    except ValueError:
        value = 5.0
    return max(1.0, min(value, 60.0))


def _load_allowlist() -> Dict[str, str]:
    raw = _env("DESKTOP_AGENT_ALLOWLIST_JSON", "{}")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = {}
    if not isinstance(parsed, dict):
        return {}
    normalized: Dict[str, str] = {}
    for key, value in parsed.items():
        k = str(key or "").strip().lower()
        v = str(value or "").strip()
        if k and v:
            normalized[k] = v
    return normalized


def _load_browser_allowlist() -> list[str]:
    raw = _env("DESKTOP_AGENT_BROWSER_DOMAIN_ALLOWLIST_JSON", "[]")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = []
    if not isinstance(parsed, list):
        return []
    out: list[str] = []
    for item in parsed:
        token = str(item or "").strip().lower()
        if token and token not in out:
            out.append(token)
    return out


def _reconnect_bounds() -> Tuple[float, float]:
    raw_min = _env("RUNNER_RECONNECT_BACKOFF_MIN_SECONDS", "1")
    raw_max = _env("RUNNER_RECONNECT_BACKOFF_MAX_SECONDS", "30")
    try:
        minimum = float(raw_min)
    except ValueError:
        minimum = 1.0
    try:
        maximum = float(raw_max)
    except ValueError:
        maximum = 30.0
    minimum = max(0.5, minimum)
    maximum = max(minimum, maximum)
    return minimum, maximum


def _normalize_instruction(message: Dict[str, Any]) -> Tuple[str, str, str, Dict[str, Any], str]:
    msg_type = str(message.get("type") or "").strip().lower()
    if msg_type == "execute":
        return (
            str(message.get("run_id") or ""),
            str(message.get("step_id") or ""),
            str(message.get("action") or ""),
            (message.get("args") if isinstance(message.get("args"), dict) else {}),
            str(message.get("tool") or "desktop").strip().lower(),
        )
    if msg_type == "command":
        command_id = str(message.get("command_id") or "")
        return (
            str(message.get("run_id") or ""),
            command_id,
            str(message.get("action") or ""),
            (message.get("args") if isinstance(message.get("args"), dict) else {}),
            ("browser" if str(message.get("action") or "").startswith("browser_") else "desktop"),
        )
    return "", "", "", {}, ""


def _map_action(tool: str, action: str, args: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    name = str(action or "").strip().lower()
    payload = dict(args or {})
    if name in {
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
    }:
        return name, payload

    if name == "open_app":
        app_name = payload.get("name") or payload.get("app") or payload.get("application")
        return "open_program", {"name": str(app_name or "").strip()}
    if name == "goto":
        return "browser_goto", {"url": payload.get("url")}
    if name == "fill":
        return "browser_fill", {"selector": payload.get("selector"), "text": payload.get("text")}
    if name == "extract":
        return "browser_extract", {"selector": payload.get("selector")}
    if name in {"press", "hotkey"}:
        if tool == "browser":
            return "browser_press", {"selector": payload.get("selector"), "key": payload.get("key")}
        return "press_key", {"key": payload.get("key")}
    if name == "click":
        if tool == "browser":
            return "browser_click", payload
        return "click", payload
    if name == "screenshot":
        return ("browser_screenshot" if tool == "browser" else "take_screenshot"), payload
    return name, payload


async def _heartbeat_loop(websocket: Any, client_id: str) -> None:
    interval = _heartbeat_seconds()
    while True:
        await websocket.send(
            json.dumps(
                {
                    "type": "heartbeat",
                    "agent_id": client_id,
                    "ts": datetime.now(timezone.utc).isoformat(),
                }
            )
        )
        await asyncio.sleep(interval)


async def _send_result(
    websocket: Any,
    *,
    run_id: str,
    step_id: str,
    status: str,
    summary: str,
    data: Dict[str, Any],
    evidence: Dict[str, Any],
    duration_ms: int,
    error: str | None = None,
) -> None:
    command_id = step_id
    payload = {
        "type": "result",
        "run_id": run_id,
        "step_id": step_id,
        "command_id": command_id,
        "status": ("completed" if status == "completed" else "failed"),
        "duration_ms": duration_ms,
        "error": error,
        "summary": summary,
        "data": {**data, "summary": summary, "evidence": evidence},
        "evidence": evidence,
    }
    await websocket.send(json.dumps(payload))


async def _handle_execute_message(
    websocket: Any,
    *,
    controller: DesktopController,
    message: Dict[str, Any],
) -> None:
    run_id, step_id, action, args, tool = _normalize_instruction(message)
    if not step_id:
        return
    started = datetime.now(timezone.utc)
    mapped_action, mapped_args = _map_action(tool, action, args)
    timeout_ms = int(message.get("timeout_ms") or _env("RUNNER_ACTION_TIMEOUT_MS", "20000"))
    try:
        result = await controller.execute(
            action=mapped_action,
            args=mapped_args,
            timeout_ms=timeout_ms,
        )
        status = "completed" if str(result.get("status") or "").lower() == "success" else "failed"
        payload = result.get("payload") if isinstance(result.get("payload"), dict) else {}
        evidence: Dict[str, Any] = {}
        if isinstance(payload.get("screenshot_base64"), str):
            evidence["screenshot_b64"] = payload.get("screenshot_base64")
        duration_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
        await _send_result(
            websocket,
            run_id=run_id,
            step_id=step_id,
            status=status,
            summary=str(result.get("error") or "completed"),
            data=payload,
            evidence=evidence,
            duration_ms=duration_ms,
            error=(str(result.get("error")) if result.get("error") else None),
        )
    except Exception as exc:
        duration_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
        await _send_result(
            websocket,
            run_id=run_id,
            step_id=step_id,
            status="failed",
            summary=f"runner_error:{exc!s}",
            data={},
            evidence={},
            duration_ms=duration_ms,
            error=f"runner_error:{exc!s}",
        )


async def run_local_runner() -> None:
    ws_url = _required_env("WS_URL")
    client_id = _required_env("CLIENT_ID")
    runner_token = _required_env("RUNNER_TOKEN")
    agent_name = _env("RUNNER_NAME", f"local-runner-{client_id[:8]}")
    agent_env = _env("RUNNER_ENV", "production")

    import websockets  # type: ignore

    controller = DesktopController(
        open_program_allowlist=_load_allowlist(),
        screenshot_max_width=int(_env("DESKTOP_AGENT_SCREENSHOT_MAX_WIDTH", "1600") or "1600"),
        unsafe_allow_all=_env("DESKTOP_AGENT_UNSAFE_ALLOW_ALL", "false").lower() in {"1", "true", "yes"},
        browser_enabled=_env("DESKTOP_AGENT_BROWSER_ENABLED", "true").lower() in {"1", "true", "yes"},
        browser_headless=_env("DESKTOP_AGENT_BROWSER_HEADLESS", "false").lower() in {"1", "true", "yes"},
        browser_channel=_env("DESKTOP_AGENT_BROWSER_CHANNEL", "chromium") or "chromium",
        browser_user_data_dir=_env("DESKTOP_AGENT_BROWSER_USER_DATA_DIR", ""),
        browser_domain_allowlist=_load_browser_allowlist(),
        browser_default_timeout_ms=int(_env("DESKTOP_AGENT_BROWSER_DEFAULT_TIMEOUT_MS", "15000") or "15000"),
        browser_max_attempts=int(_env("DESKTOP_AGENT_BROWSER_MAX_ATTEMPTS", "2") or "2"),
    )

    backoff_min, backoff_max = _reconnect_bounds()
    backoff = backoff_min
    while True:
        try:
            connect_kwargs = {
                "ping_interval": None,
            }
            headers = {
                "X-Client-Id": client_id,
                "X-Runner-Token": runner_token,
                "X-Agent-Name": agent_name,
                "X-Agent-Environment": agent_env,
            }
            connect_signature = inspect.signature(websockets.connect)
            if "additional_headers" in connect_signature.parameters:
                connect_kwargs["additional_headers"] = headers
            else:
                connect_kwargs["extra_headers"] = headers
            async with websockets.connect(ws_url, **connect_kwargs) as websocket:
                logger.info("Local runner connected: %s", ws_url)
                await websocket.send(json.dumps({"type": "agent_hello", "agent_name": agent_name, "client_id": client_id}))
                hb_task = asyncio.create_task(_heartbeat_loop(websocket, client_id))
                backoff = backoff_min
                try:
                    async for raw in websocket:
                        try:
                            message = json.loads(raw)
                        except json.JSONDecodeError:
                            continue
                        await _handle_execute_message(
                            websocket,
                            controller=controller,
                            message=message,
                        )
                finally:
                    hb_task.cancel()
                    with contextlib.suppress(BaseException):
                        await hb_task
        except Exception as exc:
            logger.warning("Runner disconnected. reconnecting in %.1fs: %s", backoff, exc)
            await asyncio.sleep(backoff + random.uniform(0, 0.25))
            backoff = min(backoff * 2.0, backoff_max)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_local_runner())


if __name__ == "__main__":
    main()
