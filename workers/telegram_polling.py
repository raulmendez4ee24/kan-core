import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from brain.dispatcher import handle_telegram_event
from config.env_helpers import env_bool
from config.env_loader import load_environment
from integrations.telegram_admin import ensure_telegram_mode, resolve_telegram_mode

logger = logging.getLogger("kan_core.workers.telegram_polling")
_POLLING_TASK: asyncio.Task | None = None


def _token() -> str:
    return str(os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN") or "").strip()


def _offset_file() -> Path:
    raw = str(os.getenv("TELEGRAM_POLLING_OFFSET_FILE") or ".run/telegram_offset.txt").strip()
    return Path(raw)


def _load_offset() -> int:
    path = _offset_file()
    try:
        value = int(path.read_text(encoding="utf-8").strip())
        return max(0, value)
    except Exception:
        return 0


def _save_offset(value: int) -> None:
    path = _offset_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(max(0, int(value))), encoding="utf-8")


async def _poll_once(client: httpx.AsyncClient, *, token: str, offset: int, timeout_s: int) -> tuple[int, int]:
    url = f"https://api.telegram.org/bot{token}/getUpdates"
    resp = await client.get(
        url,
        params={
            "offset": offset,
            "timeout": timeout_s,
            "allowed_updates": json.dumps(["message"]),
        },
        timeout=timeout_s + 5,
    )
    resp.raise_for_status()
    data = resp.json()
    items: List[Dict[str, Any]] = list(data.get("result") or [])
    next_offset = offset
    processed = 0
    for item in items:
        update_id = int(item.get("update_id") or 0)
        if update_id <= 0:
            continue
        next_offset = max(next_offset, update_id + 1)
        processed += 1
        try:
            await handle_telegram_event(item)
        except Exception:
            logger.exception("Failed to process telegram update_id=%s", update_id)
    return next_offset, processed


async def _run_forever() -> None:
    load_environment(context="telegram_polling")
    mode_state = resolve_telegram_mode()
    if str(mode_state.get("mode") or "") != "polling":
        logger.info("Telegram polling desactivado por modo=%s", mode_state.get("mode"))
        return

    load_environment(
        strict=True,
        required_all=("JARVIS_CLIENT_ID",),
        required_any=(("TELEGRAM_BOT_TOKEN", "TELEGRAM_TOKEN"),),
        context="telegram_polling",
    )
    mode_state = await ensure_telegram_mode(logger)
    resolved_mode = str(mode_state.get("mode") or "off")
    if resolved_mode != "polling":
        logger.info("Telegram polling abortado: mode=%s", resolved_mode)
        return

    token = _token()
    if not token:
        raise SystemExit("TELEGRAM_BOT_TOKEN is required")

    interval_s = max(0.2, float(os.getenv("TELEGRAM_POLLING_INTERVAL_SECONDS", "1.0")))
    timeout_s = max(1, int(os.getenv("TELEGRAM_POLLING_LONG_TIMEOUT_SECONDS", "25")))
    offset = _load_offset()
    logger.info("Telegram polling started (offset=%s)", offset)

    async with httpx.AsyncClient() as client:
        while True:
            try:
                offset, processed = await _poll_once(client, token=token, offset=offset, timeout_s=timeout_s)
                _save_offset(offset)
                if processed:
                    logger.info("Telegram polling processed=%s new offset=%s", processed, offset)
            except httpx.HTTPStatusError as exc:
                status_code: Optional[int] = exc.response.status_code if exc.response is not None else None
                if status_code == 409:
                    description = ""
                    try:
                        data = exc.response.json() if exc.response is not None else {}
                        if isinstance(data, dict):
                            description = str(data.get("description") or "").strip()
                    except Exception:
                        description = ""
                    lower_desc = description.lower()
                    if "terminated by other getupdates request" in lower_desc or "can't use getupdates method" in lower_desc:
                        logger.error(
                            "Telegram polling detenido: otra instancia usa getUpdates (%s). "
                            "Mantén una sola instancia activa del bot.",
                            description or "409 conflict",
                        )
                        return
                    logger.warning(
                        "Telegram polling recibió 409; ejecutando auto-healing de modo Telegram."
                    )
                    healed = await ensure_telegram_mode(logger)
                    healed_mode = str(healed.get("mode") or "off")
                    if healed_mode != "polling":
                        logger.info(
                            "Telegram polling detenido tras auto-healing (mode=%s).",
                            healed_mode,
                        )
                        return
                    await asyncio.sleep(max(1.0, interval_s))
                    continue
                logger.exception("Telegram polling tick failed")
                await asyncio.sleep(max(1.0, interval_s))
                continue
            except Exception:
                logger.exception("Telegram polling tick failed")
                await asyncio.sleep(max(1.0, interval_s))
                continue
            await asyncio.sleep(interval_s)


def start_polling_task() -> asyncio.Task:
    """
    Attach polling to the current running loop.
    Prevents `asyncio.run()` clashes when another loop is already active.
    """
    global _POLLING_TASK
    loop = asyncio.get_running_loop()
    if _POLLING_TASK is not None and not _POLLING_TASK.done():
        return _POLLING_TASK
    _POLLING_TASK = loop.create_task(_run_forever(), name="telegram-polling")
    logger.info("Telegram polling attached to existing asyncio loop")
    return _POLLING_TASK


def main() -> None:
    logging.basicConfig(
        level=getattr(logging, str(os.getenv("LOG_LEVEL", "INFO")).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    try:
        running_loop = asyncio.get_running_loop()
    except RuntimeError:
        running_loop = None

    if running_loop and running_loop.is_running():
        start_polling_task()
        return

    asyncio.run(_run_forever())


if __name__ == "__main__":
    main()
