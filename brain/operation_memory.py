from __future__ import annotations

from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from models import OperationMemoryEntry


async def record_operation_memory(
    session: AsyncSession,
    *,
    organization_id: UUID,
    case_id: Optional[UUID],
    memory_type: str,
    signature: str,
    payload: Dict[str, Any],
    success: Optional[bool],
) -> OperationMemoryEntry:
    row = OperationMemoryEntry(
        organization_id=organization_id,
        case_id=case_id,
        memory_type=str(memory_type or "unknown"),
        signature=str(signature or "unknown"),
        payload=dict(payload or {}),
        success=success,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def list_operation_memory(
    session: AsyncSession,
    *,
    organization_id: UUID,
    case_id: Optional[UUID] = None,
    limit: int = 10,
) -> List[Dict[str, Any]]:
    stmt = select(OperationMemoryEntry).where(OperationMemoryEntry.organization_id == organization_id)
    if case_id:
        stmt = stmt.where(OperationMemoryEntry.case_id == case_id)
    stmt = stmt.order_by(desc(OperationMemoryEntry.created_at)).limit(max(1, min(limit, 50)))
    rows = list((await session.execute(stmt)).scalars().all())
    return [
        {
            "memory_type": row.memory_type,
            "signature": row.signature,
            "payload": dict(row.payload or {}),
            "success": row.success,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
        for row in rows
    ]
