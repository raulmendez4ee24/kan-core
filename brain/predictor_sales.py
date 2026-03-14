from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Tuple
from uuid import UUID

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models import CrmInteraction, CrmLead, PredictionRecord


def _bound(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _lead_recency_hours(lead: CrmLead) -> float:
    now = datetime.now(timezone.utc)
    if not lead.last_interaction_at:
        return 9999.0
    return max((now - lead.last_interaction_at).total_seconds() / 3600.0, 0.0)


def _churn_probability(
    *,
    conversion_probability: float,
    status: str,
    recency_hours: float,
    interaction_count: int,
) -> Tuple[float, float]:
    base = 1.0 - float(conversion_probability or 0.0)
    status_token = str(status or "new").lower()
    if status_token in {"won", "closed_won"}:
        return 0.0, 0.9
    if status_token in {"lost", "closed_lost"}:
        return 1.0, 0.9

    risk = base
    if recency_hours > 168:
        risk += 0.18
    elif recency_hours > 72:
        risk += 0.1
    elif recency_hours > 24:
        risk += 0.05

    if interaction_count >= 5:
        risk -= 0.06
    elif interaction_count >= 2:
        risk -= 0.03

    risk = _bound(risk)
    confidence = 0.55
    if interaction_count >= 3:
        confidence += 0.15
    if recency_hours < 72:
        confidence += 0.1
    confidence = _bound(confidence)
    return round(risk, 4), round(confidence, 4)


async def generate_lead_churn_predictions(
    session: AsyncSession,
    *,
    organization_id: UUID,
    limit: int = 200,
) -> List[PredictionRecord]:
    leads_stmt = (
        select(CrmLead)
        .where(CrmLead.organization_id == organization_id)
        .order_by(desc(CrmLead.updated_at))
        .limit(max(1, min(limit, 500)))
    )
    leads = list((await session.execute(leads_stmt)).scalars().all())

    predictions: List[PredictionRecord] = []
    valid_until = datetime.now(timezone.utc) + timedelta(days=3)

    for lead in leads:
        interaction_stmt = select(func.count(CrmInteraction.id)).where(
            CrmInteraction.organization_id == organization_id,
            CrmInteraction.lead_id == lead.id,
        )
        interaction_count = int((await session.execute(interaction_stmt)).scalar() or 0)
        recency_hours = _lead_recency_hours(lead)
        churn_risk, confidence = _churn_probability(
            conversion_probability=float(lead.conversion_probability or 0.0),
            status=str(lead.status or "new"),
            recency_hours=recency_hours,
            interaction_count=interaction_count,
        )
        record = PredictionRecord(
            organization_id=organization_id,
            entity_type="lead",
            entity_id=str(lead.id),
            model_name="lead-churn-heuristic-v1",
            prediction_key="churn_risk",
            prediction_value=churn_risk,
            confidence=confidence,
            features={
                "conversion_probability": float(lead.conversion_probability or 0.0),
                "status": str(lead.status or "new"),
                "recency_hours": round(recency_hours, 2),
                "interaction_count": interaction_count,
            },
            valid_until=valid_until,
        )
        session.add(record)
        predictions.append(record)

    await session.commit()
    for item in predictions:
        await session.refresh(item)
    return predictions


async def _best_hour_histogram(
    session: AsyncSession,
    *,
    organization_id: UUID,
    limit_days: int = 60,
) -> Dict[int, int]:
    since = datetime.now(timezone.utc) - timedelta(days=max(1, min(limit_days, 365)))
    # Extract hour-of-day from timestamps (UTC).
    hour_expr = func.extract("hour", CrmInteraction.created_at)
    stmt = (
        select(hour_expr.label("hour"), func.count(CrmInteraction.id))
        .where(
            CrmInteraction.organization_id == organization_id,
            CrmInteraction.created_at >= since,
        )
        .group_by(hour_expr)
        .order_by(func.count(CrmInteraction.id).desc())
    )
    rows = list((await session.execute(stmt)).all())
    histogram: Dict[int, int] = {}
    for hour, count in rows:
        try:
            h = int(hour)
            c = int(count)
        except Exception:
            continue
        if 0 <= h <= 23:
            histogram[h] = c
    return histogram


def _pick_best_hour(histogram: Dict[int, int]) -> Tuple[int, float]:
    if not histogram:
        return 10, 0.4
    best_hour = max(histogram.keys(), key=lambda h: histogram.get(h, 0))
    total = sum(histogram.values())
    confidence = 0.55
    if total >= 50:
        confidence += 0.2
    elif total >= 15:
        confidence += 0.1
    if histogram.get(best_hour, 0) >= max(3, total * 0.2):
        confidence += 0.1
    return int(best_hour), _bound(confidence)


async def generate_best_contact_time_predictions(
    session: AsyncSession,
    *,
    organization_id: UUID,
    limit: int = 200,
) -> List[PredictionRecord]:
    leads_stmt = (
        select(CrmLead.id)
        .where(CrmLead.organization_id == organization_id)
        .order_by(desc(CrmLead.updated_at))
        .limit(max(1, min(limit, 500)))
    )
    lead_ids = list((await session.execute(leads_stmt)).scalars().all())
    histogram = await _best_hour_histogram(session, organization_id=organization_id, limit_days=60)
    best_hour, confidence = _pick_best_hour(histogram)
    valid_until = datetime.now(timezone.utc) + timedelta(days=7)

    predictions: List[PredictionRecord] = []
    for lead_id in lead_ids:
        record = PredictionRecord(
            organization_id=organization_id,
            entity_type="lead",
            entity_id=str(lead_id),
            model_name="best-contact-time-heuristic-v1",
            prediction_key="best_contact_hour",
            prediction_value=float(best_hour),
            confidence=float(confidence),
            features={
                "best_hour": best_hour,
                "histogram": histogram,
            },
            valid_until=valid_until,
        )
        session.add(record)
        predictions.append(record)

    await session.commit()
    for item in predictions:
        await session.refresh(item)
    return predictions

