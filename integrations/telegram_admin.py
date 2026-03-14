from __future__ import annotations

import logging
import os
from typing import Any, Dict

import httpx

_LOG = logging.getLogger("kan_core.telegram_admin")
_API_BASE = "https://api.telegram.org"
_VALID_MODES = {"polling", "webhook", "off"}


def _env_bool(name: str, default: str = "false") -> bool:
    return str(os.getenv(name, default)).strip().lower() in {"1", "true", "yes"}


def telegram_token() -> str:
    return str(os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN") or "").strip()


def resolve_telegram_mode() -> Dict[str, Any]:
    configured_mode = str(os.getenv("TELEGRAM_MODE") or "").strip().lower()
    token = telegram_token()
    webhook_url = str(os.getenv("TELEGRAM_WEBHOOK_URL") or "").strip()
    legacy_polling = not configured_mode and _env_bool("ENABLE_TELEGRAM_POLLING", "false")

    if configured_mode in _VALID_MODES:
        mode = configured_mode
    elif legacy_polling:
        mode = "polling"
    elif token and webhook_url:
        mode = "webhook"
    elif token:
        mode = "polling"
    else:
        mode = "off"

    return {
        "mode": mode,
        "configured_mode": configured_mode or None,
        "legacy_polling": legacy_polling,
        "token_present": bool(token),
        "webhook_url": webhook_url,
    }


async def get_webhook_info(token: str) -> Dict[str, Any]:
    probe = str(token or "").strip()
    if not probe:
        return {"ok": False, "error": "missing token", "result": {}}

    url = f"{_API_BASE}/bot{probe}/getWebhookInfo"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url)
        payload = resp.json() if resp.content else {}
        ok = bool(resp.status_code == 200 and payload.get("ok"))
        result = payload.get("result") if isinstance(payload, dict) else {}
        return {
            "ok": ok,
            "status_code": resp.status_code,
            "result": result if isinstance(result, dict) else {},
            "error": "" if ok else str(payload.get("description") or "").strip(),
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc), "result": {}}


async def delete_webhook(token: str, drop_pending_updates: bool = True) -> Dict[str, Any]:
    probe = str(token or "").strip()
    if not probe:
        return {"ok": False, "error": "missing token"}

    url = f"{_API_BASE}/bot{probe}/deleteWebhook"
    body = {"drop_pending_updates": bool(drop_pending_updates)}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json=body)
        payload = resp.json() if resp.content else {}
        ok = bool(resp.status_code == 200 and payload.get("ok"))
        return {
            "ok": ok,
            "status_code": resp.status_code,
            "result": payload if isinstance(payload, dict) else {},
            "error": "" if ok else str(payload.get("description") or "").strip(),
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


async def set_webhook(token: str, url: str) -> Dict[str, Any]:
    probe = str(token or "").strip()
    webhook_url = str(url or "").strip()
    if not probe:
        return {"ok": False, "error": "missing token"}
    if not webhook_url:
        return {"ok": False, "error": "missing webhook url"}

    endpoint = f"{_API_BASE}/bot{probe}/setWebhook"
    payload = {"url": webhook_url}
    secret = str(os.getenv("TELEGRAM_SECRET_TOKEN") or "").strip()
    if secret:
        payload["secret_token"] = secret
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(endpoint, json=payload)
        body = resp.json() if resp.content else {}
        ok = bool(resp.status_code == 200 and body.get("ok"))
        return {
            "ok": ok,
            "status_code": resp.status_code,
            "result": body if isinstance(body, dict) else {},
            "error": "" if ok else str(body.get("description") or "").strip(),
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


async def ensure_telegram_mode(logger: logging.Logger | None = None) -> Dict[str, Any]:
    log = logger or _LOG
    state = resolve_telegram_mode()
    mode = str(state["mode"])
    token = telegram_token()

    if state.get("legacy_polling"):
        log.warning(
            "ENABLE_TELEGRAM_POLLING está deprecado; usa TELEGRAM_MODE=polling|webhook|off."
        )

    outcome: Dict[str, Any] = {
        **state,
        "ok": True,
        "action": "noop",
        "webhook_info": {},
    }

    if mode == "off":
        log.info("Telegram mode=off (sin acciones).")
        return outcome

    if not token:
        outcome["ok"] = False
        outcome["error"] = "Telegram token no configurado."
        log.warning("Telegram mode=%s pero no hay token.", mode)
        return outcome

    if mode == "polling":
        log.info("Telegram mode=polling: deshabilitando webhook antes de getUpdates.")
        deleted = await delete_webhook(token, drop_pending_updates=True)
        if not deleted.get("ok"):
            log.error("Telegram deleteWebhook falló: %s", deleted.get("error") or "unknown")
            outcome["ok"] = False
        info = await get_webhook_info(token)
        outcome.update(
            {
                "action": "delete_webhook",
                "delete_webhook": deleted,
                "webhook_info": info.get("result") if isinstance(info.get("result"), dict) else {},
            }
        )
        return outcome

    if mode == "webhook":
        webhook_url = str(state.get("webhook_url") or "").strip()
        if not webhook_url:
            raise RuntimeError(
                "[TELEGRAM_MODE] mode=webhook requiere TELEGRAM_WEBHOOK_URL configurada."
            )
        log.info("Telegram mode=webhook: registrando webhook %s", webhook_url)
        configured = await set_webhook(token, webhook_url)
        if not configured.get("ok"):
            log.error("Telegram setWebhook falló: %s", configured.get("error") or "unknown")
            outcome["ok"] = False
        info = await get_webhook_info(token)
        outcome.update(
            {
                "action": "set_webhook",
                "set_webhook": configured,
                "webhook_info": info.get("result") if isinstance(info.get("result"), dict) else {},
            }
        )
        return outcome

    outcome["ok"] = False
    outcome["error"] = f"Modo inválido: {mode}"
    log.error("Telegram mode inválido: %s", mode)
    return outcome

