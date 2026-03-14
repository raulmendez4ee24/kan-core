from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from models import UIState, UITransition


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def remember_ui_state(
    session: AsyncSession,
    *,
    organization_id: UUID,
    ui_signature: str,
    domain: Optional[str],
    app_name: Optional[str],
    screenshot_hash: Optional[str],
    extracted_elements: Dict[str, Any],
) -> UIState:
    row = UIState(
        organization_id=organization_id,
        ui_signature=str(ui_signature).strip()[:180],
        domain=(str(domain).strip().lower()[:180] if domain else None),
        app_name=(str(app_name).strip()[:180] if app_name else None),
        screenshot_hash=(str(screenshot_hash).strip()[:80] if screenshot_hash else None),
        extracted_elements=dict(extracted_elements or {}),
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def remember_transition(
    session: AsyncSession,
    *,
    organization_id: UUID,
    from_signature: str,
    to_signature: str,
    action: str,
    success: bool,
    duration_ms: Optional[int],
    metadata: Dict[str, Any],
) -> UITransition:
    stmt = (
        select(UITransition)
        .where(
            UITransition.organization_id == organization_id,
            UITransition.from_signature == str(from_signature).strip()[:180],
            UITransition.to_signature == str(to_signature).strip()[:180],
            UITransition.action == str(action).strip()[:120],
        )
        .limit(1)
    )
    row = (await session.execute(stmt)).scalars().first()
    if not row:
        row = UITransition(
            organization_id=organization_id,
            from_signature=str(from_signature).strip()[:180],
            to_signature=str(to_signature).strip()[:180],
            action=str(action).strip()[:120],
            success_rate=1.0 if success else 0.0,
            avg_duration_ms=float(duration_ms or 0),
            sample_size=1,
            transition_metadata=dict(metadata or {}),
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return row

    n = max(int(row.sample_size or 0), 0)
    prev_success_rate = float(row.success_rate or 0.0)
    prev_duration = float(row.avg_duration_ms or 0.0)
    new_n = n + 1
    new_success = 1.0 if success else 0.0
    row.success_rate = ((prev_success_rate * n) + new_success) / new_n
    if duration_ms is not None:
        row.avg_duration_ms = ((prev_duration * n) + float(duration_ms)) / new_n
    row.sample_size = new_n
    row.transition_metadata = {**(row.transition_metadata or {}), **(metadata or {})}
    row.updated_at = _now()
    await session.commit()
    await session.refresh(row)
    return row


async def best_transitions_for_state(
    session: AsyncSession,
    *,
    organization_id: UUID,
    from_signature: str,
    limit: int = 5,
) -> List[UITransition]:
    stmt = (
        select(UITransition)
        .where(
            UITransition.organization_id == organization_id,
            UITransition.from_signature == str(from_signature).strip()[:180],
        )
        .order_by(desc(UITransition.success_rate), desc(UITransition.sample_size), desc(UITransition.updated_at))
        .limit(max(1, min(limit, 20)))
    )
    return list((await session.execute(stmt)).scalars().all())

