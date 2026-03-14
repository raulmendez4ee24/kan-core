import os
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_session
from learning.reinforcement_optimizer import calibrate_org_learning_policy, refresh_learning_from_sources
from schemas.learning_ops import LearningOptimizeRunRequest, LearningOptimizeRunResponse
from security import require_client_token

router = APIRouter(prefix="/learning", tags=["learning"])


def _is_enabled() -> bool:
    return os.getenv("ENABLE_REINFORCEMENT_OPTIMIZER", "false").lower() in {"1", "true", "yes"}


def _parse_org_id(client_id: str) -> UUID:
    try:
        return UUID(client_id)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="Invalid client id") from exc


@router.post("/optimize/run", response_model=LearningOptimizeRunResponse)
async def run_learning_optimization(
    req: LearningOptimizeRunRequest,
    client_id: str = Depends(require_client_token),
    session: AsyncSession = Depends(get_session),
):
    if not _is_enabled():
        raise HTTPException(status_code=404, detail="Feature ENABLE_REINFORCEMENT_OPTIMIZER disabled")

    org_id = _parse_org_id(client_id)

    refreshed = {}
    if req.refresh_global:
        refreshed = await refresh_learning_from_sources(session)

    calibrated = {}
    if req.calibrate_org:
        calibrated = await calibrate_org_learning_policy(session, organization_id=org_id)

    return LearningOptimizeRunResponse(status="ok", refreshed=refreshed, calibrated=calibrated)

