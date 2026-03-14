import os
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from brain.content_factory import generate_plan, publish_asset
from database import get_session
from schemas.content_factory import (
    ContentAssetRead,
    ContentPlanGenerateRequest,
    ContentPlanRead,
    ContentPublishRequest,
    ContentPublishResponse,
)
from security import require_client_token

router = APIRouter(prefix="/content", tags=["content-factory"])


def _enabled() -> bool:
    return os.getenv("ENABLE_CONTENT_FACTORY", "true").lower() in {"1", "true", "yes"}


def _parse_uuid(raw: str, *, field: str) -> UUID:
    try:
        return UUID(raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid {field}") from exc


@router.post("/plans/generate", response_model=ContentPlanRead)
async def content_generate_plan(
    req: ContentPlanGenerateRequest,
    client_id: str = Depends(require_client_token),
    session: AsyncSession = Depends(get_session),
):
    if not _enabled():
        raise HTTPException(status_code=404, detail="Feature ENABLE_CONTENT_FACTORY disabled")
    org_id = _parse_uuid(client_id, field="client_id")
    row = await generate_plan(
        session,
        organization_id=org_id,
        title=req.title,
        channel=req.channel,
        cadence=req.cadence,
        brief=req.brief,
        weeks=req.weeks,
        metadata=req.metadata,
    )
    return ContentPlanRead(
        id=str(row.id),
        title=row.title,
        channel=row.channel,
        cadence=row.cadence,
        brief=row.brief,
        plan=row.plan or {},
        status=row.status,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.post("/publish", response_model=ContentPublishResponse)
async def content_publish(
    req: ContentPublishRequest,
    client_id: str = Depends(require_client_token),
    session: AsyncSession = Depends(get_session),
):
    if not _enabled():
        raise HTTPException(status_code=404, detail="Feature ENABLE_CONTENT_FACTORY disabled")
    org_id = _parse_uuid(client_id, field="client_id")
    plan_uuid = _parse_uuid(req.plan_id, field="plan_id") if req.plan_id else None
    result = await publish_asset(
        session,
        organization_id=org_id,
        plan_id=plan_uuid,
        channel=req.channel,
        asset_type=req.asset_type,
        content=req.content,
        metadata=req.metadata,
    )
    row = result["asset"]
    return ContentPublishResponse(
        asset=ContentAssetRead(
            id=str(row.id),
            plan_id=(str(row.plan_id) if row.plan_id else None),
            asset_type=row.asset_type,
            channel=row.channel,
            content=row.content,
            metadata=row.asset_metadata or {},
            status=row.status,
            published_at=row.published_at,
            created_at=row.created_at,
        ),
        dispatched=bool(result.get("dispatched", False)),
    )

