from datetime import datetime, timezone
from typing import Any, Dict, List
from uuid import UUID

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from models import AlertIncident, AlertRule
from tools.n8n_client import send_to_n8n_with_response


def _op_passed(operator: str, value: float, threshold: float) -> bool:
    if operator == "gt":
        return value > threshold
    if operator == "gte":
        return value >= threshold
    if operator == "lt":
        return value < threshold
    if operator == "lte":
        return value <= threshold
    return value == threshold


async def evaluate_alert_rules(
    session: AsyncSession,
    *,
    organization_id: UUID,
    metrics: Dict[str, Any],
) -> List[AlertIncident]:
    stmt = select(AlertRule).where(
        AlertRule.organization_id == organization_id,
        AlertRule.is_active.is_(True),
    )
    rules = list((await session.execute(stmt)).scalars().all())
    incidents: List[AlertIncident] = []

    for rule in rules:
        value = float(metrics.get(rule.metric_key, 0.0) or 0.0)
        triggered = _op_passed(rule.operator, value, float(rule.threshold))

        incident_key = f"{rule.metric_key}:{rule.operator}:{rule.threshold}"
        open_stmt = select(AlertIncident).where(
            AlertIncident.organization_id == organization_id,
            AlertIncident.incident_key == incident_key,
            AlertIncident.status == "open",
        )
        existing_open = (await session.execute(open_stmt)).scalars().first()

        if triggered:
            if existing_open:
                existing_open.last_seen_at = datetime.now(timezone.utc)
                existing_open.incident_metadata = {
                    **(existing_open.incident_metadata or {}),
                    "current_value": value,
                }
                incidents.append(existing_open)
                continue

            incident = AlertIncident(
                organization_id=organization_id,
                rule_id=rule.id,
                incident_key=incident_key,
                title=f"{rule.name} triggered",
                description=(
                    f"Metric {rule.metric_key} value={value:.4f} violated {rule.operator} {rule.threshold:.4f}."
                ),
                severity=rule.severity,
                status="open",
                first_seen_at=datetime.now(timezone.utc),
                last_seen_at=datetime.now(timezone.utc),
                incident_metadata={
                    "metric_key": rule.metric_key,
                    "operator": rule.operator,
                    "threshold": float(rule.threshold),
                    "current_value": value,
                },
            )
            session.add(incident)
            incidents.append(incident)

            channels = [str(x) for x in (rule.channels or {}).get("items", []) if str(x).strip()]
            if not channels:
                channels = ["n8n"]
            if "n8n" in channels or "slack" in channels or "email" in channels:
                await send_to_n8n_with_response(
                    {
                        "source": "alert_engine",
                        "organization_id": str(organization_id),
                        "severity": rule.severity,
                        "title": f"{rule.name} triggered",
                        "description": incident.description,
                        "channels": channels,
                    }
                )
            continue

        if existing_open:
            existing_open.status = "resolved"
            existing_open.resolved_at = datetime.now(timezone.utc)
            existing_open.last_seen_at = datetime.now(timezone.utc)
            existing_open.incident_metadata = {
                **(existing_open.incident_metadata or {}),
                "resolved_value": value,
            }

    await session.commit()
    for item in incidents:
        await session.refresh(item)
    return incidents


async def list_incidents(
    session: AsyncSession,
    *,
    organization_id: UUID,
    status: str | None = None,
    limit: int = 100,
) -> List[AlertIncident]:
    stmt = select(AlertIncident).where(AlertIncident.organization_id == organization_id)
    if status:
        stmt = stmt.where(AlertIncident.status == status)
    stmt = stmt.order_by(desc(AlertIncident.created_at)).limit(max(1, min(limit, 500)))
    return list((await session.execute(stmt)).scalars().all())
