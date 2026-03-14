import os
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from brain.business_context_engine import run_business_context_cycle
from database import get_session
from schemas.business_context import BusinessContextRunRequest, BusinessContextRunResponse
from security import require_client_token

router = APIRouter(prefix="/optimizer/context", tags=["business-context"])


def _is_enabled() -> bool:
    return os.getenv("ENABLE_BUSINESS_CONTEXT_REASONING", "false").lower() in {"1", "true", "yes"}


def _parse_client_uuid(client_id: str) -> UUID:
    try:
        return UUID(client_id)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="Invalid client id") from exc


@router.post("/run", response_model=BusinessContextRunResponse)
async def run_business_context(
    req: BusinessContextRunRequest,
    client_id: str = Depends(require_client_token),
    session: AsyncSession = Depends(get_session),
):
    if not _is_enabled():
        raise HTTPException(status_code=404, detail="Feature ENABLE_BUSINESS_CONTEXT_REASONING disabled")

    org_id = _parse_client_uuid(client_id)
    result = await run_business_context_cycle(
        session,
        organization_id=org_id,
        window_hours=req.window_hours,
        min_prev_sales=req.min_prev_sales,
        sales_drop_threshold=req.sales_drop_threshold,
        create_recommendations=req.create_recommendations,
        recommendation_cooldown_hours=req.recommendation_cooldown_hours,
        execute_campaigns=req.execute_campaigns,
        followups_max_jobs=req.followups_max_jobs,
        followups_dry_run=req.followups_dry_run,
    )
    return BusinessContextRunResponse(**result)

