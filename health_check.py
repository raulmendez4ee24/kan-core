from __future__ import annotations

import asyncio
import gc
import os
import sys
from dataclasses import dataclass
from typing import Optional

import httpx

from config.env_loader import is_placeholder
from config.env_loader import load_environment
from desktop_agent.screen_capture import take_screenshot
from jarvis_listener import _list_audio_devices


@dataclass
class CheckResult:
    name: str
    ok: bool
    critical: bool
    detail: str


def _has_env_value(key: str) -> bool:
    value = str(os.getenv(key) or "").strip()
    return bool(value and not is_placeholder(value))


def _env_bool(name: str, default: str = "false") -> bool:
    return str(os.getenv(name, default)).strip().lower() in {"1", "true", "yes"}


def _check_required_env() -> list[CheckResult]:
    out: list[CheckResult] = []
    required = ("JARVIS_CLIENT_ID", "OPENAI_API_KEY")
    for key in required:
        ok = _has_env_value(key)
        out.append(
            CheckResult(
                name=f"env:{key}",
                ok=ok,
                critical=True,
                detail="ok" if ok else "missing or placeholder",
            )
        )
    if _env_bool("ENABLE_TELEGRAM_POLLING", "false"):
        telegram_ok = _has_env_value("TELEGRAM_BOT_TOKEN") or _has_env_value("TELEGRAM_TOKEN")
        out.append(
            CheckResult(
                name="env:TELEGRAM_BOT_TOKEN|TELEGRAM_TOKEN",
                ok=telegram_ok,
                critical=True,
                detail="ok" if telegram_ok else "missing both telegram tokens",
            )
        )
    return out


def _check_microphone() -> CheckResult:
    try:
        devices = _list_audio_devices()
        if not devices:
            return CheckResult("hardware:microphone", False, True, "no input devices found")
        return CheckResult(
            "hardware:microphone",
            True,
            True,
            f"{len(devices)} input device(s) available",
        )
    except Exception as exc:
        return CheckResult("hardware:microphone", False, True, f"error: {exc}")


def _check_screen_capture() -> CheckResult:
    shot: Optional[dict] = None
    try:
        shot = take_screenshot(max_width=800)
        image_base64 = str((shot or {}).get("image_base64") or "")
        ok = bool(image_base64)
        return CheckResult(
            "hardware:screen_capture",
            ok,
            True,
            "capture ok" if ok else "capture returned empty image",
        )
    except Exception as exc:
        return CheckResult("hardware:screen_capture", False, True, f"error: {exc}")
    finally:
        if isinstance(shot, dict):
            shot.clear()
        shot = None
        gc.collect()


async def _check_openai() -> CheckResult:
    if not _has_env_value("OPENAI_API_KEY"):
        return CheckResult("api:openai", False, True, "OPENAI_API_KEY missing")
    base_url = str(os.getenv("OPENAI_API_BASE") or "https://api.openai.com/v1").rstrip("/")
    url = f"{base_url}/models"
    headers = {"Authorization": f"Bearer {os.getenv('OPENAI_API_KEY', '').strip()}"}
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(url, headers=headers)
        ok = resp.status_code == 200
        return CheckResult("api:openai", ok, True, f"status={resp.status_code}")
    except Exception as exc:
        return CheckResult("api:openai", False, True, f"error: {exc}")


async def _check_elevenlabs() -> CheckResult:
    if not _has_env_value("ELEVENLABS_API_KEY"):
        return CheckResult("api:elevenlabs", True, False, "skipped (key not configured)")
    headers = {"xi-api-key": str(os.getenv("ELEVENLABS_API_KEY") or "").strip()}
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get("https://api.elevenlabs.io/v1/user", headers=headers)
        ok = resp.status_code == 200
        return CheckResult("api:elevenlabs", ok, False, f"status={resp.status_code}")
    except Exception as exc:
        return CheckResult("api:elevenlabs", False, False, f"error: {exc}")


async def _check_n8n() -> CheckResult:
    base = str(os.getenv("N8N_API_URL") or "").strip()
    if not base:
        return CheckResult("api:n8n", True, False, "skipped (N8N_API_URL not configured)")
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(base.rstrip("/"))
        ok = resp.status_code < 500
        return CheckResult("api:n8n", ok, False, f"status={resp.status_code}")
    except Exception as exc:
        return CheckResult("api:n8n", False, False, f"error: {exc}")


async def _check_telegram_api() -> CheckResult:
    if not _env_bool("ENABLE_TELEGRAM_POLLING", "false"):
        return CheckResult("api:telegram", True, False, "skipped (polling disabled)")
    token = str(os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN") or "").strip()
    if not token:
        return CheckResult("api:telegram", False, True, "telegram token missing")
    url = f"https://api.telegram.org/bot{token}/getMe"
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(url)
        ok = resp.status_code == 200 and bool((resp.json() or {}).get("ok"))
        return CheckResult("api:telegram", ok, True, f"status={resp.status_code}")
    except Exception as exc:
        return CheckResult("api:telegram", False, True, f"error: {exc}")


async def _run_async_checks() -> list[CheckResult]:
    openai, elevenlabs, n8n, telegram = await asyncio.gather(
        _check_openai(),
        _check_elevenlabs(),
        _check_n8n(),
        _check_telegram_api(),
    )
    return [openai, elevenlabs, n8n, telegram]


def _print_results(results: list[CheckResult]) -> None:
    print("K'an Health Check")
    print("=================")
    for item in results:
        status = "OK" if item.ok else "FAIL"
        crit = "critical" if item.critical else "optional"
        print(f"[{status}] {item.name} ({crit}) - {item.detail}")


def _exit_code(results: list[CheckResult]) -> int:
    for item in results:
        if item.critical and not item.ok:
            return 1
    return 0


async def _amain() -> int:
    load_environment(context="health_check")
    results: list[CheckResult] = []
    results.extend(_check_required_env())
    results.append(_check_microphone())
    results.append(_check_screen_capture())
    results.extend(await _run_async_checks())
    _print_results(results)
    return _exit_code(results)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_amain()))
