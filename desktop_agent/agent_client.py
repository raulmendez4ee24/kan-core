import asyncio
import contextlib
import inspect
import json
import logging
import threading
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from desktop_agent.config import AgentConfig, load_config
from desktop_agent.controller import DesktopController
from desktop_agent.human_recorder import HumanRecorder

logger = logging.getLogger("desktop_agent")



def _get_websockets_module():
    try:
        import websockets  # type: ignore
    except Exception as exc:
        raise RuntimeError("websockets package is required for desktop agent") from exc
    return websockets


async def send_result(
    websocket: Any,
    *,
    command_id: str,
    status: str,
    duration_ms: int,
    error: str | None,
    data: Dict[str, Any],
) -> None:
    payload = {
        "type": "result",
        "command_id": command_id,
        "status": status,
        "duration_ms": duration_ms,
        "error": error,
        "data": data,
    }
    await websocket.send(json.dumps(payload))


async def send_human_event(
    websocket: Any,
    *,
    recording_id: str,
    ts: str,
    event_type: str,
    payload: Dict[str, Any],
    context: Dict[str, Any],
) -> None:
    msg = {
        "type": "human_event",
        "recording_id": recording_id,
        "ts": ts,
        "event_type": event_type,
        "payload": payload,
        "context": context,
    }
    await websocket.send(json.dumps(msg))


async def _human_event_sender_loop(websocket: Any, queue: "asyncio.Queue[Dict[str, Any]]") -> None:
    while True:
        item = await queue.get()
        try:
            await send_human_event(
                websocket,
                recording_id=str(item.get("recording_id") or ""),
                ts=str(item.get("ts") or datetime.now(timezone.utc).isoformat()),
                event_type=str(item.get("event_type") or ""),
                payload=item.get("payload") if isinstance(item.get("payload"), dict) else {},
                context=item.get("context") if isinstance(item.get("context"), dict) else {},
            )
        except Exception:
            # If the connection drops, the main loop will reconnect.
            return


async def _heartbeat_loop(websocket: Any, config: AgentConfig) -> None:
    while True:
        payload = {
            "type": "heartbeat",
            "agent_id": config.client_id,
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        await websocket.send(json.dumps(payload))
        await asyncio.sleep(config.heartbeat_interval_seconds)


async def _handle_command(
    websocket: Any,
    *,
    controller: DesktopController,
    message: Dict[str, Any],
    default_timeout_ms: int,
) -> None:
    command_id = str(message.get("command_id") or "")
    if not command_id:
        return

    approved = bool(message.get("approved", False))
    if not approved:
        await send_result(
            websocket,
            command_id=command_id,
            status="failed",
            duration_ms=0,
            error="command_not_approved",
            data={},
        )
        return

    await websocket.send(
        json.dumps(
            {
                "type": "ack",
                "command_id": command_id,
                "status": "received",
            }
        )
    )

    action = str(message.get("action") or "")
    args = message.get("args") or {}
    timeout_ms = int(message.get("timeout_ms") or default_timeout_ms)

    execution = await controller.execute(action=action, args=args, timeout_ms=timeout_ms)
    await send_result(
        websocket,
        command_id=command_id,
        status=str(execution.get("status") or "failed"),
        duration_ms=int(execution.get("duration_ms") or 0),
        error=execution.get("error"),
        data=execution.get("payload") or {},
    )


async def run_agent(config: AgentConfig) -> None:
    websockets = _get_websockets_module()
    backoff = config.reconnect_backoff_min_seconds

    controller = DesktopController(
        open_program_allowlist=config.open_program_allowlist,
        screenshot_max_width=config.screenshot_max_width,
        unsafe_allow_all=config.unsafe_allow_all,
        browser_enabled=config.browser_enabled,
        browser_headless=config.browser_headless,
        browser_channel=config.browser_channel,
        browser_user_data_dir=config.browser_user_data_dir,
        browser_domain_allowlist=config.browser_domain_allowlist,
        browser_default_timeout_ms=config.browser_default_timeout_ms,
        browser_max_attempts=config.browser_max_attempts,
    )
    if config.browser_enabled and config.browser_prewarm:
        try:
            _ = await controller.execute(
                action="browser_launch",
                args={},
                timeout_ms=max(config.command_timeout_ms, 30000),
            )
            logger.info("browser prewarm completed")
        except Exception as exc:
            logger.warning("browser prewarm failed: %s", exc)

    headers = {
        "X-Client-Id": config.client_id,
        "X-Client-Token": config.client_token,
        "X-Agent-Name": config.agent_name,
        "X-Agent-Environment": config.environment,
    }
    connect_signature = inspect.signature(websockets.connect)
    header_kw = "extra_headers"
    if "additional_headers" in connect_signature.parameters:
        header_kw = "additional_headers"

    while True:
        try:
            connection = websockets.connect(
                config.backend_ws_url,
                **{header_kw: headers},
                ping_interval=None,
                close_timeout=2,
            )
            async with connection as websocket:
                logger.info("connected to backend")
                ws_loop = asyncio.get_running_loop()
                human_queue: "asyncio.Queue[Dict[str, Any]]" = asyncio.Queue()
                human_sender_task = asyncio.create_task(_human_event_sender_loop(websocket, human_queue))
                active_recording_id: Optional[str] = None
                active_recorder: Optional[HumanRecorder] = None
                recording_lock = threading.Lock()

                def _enqueue_human_event(event: Dict[str, Any]) -> None:
                    nonlocal active_recording_id
                    with recording_lock:
                        rec_id = active_recording_id
                    if not rec_id:
                        return
                    item = dict(event or {})
                    item["recording_id"] = rec_id
                    # Ensure required keys exist.
                    if "ts" not in item:
                        item["ts"] = datetime.now(timezone.utc).isoformat()
                    if "event_type" not in item:
                        item["event_type"] = "unknown"
                    if "payload" not in item or not isinstance(item.get("payload"), dict):
                        item["payload"] = {}
                    if "context" not in item or not isinstance(item.get("context"), dict):
                        item["context"] = {}
                    try:
                        ws_loop.call_soon_threadsafe(human_queue.put_nowait, item)
                    except Exception:
                        return

                await websocket.send(
                    json.dumps(
                        {
                            "type": "agent_hello",
                            "agent_name": config.agent_name,
                            "environment": config.environment,
                            "version": "1.0.0",
                        }
                    )
                )

                heartbeat_task = asyncio.create_task(_heartbeat_loop(websocket, config))
                backoff = config.reconnect_backoff_min_seconds
                try:
                    async for raw in websocket:
                        try:
                            message = json.loads(raw)
                        except json.JSONDecodeError:
                            continue

                        msg_type = str(message.get("type") or "").lower()
                        if msg_type == "command":
                            try:
                                await _handle_command(
                                    websocket,
                                    controller=controller,
                                    message=message,
                                    default_timeout_ms=config.command_timeout_ms,
                                )
                            except Exception as exc:
                                command_id = str(message.get("command_id") or "")
                                if command_id:
                                    with contextlib.suppress(Exception):
                                        await send_result(
                                            websocket,
                                            command_id=command_id,
                                            status="failed",
                                            duration_ms=0,
                                            error=f"agent_command_error:{exc!s}",
                                            data={},
                                        )
                                logger.warning("command handling failed: %s", exc)
                                continue
                        elif msg_type == "recording_control":
                            if not config.human_recorder_enabled:
                                await websocket.send(
                                    json.dumps(
                                        {
                                            "type": "ack",
                                            "status": "error",
                                            "detail": "human_recorder_disabled",
                                        }
                                    )
                                )
                                continue

                            status = str(message.get("status") or "").strip().lower()
                            recording_id = str(message.get("recording_id") or "").strip()
                            redaction = message.get("redaction") if isinstance(message.get("redaction"), dict) else {}

                            if status in {"start", "resume"} and recording_id:
                                with recording_lock:
                                    active_recording_id = recording_id
                                if active_recorder is None:
                                    try:
                                        active_recorder = HumanRecorder(on_event=_enqueue_human_event, redaction=redaction)
                                    except Exception as exc:
                                        await websocket.send(
                                            json.dumps(
                                                {
                                                    "type": "ack",
                                                    "status": "error",
                                                    "detail": f"human_recorder_init_failed:{exc!s}",
                                                }
                                            )
                                        )
                                        continue
                                else:
                                    try:
                                        active_recorder.update_redaction(redaction)
                                    except Exception:
                                        pass

                                try:
                                    active_recorder.start()
                                except Exception as exc:
                                    await websocket.send(
                                        json.dumps(
                                            {
                                                "type": "ack",
                                                "status": "error",
                                                "detail": f"human_recorder_start_failed:{exc!s}",
                                            }
                                        )
                                    )
                                    continue

                                await websocket.send(
                                    json.dumps(
                                        {
                                            "type": "ack",
                                            "status": "received",
                                            "detail": f"recording_control:{status}",
                                        }
                                    )
                                )
                            elif status in {"stop", "pause"}:
                                # Stop recorder first (flushes buffer) while recording_id is still active.
                                if active_recorder is not None:
                                    try:
                                        active_recorder.stop()
                                    except Exception:
                                        pass
                                with recording_lock:
                                    active_recording_id = None
                                await websocket.send(
                                    json.dumps(
                                        {
                                            "type": "ack",
                                            "status": "received",
                                            "detail": f"recording_control:{status}",
                                        }
                                    )
                                )
                            else:
                                await websocket.send(
                                    json.dumps(
                                        {
                                            "type": "ack",
                                            "status": "error",
                                            "detail": "invalid_recording_control",
                                        }
                                    )
                                )
                        elif msg_type == "ack":
                            logger.debug("ack from backend: %s", message)
                finally:
                    heartbeat_task.cancel()
                    with contextlib.suppress(BaseException):
                        await heartbeat_task
                    if active_recorder is not None:
                        with contextlib.suppress(BaseException):
                            active_recorder.stop()
                    human_sender_task.cancel()
                    with contextlib.suppress(BaseException):
                        await human_sender_task
        except Exception as exc:
            logger.warning("connection failed, retrying in %.1fs: %s", backoff, exc)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2.0, config.reconnect_backoff_max_seconds)


async def main_async() -> None:
    config = load_config()
    await run_agent(config)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
