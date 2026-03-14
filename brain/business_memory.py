from __future__ import annotations

from typing import Any, Dict, List
from uuid import UUID

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from models import BusinessStateSnapshot, OperationMemoryEntry


async def get_business_memory(
    session: AsyncSession,
    *,
    organization_id: UUID,
    limit: int = 5,
) -> Dict[str, Any]:
    snapshots = list(
        (
            await session.execute(
                select(BusinessStateSnapshot)
                .where(BusinessStateSnapshot.organization_id == organization_id)
                .order_by(desc(BusinessStateSnapshot.created_at))
                .limit(max(1, min(limit, 20)))
            )
        )
        .scalars()
        .all()
    )
    recent_ops = list(
        (
            await session.execute(
                select(OperationMemoryEntry)
                .where(OperationMemoryEntry.organization_id == organization_id)
                .order_by(desc(OperationMemoryEntry.created_at))
                .limit(max(1, min(limit, 20)))
            )
        )
        .scalars()
        .all()
    )
    return {
        "recent_business_state": [dict(item.state_payload or {}) for item in snapshots],
        "recent_operation_outcomes": [dict(item.payload or {}) for item in recent_ops],
    }
