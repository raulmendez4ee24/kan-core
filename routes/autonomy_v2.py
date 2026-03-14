import os
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from brain.autonomy_v2_engine import get_autonomy_v2_benchmarks, run_autonomy_v2
from database import get_session
from schemas.autonomy_v2 import (
    AutonomyV2BenchmarkItem,
    AutonomyV2RunRequest,
    AutonomyV2RunResponse,
)
from security import require_client_token

router = APIRouter(prefix="/autonomy/v2", tags=["autonomy-v2"])


def _enabled() -> bool:
    return os.getenv("ENABLE_DESKTOP_AUTONOMY_LOOP", "true").lower() in {"1", "true", "yes"}


@router.post("/run", response_model=AutonomyV2RunResponse)
async def autonomy_v2_run(
    req: AutonomyV2RunRequest,
    client_id: str = Depends(require_client_token),
    session: AsyncSession = Depends(get_session),
):
    if not _enabled():
        raise HTTPException(status_code=404, detail="Feature ENABLE_DESKTOP_AUTONOMY_LOOP disabled")
    try:
        return await run_autonomy_v2(client_id=client_id, req=req, session=session)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/benchmarks", response_model=list[AutonomyV2BenchmarkItem])
async def autonomy_v2_benchmarks(
    limit: int = 25,
    order_by: str = "reliability",
    descending: bool = True,
    client_id: str = Depends(require_client_token),
    session: AsyncSession = Depends(get_session),
):
    try:
        org_id = UUID(client_id)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="Invalid client id") from exc
    return await get_autonomy_v2_benchmarks(
        session=session,
        organization_id=org_id,
        limit=limit,
        order_by=order_by,
        descending=descending,
    )
