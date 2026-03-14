import os
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from brain.process_audit_engine import create_audit_job, estimate_job_roi, get_audit_job
from database import get_session
from schemas.process_audit import (
    ProcessAuditFindingRead,
    ProcessAuditJobCreateRequest,
    ProcessAuditJobRead,
    ProcessAuditRoiResponse,
)
from security import require_client_token

router = APIRouter(prefix="/process-audit", tags=["process-audit"])


def _enabled() -> bool:
    return os.getenv("ENABLE_PROCESS_AUDIT", "true").lower() in {"1", "true", "yes"}


def _parse_uuid(raw: str, *, field: str) -> UUID:
    try:
        return UUID(raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid {field}") from exc


@router.post("/jobs", response_model=ProcessAuditJobRead)
async def process_audit_create(
    req: ProcessAuditJobCreateRequest,
    client_id: str = Depends(require_client_token),
    session: AsyncSession = Depends(get_session),
):
    if not _enabled():
        raise HTTPException(status_code=404, detail="Feature ENABLE_PROCESS_AUDIT disabled")
    org_id = _parse_uuid(client_id, field="client_id")
    row = await create_audit_job(
        session,
        organization_id=org_id,
        title=req.title,
        input_text=req.input_text,
        documents=req.documents,
        metadata=req.metadata,
    )
    _, findings = await get_audit_job(session, organization_id=org_id, job_id=row.id)
    return ProcessAuditJobRead(
        id=str(row.id),
        title=row.title,
        status=row.status,
        findings_summary=row.findings_summary,
        estimated_roi=row.estimated_roi,
        implementation_plan=row.implementation_plan or {},
        created_at=row.created_at,
        updated_at=row.updated_at,
        findings=[
            ProcessAuditFindingRead(
                id=str(item.id),
                finding_type=item.finding_type,
                severity=item.severity,
                description=item.description,
                estimated_impact=item.estimated_impact,
                recommendation=item.recommendation,
                created_at=item.created_at,
            )
            for item in findings
        ],
    )


@router.get("/jobs/{job_id}", response_model=ProcessAuditJobRead)
async def process_audit_get(
    job_id: str,
    client_id: str = Depends(require_client_token),
    session: AsyncSession = Depends(get_session),
):
    if not _enabled():
        raise HTTPException(status_code=404, detail="Feature ENABLE_PROCESS_AUDIT disabled")
    org_id = _parse_uuid(client_id, field="client_id")
    job_uuid = _parse_uuid(job_id, field="job_id")
    row, findings = await get_audit_job(session, organization_id=org_id, job_id=job_uuid)
    if not row:
        raise HTTPException(status_code=404, detail="Job not found")
    return ProcessAuditJobRead(
        id=str(row.id),
        title=row.title,
        status=row.status,
        findings_summary=row.findings_summary,
        estimated_roi=row.estimated_roi,
        implementation_plan=row.implementation_plan or {},
        created_at=row.created_at,
        updated_at=row.updated_at,
        findings=[
            ProcessAuditFindingRead(
                id=str(item.id),
                finding_type=item.finding_type,
                severity=item.severity,
                description=item.description,
                estimated_impact=item.estimated_impact,
                recommendation=item.recommendation,
                created_at=item.created_at,
            )
            for item in findings
        ],
    )


@router.get("/jobs/{job_id}/roi", response_model=ProcessAuditRoiResponse)
async def process_audit_roi(
    job_id: str,
    client_id: str = Depends(require_client_token),
    session: AsyncSession = Depends(get_session),
):
    if not _enabled():
        raise HTTPException(status_code=404, detail="Feature ENABLE_PROCESS_AUDIT disabled")
    org_id = _parse_uuid(client_id, field="client_id")
    job_uuid = _parse_uuid(job_id, field="job_id")
    try:
        payload = await estimate_job_roi(
            session,
            organization_id=org_id,
            job_id=job_uuid,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ProcessAuditRoiResponse(**payload)

