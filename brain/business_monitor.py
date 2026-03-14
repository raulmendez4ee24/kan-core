from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models import BusinessEvent, BusinessMetricsSnapshot, ConversationLog, IntegrationRunLog


async def ingest_business_event(
    session: AsyncSession,
    *,
    organization_id: UUID,
    event_type: str,
    source: str,
    payload: Dict[str, Any],
    channel: str | None = None,
    entity_id: str | None = None,
) -> BusinessEvent:
    if event_type in {"sale_closed", "payment_success"}:
        try:
            from brain.creative_event_tracker import (
                record_sales_outcome,
                resolve_campaign_source,
            )

            source_key = resolve_campaign_source(
                campaign_id=str(payload.get("campaign_id") or ""),
                utm_campaign=str(payload.get("utm_campaign") or ""),
                source_campaign=str(payload.get("source_campaign") or ""),
                ad_name=str(payload.get("ad_name") or ""),
            )
            if source_key:
                record_sales_outcome(source_key, closed=True)
        except Exception:
            pass
    event = BusinessEvent(
        organization_id=organization_id,
        event_type=event_type,
        source=source,
        channel=channel,
        entity_id=entity_id,
        payload=payload,
    )
    session.add(event)
    await session.commit()
    await session.refresh(event)
    return event


async def build_metrics_snapshot(
    session: AsyncSession,
    *,
    organization_id: UUID,
    window_minutes: int = 5,
) -> BusinessMetricsSnapshot:
    now = datetime.now(timezone.utc)
    start = now - timedelta(minutes=max(1, window_minutes))

    event_stmt = select(BusinessEvent).where(
        BusinessEvent.organization_id == organization_id,
        BusinessEvent.created_at >= start,
    )
    events = list((await session.execute(event_stmt)).scalars().all())

    conversation_stmt = select(func.count(ConversationLog.id)).where(
        ConversationLog.client_id == organization_id,
        ConversationLog.timestamp >= start,
    )
    conversation_count = int((await session.execute(conversation_stmt)).scalar() or 0)

    run_stmt = select(IntegrationRunLog).where(
        IntegrationRunLog.client_id == organization_id,
        IntegrationRunLog.created_at >= start,
    )
    run_rows = list((await session.execute(run_stmt)).scalars().all())

    metrics = {
        "leads_incoming": sum(1 for e in events if e.event_type in {"lead_created", "lead_updated"}),
        "conversations": conversation_count,
        "messages": sum(1 for e in events if e.event_type in {"message_in", "message_out"}),
        "appointments": sum(1 for e in events if e.event_type in {"appointment_created", "appointment_reminder"}),
        "sales": sum(1 for e in events if e.event_type in {"sale_closed", "payment_success"}),
        "errors": sum(1 for e in events if e.event_type in {"error", "workflow_failed"}),
        "api_failures": sum(1 for e in events if e.event_type in {"api_failure", "provider_failure"}),
        "integration_error_rate": (
            (sum(1 for r in run_rows if r.status in {"failed", "manual_action_required"}) / len(run_rows))
            if run_rows
            else 0.0
        ),
        "integration_runs": len(run_rows),
    }

    snapshot = BusinessMetricsSnapshot(
        organization_id=organization_id,
        window_minutes=max(1, window_minutes),
        window_start=start,
        window_end=now,
        total_leads=int(metrics["leads_incoming"]),
        total_conversations=int(metrics["conversations"]),
        total_messages=int(metrics["messages"]),
        total_appointments=int(metrics["appointments"]),
        total_sales=int(metrics["sales"]),
        total_errors=int(metrics["errors"]),
        total_api_failures=int(metrics["api_failures"]),
        metrics=metrics,
    )
    session.add(snapshot)
    await session.commit()
    await session.refresh(snapshot)
    return snapshot
