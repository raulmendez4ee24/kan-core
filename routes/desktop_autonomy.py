import os

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from brain.autonomy_web_research import run_desktop_autonomy_with_research
from brain.desktop_autonomous_loop import get_autonomy_run, run_desktop_autonomy_loop
from database import get_session
from schemas.desktop_autonomy import (
    DesktopAutonomyResearchRunRequest,
    DesktopAutonomyResearchRunResponse,
    DesktopAutonomyRunRequest,
    DesktopAutonomyRunResponse,
)
from security import require_client_token

router = APIRouter(prefix="/desktop-autonomy", tags=["desktop-autonomy"])


def _is_enabled() -> bool:
    return os.getenv("ENABLE_DESKTOP_AUTONOMY_LOOP", "true").lower() in {"1", "true", "yes"}


@router.post(
    "/run",
    response_model=DesktopAutonomyRunResponse,
    summary="Run desktop autonomy (API REST)",
    description=(
        "Executes the desktop autonomy loop for the given goal (same as Telegram flow). "
        "Requires runner connected for this client_id; returns status=runner_offline with instructions if not. "
        "Auth: X-Client-Id + X-Client-Token (or CLIENT_TOKENS_JSON)."
    ),
)
async def run_desktop_autonomy(
    req: DesktopAutonomyRunRequest,
    client_id: str = Depends(require_client_token),
    session: AsyncSession = Depends(get_session),
):
    if not _is_enabled():
        raise HTTPException(status_code=404, detail="Feature ENABLE_DESKTOP_AUTONOMY_LOOP disabled")
    try:
        return await run_desktop_autonomy_loop(
            client_id=client_id,
            request=req,
            session=session,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get(
    "/runs/{run_id}",
    response_model=DesktopAutonomyRunResponse,
    summary="Get desktop autonomy run by ID",
)
async def get_desktop_autonomy_run(
    run_id: str,
    client_id: str = Depends(require_client_token),
):
    _ = client_id
    if not _is_enabled():
        raise HTTPException(status_code=404, detail="Feature ENABLE_DESKTOP_AUTONOMY_LOOP disabled")
    run = get_autonomy_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return run


@router.post("/research-run", response_model=DesktopAutonomyResearchRunResponse)
async def run_desktop_autonomy_research(
    req: DesktopAutonomyResearchRunRequest,
    client_id: str = Depends(require_client_token),
    session: AsyncSession = Depends(get_session),
):
    if not _is_enabled():
        raise HTTPException(status_code=404, detail="Feature ENABLE_DESKTOP_AUTONOMY_LOOP disabled")
    try:
        return await run_desktop_autonomy_with_research(
            client_id=client_id,
            req=req,
            session=session,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
