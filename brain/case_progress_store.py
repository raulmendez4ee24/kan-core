from __future__ import annotations

from typing import Any, Dict, Optional
from uuid import UUID

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from brain.operation_memory import record_operation_memory
from models import OperationMemoryEntry


async def load_case_progress(
    session: AsyncSession,
    *,
    organization_id: UUID,
    case_id: UUID,
) -> Dict[str, Any]:
    stmt = (
        select(OperationMemoryEntry)
        .where(
            OperationMemoryEntry.organization_id == organization_id,
            OperationMemoryEntry.case_id == case_id,
            OperationMemoryEntry.memory_type == "case_progress",
        )
        .order_by(desc(OperationMemoryEntry.created_at))
        .limit(1)
    )
    row = (await session.execute(stmt)).scalars().first()
    if not row:
        return {
            "case_id": str(case_id),
            "organization_id": str(organization_id),
            "current_goal": "",
            "active_domain": "",
            "last_completed_step": 0,
            "next_expected_step": "",
            "last_successful_tool": "",
            "last_failure": {},
            "retry_count_by_signature": {},
            "rollback_options": [],
            "open_questions": [],
            "artifacts_created": [],
        }
    payload = dict(row.payload or {})
    payload.setdefault("case_id", str(case_id))
    payload.setdefault("organization_id", str(organization_id))
    return payload


async def save_case_progress(
    session: AsyncSession,
    *,
    organization_id: UUID,
    case_id: UUID,
    payload: Dict[str, Any],
    success: Optional[bool] = None,
) -> None:
    normalized = dict(payload or {})
    normalized["case_id"] = str(case_id)
    normalized["organization_id"] = str(organization_id)
    await record_operation_memory(
        session,
        organization_id=organization_id,
        case_id=case_id,
        memory_type="case_progress",
        signature="case_progress",
        payload=normalized,
        success=success,
    )

