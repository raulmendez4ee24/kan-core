from __future__ import annotations

from typing import Any, Dict
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from models import AutonomousCase


async def load_world_state(
    session: AsyncSession,
    *,
    organization_id: UUID,
    case_id: UUID,
) -> Dict[str, Any]:
    row = await session.get(AutonomousCase, case_id)
    if not row or row.organization_id != organization_id:
        return {}
    return dict(row.world_state or {})


async def save_world_state(
    session: AsyncSession,
    *,
    organization_id: UUID,
    case_id: UUID,
    world_state: Dict[str, Any],
) -> Dict[str, Any]:
    row = await session.get(AutonomousCase, case_id)
    if not row or row.organization_id != organization_id:
        return {}
    row.world_state = dict(world_state or {})
    await session.commit()
    await session.refresh(row)
    return dict(row.world_state or {})
