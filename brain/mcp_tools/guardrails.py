from __future__ import annotations

from typing import Any, Dict
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from brain.autonomous_case_manager import list_rollbackable_guardrails
from brain.execution_rollback import execute_rollback_from_metadata


async def get_rollbackable_guardrails_tool(
    session: AsyncSession,
    *,
    organization_id: UUID,
    args: Dict[str, Any],
) -> Dict[str, Any]:
    limit = int(args.get("limit") or 10)
    items = await list_rollbackable_guardrails(session, organization_id=organization_id, limit=limit)
    return {"items": items}


async def run_rollback_tool(
    session: AsyncSession,
    *,
    organization_id: UUID,
    args: Dict[str, Any],
) -> Dict[str, Any]:
    metadata = dict(args.get("metadata") or {})
    return await execute_rollback_from_metadata(
        session,
        organization_id=organization_id,
        metadata=metadata,
    )

