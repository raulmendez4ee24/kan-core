import os
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from brain.recruiting_engine import (
    import_candidates,
    ranking_by_job,
    schedule_interview,
    screen_candidate,
)
from database import get_session
from schemas.recruiting import (
    RecruitingCandidatesImportRequest,
    RecruitingCandidateRead,
    RecruitingImportResponse,
    RecruitingInterviewRead,
    RecruitingInterviewScheduleRequest,
    RecruitingRankingResponse,
    RecruitingScreenRequest,
    RecruitingScreenResponse,
)
from security import require_client_token

router = APIRouter(prefix="/recruiting", tags=["recruiting"])


def _enabled() -> bool:
    return os.getenv("ENABLE_RECRUITING_AI", "true").lower() in {"1", "true", "yes"}


def _parse_uuid(raw: str, *, field: str) -> UUID:
    try:
        return UUID(raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid {field}") from exc


def _to_candidate_read(row) -> RecruitingCandidateRead:
    return RecruitingCandidateRead(
        id=str(row.id),
        job_id=str(row.job_id),
        name=str(row.name),
        email=row.email,
        phone=row.phone,
        source=row.source,
        score=float(row.score or 0.0),
        status=str(row.status),
        metadata=row.candidate_metadata or {},
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _to_interview_read(row) -> RecruitingInterviewRead:
    return RecruitingInterviewRead(
        id=str(row.id),
        candidate_id=str(row.candidate_id),
        scheduled_at=row.scheduled_at,
        channel=str(row.channel),
        interviewer=row.interviewer,
        status=str(row.status),
        result=row.result or {},
        created_at=row.created_at,
    )


@router.post("/candidates/import", response_model=RecruitingImportResponse)
async def recruiting_import(
    req: RecruitingCandidatesImportRequest,
    client_id: str = Depends(require_client_token),
    session: AsyncSession = Depends(get_session),
):
    if not _enabled():
        raise HTTPException(status_code=404, detail="Feature ENABLE_RECRUITING_AI disabled")
    org_id = _parse_uuid(client_id, field="client_id")
    rows = await import_candidates(
        session,
        organization_id=org_id,
        job_id=req.job_id,
        candidates=[item.model_dump() for item in req.candidates],
    )
    return RecruitingImportResponse(
        job_id=req.job_id,
        imported=len(rows),
        candidates=[_to_candidate_read(row) for row in rows],
    )


@router.post("/candidates/{candidate_id}/screen", response_model=RecruitingScreenResponse)
async def recruiting_screen(
    candidate_id: str,
    req: RecruitingScreenRequest,
    client_id: str = Depends(require_client_token),
    session: AsyncSession = Depends(get_session),
):
    if not _enabled():
        raise HTTPException(status_code=404, detail="Feature ENABLE_RECRUITING_AI disabled")
    org_id = _parse_uuid(client_id, field="client_id")
    candidate_uuid = _parse_uuid(candidate_id, field="candidate_id")
    try:
        result = await screen_candidate(
            session,
            organization_id=org_id,
            candidate_id=candidate_uuid,
            must_have=req.must_have_keywords,
            nice_to_have=req.nice_to_have_keywords,
            reject=req.reject_keywords,
            metadata=req.metadata,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return RecruitingScreenResponse(**result)


@router.post("/interviews/schedule", response_model=RecruitingInterviewRead)
async def recruiting_schedule(
    req: RecruitingInterviewScheduleRequest,
    client_id: str = Depends(require_client_token),
    session: AsyncSession = Depends(get_session),
):
    if not _enabled():
        raise HTTPException(status_code=404, detail="Feature ENABLE_RECRUITING_AI disabled")
    org_id = _parse_uuid(client_id, field="client_id")
    candidate_uuid = _parse_uuid(req.candidate_id, field="candidate_id")
    try:
        row = await schedule_interview(
            session,
            organization_id=org_id,
            candidate_id=candidate_uuid,
            scheduled_at=req.scheduled_at,
            channel=req.channel,
            interviewer=req.interviewer,
            metadata=req.metadata,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _to_interview_read(row)


@router.get("/jobs/{job_id}/ranking", response_model=RecruitingRankingResponse)
async def recruiting_ranking(
    job_id: str,
    client_id: str = Depends(require_client_token),
    session: AsyncSession = Depends(get_session),
):
    if not _enabled():
        raise HTTPException(status_code=404, detail="Feature ENABLE_RECRUITING_AI disabled")
    org_id = _parse_uuid(client_id, field="client_id")
    rows = await ranking_by_job(session, organization_id=org_id, job_id=job_id)
    return RecruitingRankingResponse(
        job_id=job_id,
        candidates=[_to_candidate_read(row) for row in rows],
        generated_at=(rows[0].updated_at if rows and rows[0].updated_at else datetime.now(timezone.utc)),
    )
