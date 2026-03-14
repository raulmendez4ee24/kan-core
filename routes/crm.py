import os
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from brain.crm_engine import list_interactions, list_leads, patch_lead, run_followups, upsert_lead
from database import get_session
from schemas.crm import (
    FollowUpJobRead,
    FollowUpsRunRequest,
    FollowUpsRunResponse,
    InteractionRead,
    LeadPatchRequest,
    LeadRead,
    LeadUpsertRequest,
)
from security import require_client_token

router = APIRouter(tags=["crm"])


def _is_enabled(flag: str, default: str = "true") -> bool:
    return os.getenv(flag, default).lower() in {"1", "true", "yes"}


def _parse_uuid(raw: str, *, field: str) -> UUID:
    try:
        return UUID(raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid {field}") from exc


def _to_lead_read(row) -> LeadRead:
    tags = row.tags if isinstance(row.tags, dict) else {}
    return LeadRead(
        id=str(row.id),
        external_id=row.external_id,
        name=row.name,
        email=row.email,
        phone=row.phone,
        status=row.status,
        source=row.source,
        industry=row.industry,
        owner=row.owner,
        tags=[str(x) for x in tags.get("items", []) if str(x).strip()],
        metadata=row.lead_metadata or {},
        score=float(row.score or 0.0),
        conversion_probability=float(row.conversion_probability or 0.0),
        last_interaction_at=row.last_interaction_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _to_interaction_read(row) -> InteractionRead:
    return InteractionRead(
        id=str(row.id),
        lead_id=str(row.lead_id) if row.lead_id else None,
        channel=row.channel,
        direction=row.direction,
        content=row.content,
        metadata=row.interaction_metadata or {},
        created_at=row.created_at,
    )


@router.get("/crm/leads", response_model=List[LeadRead])
async def crm_list_leads(
    status: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    client_id: str = Depends(require_client_token),
    session: AsyncSession = Depends(get_session),
):
    if not _is_enabled("ENABLE_CRM_INTERNAL"):
        raise HTTPException(status_code=404, detail="Feature ENABLE_CRM_INTERNAL disabled")

    org_id = _parse_uuid(client_id, field="client_id")
    leads = await list_leads(session, organization_id=org_id, status=status, limit=limit)
    return [_to_lead_read(item) for item in leads]


@router.post("/crm/leads/upsert", response_model=LeadRead)
async def crm_upsert_lead(
    req: LeadUpsertRequest,
    client_id: str = Depends(require_client_token),
    session: AsyncSession = Depends(get_session),
):
    if not _is_enabled("ENABLE_CRM_INTERNAL"):
        raise HTTPException(status_code=404, detail="Feature ENABLE_CRM_INTERNAL disabled")

    org_id = _parse_uuid(client_id, field="client_id")
    lead = await upsert_lead(
        session,
        organization_id=org_id,
        payload=req.model_dump(exclude_none=True),
    )
    return _to_lead_read(lead)


@router.patch("/crm/leads/{lead_id}", response_model=LeadRead)
async def crm_patch_lead(
    lead_id: str,
    req: LeadPatchRequest,
    client_id: str = Depends(require_client_token),
    session: AsyncSession = Depends(get_session),
):
    if not _is_enabled("ENABLE_CRM_INTERNAL"):
        raise HTTPException(status_code=404, detail="Feature ENABLE_CRM_INTERNAL disabled")

    org_id = _parse_uuid(client_id, field="client_id")
    lead_uuid = _parse_uuid(lead_id, field="lead_id")
    row = await patch_lead(
        session,
        organization_id=org_id,
        lead_id=lead_uuid,
        payload=req.model_dump(exclude_none=True),
    )
    if not row:
        raise HTTPException(status_code=404, detail="Lead not found")
    return _to_lead_read(row)


@router.get("/crm/interactions", response_model=List[InteractionRead])
async def crm_list_interactions(
    lead_id: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    client_id: str = Depends(require_client_token),
    session: AsyncSession = Depends(get_session),
):
    if not _is_enabled("ENABLE_CRM_INTERNAL"):
        raise HTTPException(status_code=404, detail="Feature ENABLE_CRM_INTERNAL disabled")

    org_id = _parse_uuid(client_id, field="client_id")
    lead_uuid = _parse_uuid(lead_id, field="lead_id") if lead_id else None
    items = await list_interactions(
        session,
        organization_id=org_id,
        lead_id=lead_uuid,
        limit=limit,
    )
    return [_to_interaction_read(item) for item in items]


@router.post("/followups/run", response_model=FollowUpsRunResponse)
async def crm_run_followups(
    req: FollowUpsRunRequest,
    client_id: str = Depends(require_client_token),
    session: AsyncSession = Depends(get_session),
):
    if not _is_enabled("ENABLE_CRM_INTERNAL"):
        raise HTTPException(status_code=404, detail="Feature ENABLE_CRM_INTERNAL disabled")

    org_id = _parse_uuid(client_id, field="client_id")
    result = await run_followups(
        session,
        organization_id=org_id,
        max_jobs=req.max_jobs,
        dry_run=req.dry_run,
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
