import os
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from brain.omnichannel_engine import (
    get_customer_profile,
    get_last_message,
    ingest_message,
    list_threads,
    reply_message,
)
from database import get_session
from models import OmnichannelThread
from schemas.omnichannel import (
    CustomerProfileRead,
    OmnichannelInboxResponse,
    OmnichannelIngestRequest,
    OmnichannelMessageRead,
    OmnichannelReplyRequest,
    OmnichannelThreadRead,
)
from security import require_client_token

router = APIRouter(prefix="/omnichannel", tags=["omnichannel"])


def _enabled() -> bool:
    return os.getenv("ENABLE_OMNICHANNEL", "true").lower() in {"1", "true", "yes"}


def _parse_uuid(raw: str) -> UUID:
    try:
        return UUID(raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid client id") from exc


def _to_message_read(row) -> OmnichannelMessageRead:
    return OmnichannelMessageRead(
        id=str(row.id),
        thread_id=str(row.thread_id),
        customer_id=str(row.customer_id),
        channel=str(row.channel),
        direction=str(row.direction),
        body=str(row.body),
        status=str(row.status),
        metadata=row.message_metadata or {},
        created_at=row.created_at,
    )


def _to_thread_read(row: OmnichannelThread, last_message) -> OmnichannelThreadRead:
    return OmnichannelThreadRead(
        id=str(row.id),
        customer_id=str(row.customer_id),
        primary_channel=str(row.primary_channel),
        status=str(row.status),
        last_message_at=row.last_message_at,
        metadata=row.thread_metadata or {},
        created_at=row.created_at,
        updated_at=row.updated_at,
        last_message=(_to_message_read(last_message) if last_message else None),
    )


@router.post("/messages/ingest", response_model=OmnichannelMessageRead)
async def omnichannel_ingest(
    req: OmnichannelIngestRequest,
    client_id: str = Depends(require_client_token),
    session: AsyncSession = Depends(get_session),
):
    if not _enabled():
        raise HTTPException(status_code=404, detail="Feature ENABLE_OMNICHANNEL disabled")
    org_id = _parse_uuid(client_id)
    _, message = await ingest_message(
        session,
        organization_id=org_id,
        channel=req.channel,
        customer_id=req.customer_id,
        message=req.message,
        external_message_id=req.external_message_id,
        customer_name=req.customer_name,
        customer_email=req.customer_email,
        customer_phone=req.customer_phone,
        metadata=req.metadata,
    )
    return _to_message_read(message)


@router.get("/inbox", response_model=OmnichannelInboxResponse)
async def omnichannel_inbox(
    limit: int = Query(default=50, ge=1, le=500),
    client_id: str = Depends(require_client_token),
    session: AsyncSession = Depends(get_session),
):
    if not _enabled():
        raise HTTPException(status_code=404, detail="Feature ENABLE_OMNICHANNEL disabled")
    org_id = _parse_uuid(client_id)
    rows = await list_threads(session, organization_id=org_id, limit=limit)
    items = []
    for row in rows:
        msg = await get_last_message(session, organization_id=org_id, thread_id=row.id)
        items.append(_to_thread_read(row, msg))
    return OmnichannelInboxResponse(items=items)


@router.post("/reply", response_model=OmnichannelMessageRead)
async def omnichannel_reply(
    req: OmnichannelReplyRequest,
    client_id: str = Depends(require_client_token),
    session: AsyncSession = Depends(get_session),
):
    if not _enabled():
        raise HTTPException(status_code=404, detail="Feature ENABLE_OMNICHANNEL disabled")
    org_id = _parse_uuid(client_id)

    thread_id = None
    if req.thread_id:
        try:
            thread_id = UUID(req.thread_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid thread_id") from exc
    elif req.customer_id:
        rows = await list_threads(session, organization_id=org_id, limit=500)
        for row in rows:
            if row.customer_id == req.customer_id:
                thread_id = row.id
                break
    if not thread_id:
        raise HTTPException(status_code=404, detail="Thread not found")

    msg = await reply_message(
        session,
        organization_id=org_id,
        thread_id=thread_id,
        channel=req.channel,
        message=req.message,
        metadata=req.metadata,
    )
    return _to_message_read(msg)


@router.get("/customers/{customer_id}", response_model=CustomerProfileRead)
async def omnichannel_customer(
    customer_id: str,
    client_id: str = Depends(require_client_token),
    session: AsyncSession = Depends(get_session),
):
    if not _enabled():
        raise HTTPException(status_code=404, detail="Feature ENABLE_OMNICHANNEL disabled")
    org_id = _parse_uuid(client_id)
    profile, thread_count, message_count = await get_customer_profile(
        session,
        organization_id=org_id,
        customer_id=customer_id,
    )
    if not profile:
        raise HTTPException(status_code=404, detail="Customer not found")
    return CustomerProfileRead(
        id=str(profile.id),
        customer_id=str(profile.customer_id),
        name=profile.name,
        email=profile.email,
        phone=profile.phone,
        preferred_channel=profile.preferred_channel,
        tags=profile.tags or {},
        metadata=profile.profile_metadata or {},
        thread_count=thread_count,
        message_count=message_count,
        created_at=profile.created_at,
        updated_at=profile.updated_at,
    )

