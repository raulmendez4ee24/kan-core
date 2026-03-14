from __future__ import annotations

from typing import Any, Dict, Optional
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from brain.operation_memory import record_operation_memory


async def record_skill_feedback(
    session: AsyncSession,
    *,
    organization_id: UUID,
    case_id: Optional[UUID],
    skill_name: str,
    helpful: bool,
    note: str = "",
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    await record_operation_memory(
        session,
        organization_id=organization_id,
        case_id=case_id,
        memory_type="skill_feedback",
        signature=str(skill_name or "unknown"),
        payload={
            "skill_name": str(skill_name or "unknown"),
            "helpful": bool(helpful),
            "note": str(note or "").strip(),
            "metadata": dict(metadata or {}),
        },
        success=bool(helpful),
    )

