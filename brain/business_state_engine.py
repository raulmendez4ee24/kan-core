from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional
from uuid import UUID

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from brain.business_monitor import build_metrics_snapshot
from models import AuditLog, BusinessEvent, BusinessMetricsSnapshot, BusinessStateSnapshot, IntegrationRunLog, Objective


def _safe_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


async def _count_rows(session: AsyncSession, stmt: Any) -> int:
    try:
        result = await session.execute(stmt)
        value = result.scalar()
        return int(value or 0)
    except Exception:
        return 0


async def build_business_state(
    session: AsyncSession,
    *,
    organization_id: UUID,
    window_minutes: int = 60,
    persist: bool = True,
    snapshot_kind: str = "operational",
) -> Dict[str, Any]:
    effective_window = max(5, int(window_minutes))
    snapshot = await build_metrics_snapshot(
        session,
        organization_id=organization_id,
        window_minutes=effective_window,
    )
    metrics = dict(snapshot.metrics or {})
    window_start = snapshot.window_start or (datetime.now(timezone.utc) - timedelta(minutes=effective_window))
    window_end = snapshot.window_end or datetime.now(timezone.utc)

    active_objectives = await _count_rows(
        session,
        select(func.count()).select_from(Objective).where(
            Objective.organization_id == organization_id,
            Objective.status == "active",
        ),
    )
    integration_failures = await _count_rows(
        session,
        select(func.count()).select_from(IntegrationRunLog).where(
            IntegrationRunLog.organization_id == organization_id,
            IntegrationRunLog.created_at >= window_start,
            IntegrationRunLog.status.in_(["failed", "error"]),
        ),
    )
    recent_business_events = await _count_rows(
        session,
        select(func.count()).select_from(BusinessEvent).where(
            BusinessEvent.organization_id == organization_id,
            BusinessEvent.created_at >= window_start,
        ),
    )
    guardrail_warnings = await _count_rows(
        session,
        select(func.count()).select_from(AuditLog).where(
            AuditLog.organization_id == organization_id,
            AuditLog.created_at >= window_start,
            AuditLog.action.in_(["execution_guard.paused", "execution_guard.tripwire"]),
        ),
    )
    campaign_backlog = _safe_float(
        metrics.get("pending_campaigns")
        or metrics.get("outreach_backlog")
        or metrics.get("queued_campaigns")
        or 0.0
    )

    critical_risks = []
    if _safe_float(metrics.get("integration_error_rate")) >= 0.25:
        critical_risks.append("integration_error_rate")
    if guardrail_warnings > 0:
        critical_risks.append("execution_guard")
    if integration_failures > 3:
        critical_risks.append("integration_failures")
    if integration_failures + guardrail_warnings >= 4:
        critical_risks.append("workflow_degradation")

    cash_exposure = _safe_float(metrics.get("collections_exposure_usd") or metrics.get("cash_exposure") or 0.0)
    customer_impact_score = round(
        min(
            100.0,
            _safe_float(metrics.get("open_customer_issues") or 0.0) * 10.0
            + _safe_float(metrics.get("lead_drop_rate") or 0.0) * 100.0,
        ),
        4,
    )
    ops_health_score = round(
        max(0.0, 1.0 - _safe_float(metrics.get("integration_error_rate") or 0.0) - min(guardrail_warnings * 0.05, 0.3)),
        4,
    )
    sla_pressure = round(min(1.0, (_safe_float(metrics.get("pending_tasks") or 0.0) / 25.0) + (guardrail_warnings * 0.05)), 4)

    if cash_exposure > 0:
        recommended_focus = "payment_followthrough"
    elif customer_impact_score >= 40:
        recommended_focus = "customer_recovery"
    elif "workflow_degradation" in critical_risks:
        recommended_focus = "workflow_recovery"
    elif campaign_backlog > 0 and ops_health_score >= 0.55:
        recommended_focus = "campaign_execution"
    elif critical_risks:
        recommended_focus = "ops_anomaly_response"
    else:
        recommended_focus = "ops_anomaly_response" if ops_health_score < 0.8 else "customer_recovery"

    state_payload = {
        "organization_id": str(organization_id),
        "generated_at": window_end.isoformat(),
        "active_objectives": active_objectives,
        "critical_risks": critical_risks,
        "cash_exposure": round(cash_exposure, 4),
        "customer_impact_score": customer_impact_score,
        "ops_health_score": ops_health_score,
        "sla_pressure": sla_pressure,
        "current_bottlenecks": {
            "integration_failures": integration_failures,
            "guardrail_warnings": guardrail_warnings,
            "recent_business_events": recent_business_events,
            "campaign_backlog": round(campaign_backlog, 4),
        },
        "recommended_focus": recommended_focus,
        "confidence": round(max(0.25, min(0.95, 0.45 + (recent_business_events * 0.01) + (active_objectives * 0.02))), 4),
        "metrics": metrics,
        "window_minutes": effective_window,
    }

    if persist:
        row = BusinessStateSnapshot(
            organization_id=organization_id,
            snapshot_kind=snapshot_kind,
            state_payload=state_payload,
            confidence=float(state_payload["confidence"]),
            window_start=window_start,
            window_end=window_end,
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        state_payload["snapshot_id"] = str(row.id)

    return state_payload


async def get_latest_business_state(
    session: AsyncSession,
    *,
    organization_id: UUID,
    snapshot_kind: str = "operational",
    refresh: bool = False,
    window_minutes: int = 60,
) -> Dict[str, Any]:
    if refresh:
        return await build_business_state(
            session,
            organization_id=organization_id,
            window_minutes=window_minutes,
            persist=True,
            snapshot_kind=snapshot_kind,
        )

    stmt = (
        select(BusinessStateSnapshot)
        .where(
            BusinessStateSnapshot.organization_id == organization_id,
            BusinessStateSnapshot.snapshot_kind == snapshot_kind,
        )
        .order_by(desc(BusinessStateSnapshot.created_at))
        .limit(1)
    )
    row = (await session.execute(stmt)).scalars().first()
    if not row:
        return await build_business_state(
            session,
            organization_id=organization_id,
            window_minutes=window_minutes,
            persist=True,
            snapshot_kind=snapshot_kind,
        )
    payload = dict(row.state_payload or {})
    payload["snapshot_id"] = str(row.id)
    return payload
