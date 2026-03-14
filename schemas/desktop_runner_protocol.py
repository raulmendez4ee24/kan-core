"""
Unified desktop runner protocol: Cloud -> Runner and Runner -> Cloud.

Cloud -> Runner (execute):
  { type: "execute", run_id, step_id, action, args, timeout_s }
  Actions: open_app, goto, click, fill, press, hotkey, scroll, screenshot, extract, wait

Runner -> Cloud (result):
  { type: "result", run_id, step_id, status, summary, data, evidence: { screenshot_b64|path }, current_url?, active_app? }
"""
from __future__ import annotations

from typing import Any, Dict, Optional

# Cloud -> Runner: execute command
EXECUTE_ACTIONS = [
    "open_app",      # open_app(app_name)
    "goto",         # goto(url)
    "click",        # click(selector|coords|text)
    "fill",         # fill(selector|coords|text, value)
    "press",        # press(key)
    "hotkey",       # hotkey(keys)
    "scroll",       # scroll(amount)
    "screenshot",   # screenshot()
    "extract",      # extract(text|selector|region)
    "wait",         # wait(seconds)
]

# Mapped to server/runner legacy names in runner/local_runner.py (_map_action)
LEGACY_ACTION_MAP = {
    "open_app": "open_program",
    "goto": "browser_goto",
    "click": "click",  # or browser_click
    "fill": "browser_fill",
    "press": "press_key",
    "hotkey": "press_key",
    "scroll": "browser_press",  # or custom
    "screenshot": "take_screenshot",
    "extract": "browser_extract",
    "wait": "wait",
}


def build_execute_message(
    *,
    run_id: str,
    step_id: str,
    action: str,
    args: Optional[Dict[str, Any]] = None,
    timeout_s: Optional[int] = None,
) -> Dict[str, Any]:
    """Build Cloud -> Runner execute message."""
    payload: Dict[str, Any] = {
        "type": "execute",
        "run_id": run_id,
        "step_id": step_id,
        "action": action,
        "args": dict(args or {}),
    }
    if timeout_s is not None:
        payload["timeout_s"] = int(timeout_s)
    return payload


def parse_result_message(data: Dict[str, Any]) -> Dict[str, Any]:
    """Parse Runner -> Cloud result; normalize evidence (screenshot_b64/path), current_url, active_app."""
    evidence = dict(data.get("evidence") or {})
    if "screenshot_b64" not in evidence and isinstance(data.get("data"), dict):
        ev = (data.get("data") or {}).get("evidence") or (data.get("data") or {}).get("screenshot_base64")
        if ev:
            evidence["screenshot_b64"] = ev
    return {
        "type": "result",
        "run_id": str(data.get("run_id") or ""),
        "step_id": str(data.get("step_id") or data.get("command_id") or ""),
        "status": str(data.get("status") or "failed").strip().lower(),
        "summary": str(data.get("summary") or ""),
        "data": dict(data.get("data") or {}),
        "evidence": evidence,
        "current_url": data.get("current_url") or (data.get("data") or {}).get("current_url"),
        "active_app": data.get("active_app") or (data.get("data") or {}).get("active_app"),
        "duration_ms": data.get("duration_ms"),
        "error": data.get("error"),
    }
