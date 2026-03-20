"""
Desktop Action Loop: observe -> decide -> act -> verify.

Used when intent is desktop/mixed: ensures runner is online, then runs the
existing desktop autonomy loop (plan -> enqueue -> wait result). Optional
observe step (screenshot) can be used for vision-based decide/verify.
"""
from __future__ import annotations

import logging
from typing import Any, Dict

logger = logging.getLogger("kan_core.desktop_action_loop")


async def is_runner_online_for_client(client_id: str) -> bool:
    """Return True if a desktop runner is connected for this client_id."""
    try:
        from brain.doctor import _runner_connected
        result = await _runner_connected(client_id)
        return result.get("connected") is True
    except Exception:
        return False


async def run_desktop_action_loop(
    client_id: str,
    goal: str,
    *,
    max_steps: int = 8,
    step_timeout_seconds: float = 35.0,
) -> Dict[str, Any]:
    """
    Run desktop autonomy with immediate runner_offline if no runner.
    Delegates to run_desktop_autonomy_loop after runner check.
    """
    if not client_id:
        return {
            "status": "failed",
            "summary": "client_id requerido.",
            "error": "invalid_client_id",
            "data": {},
        }
    if not await is_runner_online_for_client(client_id):
        from execution.desktop_executor import runner_offline_instructions
        return {
            "status": "runner_offline",
            "summary": runner_offline_instructions(),
            "error": "runner_offline",
            "data": {},
        }
    from brain.desktop_autonomous_loop import run_desktop_autonomy_loop
    from schemas.desktop_autonomy import DesktopAutonomyRunRequest
    import os

    exec_mode = "autonomous" if (os.getenv("AUTONOMY_LEVEL") or "high").strip().lower() == "high" else "balanced"
    req = DesktopAutonomyRunRequest(
        goal=goal,
        max_steps=max(2, min(max_steps, 12)),
        execution_mode=exec_mode,
        context={"source": "desktop_action_loop", "allow_high_risk_auto_approval": exec_mode == "autonomous"},
        dry_run=False,
        wait_for_results=True,
        step_timeout_seconds=step_timeout_seconds,
        stop_on_failure=False,
        dynamic_risk_controls=False,
        enforce_quality_gate=False,
    )
    from database import AsyncSessionLocal
    async with AsyncSessionLocal() as s:
        response = await run_desktop_autonomy_loop(client_id=client_id, request=req, session=s)
    status = str(response.status or "failed")
    return {
        "status": status,
        "summary": str(response.summary or ""),
        "error": str(response.error or ""),
        "data": response.model_dump() if hasattr(response, "model_dump") else {},
    }
