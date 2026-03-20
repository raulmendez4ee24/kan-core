import os
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from brain.crm_engine import run_followups
from database import get_session
from schemas.crm import FollowUpJobRead, FollowUpsRunRequest, FollowUpsRunResponse
from security import require_client_token

router = APIRouter(prefix="/predictive", tags=["predictive"])


def _is_enabled(flag: str, default: str = "true") -> bool:
    return os.getenv(flag, default).lower() in {"1", "true", "yes"}


def _parse_uuid(raw: str, *, field: str) -> UUID:
    try:
        return UUID(raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid {field}") from exc


@router.post("/followups/run", response_model=FollowUpsRunResponse)
async def predictive_run_followups(
    req: FollowUpsRunRequest,
    client_id: str = Depends(require_client_token),
    session: AsyncSession = Depends(get_session),
):
    if not _is_enabled("ENABLE_CRM_INTERNAL"):
        raise HTTPException(status_code=404, detail="Feature ENABLE_CRM_INTERNAL disabled")
    if not _is_enabled("ENABLE_PREDICTIVE_AUTOMATION", "false"):
        raise HTTPException(status_code=404, detail="Feature ENABLE_PREDICTIVE_AUTOMATION disabled")

    org_id = _parse_uuid(client_id, field="client_id")
    result = await run_followups(
        session,
        organization_id=org_id,
        max_jobs=req.max_jobs,
        dry_run=req.dry_run,
        predictive=True,
    )

    jobs = [
        FollowUpJobRead(
            id=str(job.id),
            lead_id=str(job.lead_id) if job.lead_id else None,
            status=job.status,
            scheduled_for=job.scheduled_for,
            executed_at=job.executed_at,
            decision_reason=job.decision_reason,
            payload=job.payload or {},
            result=job.result or {},
        )
        for job in result.get("jobs", [])
    ]
    return FollowUpsRunResponse(
        jobs=jobs,
        executed=int(result.get("executed", 0)),
        skipped=int(result.get("skipped", 0)),
    )

