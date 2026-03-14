from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List
from uuid import UUID

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models import CrmInteraction, CrmLead, PredictionRecord


def _bound(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _lead_features(lead: CrmLead, interaction_count: int) -> Dict[str, Any]:
    now = datetime.now(timezone.utc)
    recency_hours = None
    if lead.last_interaction_at:
        recency_hours = max((now - lead.last_interaction_at).total_seconds() / 3600.0, 0.0)

    features = {
        "score": float(lead.score or 0.0),
        "interaction_count": int(interaction_count),
        "has_email": bool(lead.email),
        "has_phone": bool(lead.phone),
        "status": str(lead.status or "new"),
        "recency_hours": recency_hours,
    }
    return features


def _predict_probability(features: Dict[str, Any]) -> tuple[float, float]:
    score = float(features.get("score") or 0.0)
    interaction_count = float(features.get("interaction_count") or 0.0)
    has_email = 0.12 if features.get("has_email") else 0.0
    has_phone = 0.1 if features.get("has_phone") else 0.0

    status_bonus = {
        "new": 0.05,
        "contacted": 0.12,
        "qualified": 0.2,
        "proposal": 0.24,
        "negotiation": 0.28,
        "won": 0.35,
        "lost": -0.3,
    }.get(str(features.get("status") or "new").lower(), 0.0)

    recency_hours = features.get("recency_hours")
    recency_penalty = 0.0
    if isinstance(recency_hours, (int, float)):
        if recency_hours > 72:
            recency_penalty = 0.15
        elif recency_hours > 24:
            recency_penalty = 0.08

    interaction_bonus = min(interaction_count * 0.03, 0.18)
    raw = score * 0.55 + has_email + has_phone + status_bonus + interaction_bonus - recency_penalty
    probability = _bound(raw)

    confidence = 0.5
    if interaction_count >= 3:
        confidence += 0.2
    if score >= 0.4:
        confidence += 0.15
    if features.get("has_email") and features.get("has_phone"):
        confidence += 0.1
    confidence = _bound(confidence)
    return round(probability, 4), round(confidence, 4)


async def generate_lead_predictions(
    session: AsyncSession,
    *,
    organization_id: UUID,
    limit: int = 100,
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
        features = _lead_features(lead, interaction_count)
        probability, confidence = _predict_probability(features)

        lead.conversion_probability = probability

        record = PredictionRecord(
            organization_id=organization_id,
            entity_type="lead",
            entity_id=str(lead.id),
            model_name="lead-conversion-heuristic-v1",
            prediction_key="conversion_probability",
            prediction_value=probability,
            confidence=confidence,
            features=features,
            valid_until=valid_until,
        )
        session.add(record)
        predictions.append(record)

    await session.commit()
    for item in predictions:
        await session.refresh(item)
    return predictions
