from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from brain.business_monitor import ingest_business_event
from models import CustomerProfile, OmnichannelMessage, OmnichannelThread
from tools.n8n_client import send_to_n8n_with_response


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _get_or_create_profile(
    session: AsyncSession,
    *,
    organization_id: UUID,
    customer_id: str,
    channel: str,
    name: Optional[str],
    email: Optional[str],
    phone: Optional[str],
) -> CustomerProfile:
    stmt = select(CustomerProfile).where(
        CustomerProfile.organization_id == organization_id,
        CustomerProfile.customer_id == customer_id,
    )
    row = (await session.execute(stmt)).scalars().first()
    if row:
        if name and not row.name:
            row.name = name
        if email and not row.email:
            row.email = email
        if phone and not row.phone:
            row.phone = phone
        row.preferred_channel = row.preferred_channel or channel
        await session.commit()
        await session.refresh(row)
        return row

    row = CustomerProfile(
        organization_id=organization_id,
        customer_id=customer_id,
        name=name,
        email=email,
        phone=phone,
        preferred_channel=channel,
        tags={},
        profile_metadata={},
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def _get_or_create_thread(
    session: AsyncSession,
    *,
    organization_id: UUID,
    customer_id: str,
    profile_id: Optional[UUID],
    channel: str,
) -> OmnichannelThread:
    stmt = (
        select(OmnichannelThread)
        .where(
            OmnichannelThread.organization_id == organization_id,
            OmnichannelThread.customer_id == customer_id,
            OmnichannelThread.status == "open",
        )
        .order_by(desc(OmnichannelThread.updated_at))
        .limit(1)
    )
    row = (await session.execute(stmt)).scalars().first()
    if row:
        if profile_id and not row.customer_profile_id:
            row.customer_profile_id = profile_id
        row.primary_channel = row.primary_channel or channel
        await session.commit()
        await session.refresh(row)
        return row

    row = OmnichannelThread(
        organization_id=organization_id,
        customer_profile_id=profile_id,
        customer_id=customer_id,
        primary_channel=channel,
        status="open",
        subject=None,
        last_message_at=_now(),
        thread_metadata={},
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def ingest_message(
    session: AsyncSession,
    *,
    organization_id: UUID,
    channel: str,
    customer_id: str,
    message: str,
    external_message_id: Optional[str] = None,
    customer_name: Optional[str] = None,
    customer_email: Optional[str] = None,
    customer_phone: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Tuple[OmnichannelThread, OmnichannelMessage]:
    profile = await _get_or_create_profile(
        session,
        organization_id=organization_id,
        customer_id=customer_id,
        channel=channel,
        name=customer_name,
        email=customer_email,
        phone=customer_phone,
    )
    thread = await _get_or_create_thread(
        session,
        organization_id=organization_id,
        customer_id=customer_id,
        profile_id=profile.id,
        channel=channel,
    )

    row = OmnichannelMessage(
        organization_id=organization_id,
        thread_id=thread.id,
        customer_id=customer_id,
        channel=channel,
        direction="inbound",
        external_message_id=external_message_id,
        body=message,
        status="received",
        message_metadata=dict(metadata or {}),
    )
    session.add(row)
    thread.last_message_at = row.created_at or _now()
    await session.commit()
    await session.refresh(row)
    await session.refresh(thread)

    await ingest_business_event(
        session,
        organization_id=organization_id,
        event_type="message_in",
        source="omnichannel.ingest",
        channel=channel,
        entity_id=customer_id,
        payload={"thread_id": str(thread.id), "message_id": str(row.id)},
    )
    return thread, row


async def reply_message(
    session: AsyncSession,
    *,
    organization_id: UUID,
    thread_id: UUID,
    channel: str,
    message: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> OmnichannelMessage:
    thread = await session.get(OmnichannelThread, thread_id)
    if not thread or thread.organization_id != organization_id:
        raise ValueError("Thread not found")

    row = OmnichannelMessage(
        organization_id=organization_id,
        thread_id=thread.id,
        customer_id=thread.customer_id,
        channel=channel or thread.primary_channel,
        direction="outbound",
        external_message_id=None,
        body=message,
        status="sent",
        message_metadata=dict(metadata or {}),
    )
    session.add(row)
    thread.last_message_at = _now()
    await session.commit()
    await session.refresh(row)
    await session.refresh(thread)

    await send_to_n8n_with_response(
        {
            "source": "omnichannel.reply",
            "organization_id": str(organization_id),
            "thread_id": str(thread.id),
            "customer_id": thread.customer_id,
            "channel": row.channel,
            "message": row.body,
            "metadata": row.message_metadata,
        }
    )
    await ingest_business_event(
        session,
        organization_id=organization_id,
        event_type="message_out",
        source="omnichannel.reply",
        channel=row.channel,
        entity_id=thread.customer_id,
        payload={"thread_id": str(thread.id), "message_id": str(row.id)},
    )
    return row


async def list_threads(
    session: AsyncSession,
    *,
    organization_id: UUID,
    limit: int = 50,
) -> List[OmnichannelThread]:
    stmt = (
        select(OmnichannelThread)
        .where(OmnichannelThread.organization_id == organization_id)
        .order_by(desc(OmnichannelThread.last_message_at), desc(OmnichannelThread.updated_at))
        .limit(max(1, min(limit, 500)))
    )
    return list((await session.execute(stmt)).scalars().all())


async def get_last_message(
    session: AsyncSession,
    *,
    organization_id: UUID,
    thread_id: UUID,
) -> Optional[OmnichannelMessage]:
    stmt = (
        select(OmnichannelMessage)
        .where(
            OmnichannelMessage.organization_id == organization_id,
            OmnichannelMessage.thread_id == thread_id,
        )
        .order_by(desc(OmnichannelMessage.created_at))
        .limit(1)
    )
    return (await session.execute(stmt)).scalars().first()


async def get_customer_profile(
    session: AsyncSession,
    *,
    organization_id: UUID,
    customer_id: str,
) -> Tuple[Optional[CustomerProfile], int, int]:
    profile = (
        await session.execute(
            select(CustomerProfile).where(
                CustomerProfile.organization_id == organization_id,
                CustomerProfile.customer_id == customer_id,
            )
        )
    ).scalars().first()
    threads_count = int(
        (
            await session.execute(
                select(func.count(OmnichannelThread.id)).where(
                    OmnichannelThread.organization_id == organization_id,
                    OmnichannelThread.customer_id == customer_id,
                )
            )
        ).scalar()
        or 0
    )
    messages_count = int(
        (
            await session.execute(
                select(func.count(OmnichannelMessage.id)).where(
                    OmnichannelMessage.organization_id == organization_id,
                    OmnichannelMessage.customer_id == customer_id,
                )
            )
        ).scalar()
        or 0
    )
    return profile, threads_count, messages_count
