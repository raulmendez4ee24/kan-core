"""GET /console/doctor — autodiagnóstico (envs, OpenAI, Telegram, n8n, runner, DB)."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from brain.doctor import run_doctor_checks
from security import require_client_token

router = APIRouter(prefix="/console", tags=["console"])


@router.get("/doctor")
async def console_doctor(
    client_id: str = Depends(require_client_token),
):
    """Run doctor checks; returns structured JSON (env_effective, openai, telegram_send, n8n_webhook, cloud_vs_local, runner, loop_flags, db_issues)."""
    result = await run_doctor_checks(client_id=client_id)
    return result
