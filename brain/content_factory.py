from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from models import ContentAsset, ContentPlan
from tools.n8n_client import send_to_n8n_with_response


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _build_weekly_plan(*, channel: str, brief: str, weeks: int) -> Dict[str, Any]:
    start = _now().date()
    items: List[Dict[str, Any]] = []
    for i in range(max(1, min(weeks, 12))):
        day = start + timedelta(days=i * 7)
        items.append(
            {
                "week": i + 1,
                "date": day.isoformat(),
                "channel": channel,
                "topic": f"{brief[:80]} - foco semana {i + 1}",
                "cta": "Escríbenos para más información",
            }
        )
    return {"items": items}


async def generate_plan(
    session: AsyncSession,
    *,
    organization_id: UUID,
    title: str,
    channel: str,
    cadence: str,
    brief: str,
    weeks: int,
    metadata: Dict[str, Any],
) -> ContentPlan:
    plan = _build_weekly_plan(channel=channel, brief=brief, weeks=weeks)
    row = ContentPlan(
        organization_id=organization_id,
        title=title,
        channel=channel,
        cadence=cadence,
        brief=brief,
        plan={**plan, **(metadata or {})},
        status="draft",
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def publish_asset(
    session: AsyncSession,
    *,
    organization_id: UUID,
    plan_id: UUID | None,
    channel: str,
    asset_type: str,
    content: str,
    metadata: Dict[str, Any],
) -> Dict[str, Any]:
    asset = ContentAsset(
        organization_id=organization_id,
        plan_id=plan_id,
        asset_type=asset_type,
        channel=channel,
        content=content,
        asset_metadata=dict(metadata or {}),
        status="published",
        published_at=_now(),
    )
    session.add(asset)
    await session.commit()
    await session.refresh(asset)

    dispatch_result = await send_to_n8n_with_response(
        {
            "source": "content.publish",
            "organization_id": str(organization_id),
            "plan_id": str(plan_id) if plan_id else None,
            "asset_id": str(asset.id),
            "channel": channel,
            "asset_type": asset_type,
            "content": content,
            "metadata": metadata,
        }
    )
    return {
        "asset": asset,
        "dispatched": bool(dispatch_result.get("dispatched")),
        "pending": bool(dispatch_result.get("pending")),
        "confirmed": bool(dispatch_result.get("confirmed")),
        "execution_status": str(dispatch_result.get("execution_status") or "not_dispatched"),
    }
