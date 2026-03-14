import os
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from brain.analytics_engine import get_insight, run_insight_generation, upload_dataset
from database import get_session
from schemas.analytics_ai import (
    AnalyticsInsightRead,
    AnalyticsRunRequest,
    AnalyticsUploadRead,
    AnalyticsUploadRequest,
)
from security import require_client_token

router = APIRouter(prefix="/analytics", tags=["analytics-ai"])


def _enabled() -> bool:
    return os.getenv("ENABLE_ANALYTICS_AI", "true").lower() in {"1", "true", "yes"}


def _parse_uuid(raw: str, *, field: str) -> UUID:
    try:
        return UUID(raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid {field}") from exc


@router.post("/uploads", response_model=AnalyticsUploadRead)
async def analytics_upload(
    req: AnalyticsUploadRequest,
    client_id: str = Depends(require_client_token),
    session: AsyncSession = Depends(get_session),
):
    if not _enabled():
        raise HTTPException(status_code=404, detail="Feature ENABLE_ANALYTICS_AI disabled")
    org_id = _parse_uuid(client_id, field="client_id")
    row = await upload_dataset(
        session,
        organization_id=org_id,
        file_name=req.file_name,
        mime_type=req.mime_type,
        rows=req.rows,
        metadata=req.metadata,
    )
    return AnalyticsUploadRead(
        id=str(row.id),
        file_name=row.file_name,
        mime_type=row.mime_type,
        rows_count=int(row.rows_count or 0),
        status=row.status,
        created_at=row.created_at,
    )


@router.post("/insights/run", response_model=AnalyticsInsightRead)
async def analytics_run(
    req: AnalyticsRunRequest,
    client_id: str = Depends(require_client_token),
    session: AsyncSession = Depends(get_session),
):
    if not _enabled():
        raise HTTPException(status_code=404, detail="Feature ENABLE_ANALYTICS_AI disabled")
    org_id = _parse_uuid(client_id, field="client_id")
    upload_id = _parse_uuid(req.upload_id, field="upload_id") if req.upload_id else None
    try:
        row = await run_insight_generation(
            session,
            organization_id=org_id,
            upload_id=upload_id,
            objective=req.objective,
            metadata=req.metadata,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return AnalyticsInsightRead(
        id=str(row.id),
        upload_id=(str(row.upload_id) if row.upload_id else None),
        status=row.status,
        summary=row.summary,
        metrics=row.metrics or {},
        recommendations=row.recommendations or {},
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.get("/insights/{insight_id}", response_model=AnalyticsInsightRead)
async def analytics_get_insight(
    insight_id: str,
    client_id: str = Depends(require_client_token),
    session: AsyncSession = Depends(get_session),
):
    if not _enabled():
        raise HTTPException(status_code=404, detail="Feature ENABLE_ANALYTICS_AI disabled")
    org_id = _parse_uuid(client_id, field="client_id")
    insight_uuid = _parse_uuid(insight_id, field="insight_id")
    row = await get_insight(session, organization_id=org_id, insight_id=insight_uuid)
    if not row:
        raise HTTPException(status_code=404, detail="Insight not found")
    return AnalyticsInsightRead(
        id=str(row.id),
        upload_id=(str(row.upload_id) if row.upload_id else None),
        status=row.status,
        summary=row.summary,
        metrics=row.metrics or {},
        recommendations=row.recommendations or {},
        created_at=row.created_at,
        updated_at=row.updated_at,
    )

