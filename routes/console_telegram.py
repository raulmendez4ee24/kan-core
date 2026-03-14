from __future__ import annotations

from fastapi import APIRouter

from integrations.telegram_admin import get_webhook_info, resolve_telegram_mode, telegram_token

router = APIRouter(prefix="/console/telegram", tags=["console-telegram"])


@router.get("/mode")
async def telegram_mode_status() -> dict:
    state = resolve_telegram_mode()
    token = telegram_token()
    webhook_info: dict = {}
    if token:
        info = await get_webhook_info(token)
        webhook_info = info.get("result") if isinstance(info.get("result"), dict) else {}
        if not webhook_info and info.get("error"):
            webhook_info = {"error": str(info.get("error") or "")}

    return {
        "mode": state.get("mode"),
        "configured_mode": state.get("configured_mode"),
        "token_present": bool(token),
        "webhook_url": str(state.get("webhook_url") or ""),
        "webhook_info": webhook_info,
    }

