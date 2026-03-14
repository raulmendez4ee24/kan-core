from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import ExecutionLease


async def acquire_case_lease(
    session: AsyncSession,
    *,
    organization_id: UUID,
    case_id: UUID,
    lease_key: str,
    owner: str,
    ttl_seconds: int = 60,
) -> bool:
    now = datetime.now(timezone.utc)
    leased_until = now + timedelta(seconds=max(5, int(ttl_seconds)))
    stmt = (
        select(ExecutionLease)
        .where(
            ExecutionLease.organization_id == organization_id,
            ExecutionLease.lease_key == lease_key,
        )
        .limit(1)
    )
    row = (await session.execute(stmt)).scalars().first()
    if row and row.leased_until and row.leased_until > now and row.owner != owner:
        return False
    if row:
        row.case_id = case_id
        row.owner = owner
        row.leased_until = leased_until
    else:
        row = ExecutionLease(
            organization_id=organization_id,
            case_id=case_id,
            lease_key=lease_key,
            owner=owner,
            leased_until=leased_until,
        )
        session.add(row)
    await session.commit()
    return True


async def release_case_lease(
    session: AsyncSession,
    *,
    organization_id: UUID,
    lease_key: str,
    owner: Optional[str] = None,
) -> None:
    stmt = (
        select(ExecutionLease)
        .where(
            ExecutionLease.organization_id == organization_id,
            ExecutionLease.lease_key == lease_key,
        )
        .limit(1)
    )
    row = (await session.execute(stmt)).scalars().first()
    if not row:
        return
    if owner and row.owner != owner:
        return
    await session.delete(row)
    await session.commit()
