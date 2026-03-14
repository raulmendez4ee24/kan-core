import os
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from brain.alert_engine import evaluate_alert_rules, list_incidents
from brain.business_monitor import build_metrics_snapshot
from database import get_session
from models import AlertRule
from schemas.alerts import AlertIncidentRead, AlertRuleCreateRequest, AlertRuleRead
from security import require_client_token

router = APIRouter(prefix="/alerts", tags=["alerts"])


def _is_enabled(flag: str, default: str = "true") -> bool:
    return os.getenv(flag, default).lower() in {"1", "true", "yes"}


def _parse_uuid(raw: str, *, field: str) -> UUID:
    try:
        return UUID(raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid {field}") from exc


def _to_rule_read(rule: AlertRule) -> AlertRuleRead:
    channels = rule.channels if isinstance(rule.channels, dict) else {}
    return AlertRuleRead(
        id=str(rule.id),
        name=rule.name,
        metric_key=rule.metric_key,
        operator=rule.operator,
        threshold=float(rule.threshold),
        severity=rule.severity,
        channels=[str(x) for x in channels.get("items", []) if str(x).strip()],
        is_active=bool(rule.is_active),
        metadata=rule.alert_metadata or {},
        created_at=rule.created_at,
    )


@router.post("/rules", response_model=AlertRuleRead)
async def create_alert_rule(
    req: AlertRuleCreateRequest,
    evaluate_now: bool = Query(default=True),
    client_id: str = Depends(require_client_token),
    session: AsyncSession = Depends(get_session),
):
    if not _is_enabled("ENABLE_ADVANCED_ALERTS"):
        raise HTTPException(status_code=404, detail="Feature ENABLE_ADVANCED_ALERTS disabled")

    org_id = _parse_uuid(client_id, field="client_id")
    row = AlertRule(
        organization_id=org_id,
        name=req.name,
        metric_key=req.metric_key,
        operator=req.operator,
        threshold=req.threshold,
        severity=req.severity,
        channels={"items": list(req.channels)},
        is_active=True,
        alert_metadata=req.metadata,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)

    if evaluate_now:
        snapshot = await build_metrics_snapshot(session, organization_id=org_id, window_minutes=5)
        await evaluate_alert_rules(
            session,
            organization_id=org_id,
            metrics=snapshot.metrics or {},
        )

    return _to_rule_read(row)


@router.get("/incidents", response_model=list[AlertIncidentRead])
async def get_alert_incidents(
    status: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    refresh: bool = Query(default=True),
    client_id: str = Depends(require_client_token),
    session: AsyncSession = Depends(get_session),
):
    if not _is_enabled("ENABLE_ADVANCED_ALERTS"):
        raise HTTPException(status_code=404, detail="Feature ENABLE_ADVANCED_ALERTS disabled")

    org_id = _parse_uuid(client_id, field="client_id")
    if refresh:
        snapshot = await build_metrics_snapshot(session, organization_id=org_id, window_minutes=5)
        await evaluate_alert_rules(
            session,
            organization_id=org_id,
            metrics=snapshot.metrics or {},
        )

    rows = await list_incidents(
        session,
        organization_id=org_id,
        status=status,
        limit=limit,
    )
    return [
        AlertIncidentRead(
            id=str(item.id),
            rule_id=str(item.rule_id) if item.rule_id else None,
            incident_key=item.incident_key,
            title=item.title,
            description=item.description,
            severity=item.severity,
            status=item.status,
            first_seen_at=item.first_seen_at,
            last_seen_at=item.last_seen_at,
            resolved_at=item.resolved_at,
            metadata=item.incident_metadata or {},
            created_at=item.created_at,
        )
        for item in rows
    ]
