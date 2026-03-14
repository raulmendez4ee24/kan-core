from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List
from uuid import UUID

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from models import CollectionsAction, CollectionsCase, CrmLead


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _risk_by_age(hours_without_interaction: float) -> str:
    if hours_without_interaction >= 168:
        return "high"
    if hours_without_interaction >= 72:
        return "medium"
    return "low"


def _strategy_for_case(amount_due: float, risk_level: str) -> str:
    if amount_due >= 1000:
        return "structured_settlement"
    if risk_level == "high":
        return "urgent_reminder"
    return "friendly_reminder"


async def run_collections_cycle(
    session: AsyncSession,
    *,
    organization_id: UUID,
    dry_run: bool,
    max_cases: int,
    min_amount_due: float,
) -> Dict[str, Any]:
    leads = list(
        (
            await session.execute(
                select(CrmLead)
                .where(CrmLead.organization_id == organization_id)
                .order_by(desc(CrmLead.updated_at))
                .limit(max(1, min(max_cases, 500)))
            )
        )
        .scalars()
        .all()
    )
    scanned = len(leads)

    created_cases: List[CollectionsCase] = []
    scheduled_actions = 0

    if dry_run:
        now = _now()
        for lead in leads:
            due = float(((lead.lead_metadata or {}).get("amount_due")) or 0.0)
            if due < float(min_amount_due):
                continue
            last = lead.last_interaction_at or (now - timedelta(hours=48))
            age_hours = max((now - last).total_seconds() / 3600.0, 0.0)
            risk = _risk_by_age(age_hours)
            created_cases.append(
                CollectionsCase(
                    organization_id=organization_id,
                    lead_id=lead.id,
                    customer_id=(lead.external_id or None),
                    amount_due=due,
                    currency=str(((lead.lead_metadata or {}).get("currency")) or "USD"),
                    due_at=None,
                    status="open",
                    risk_level=risk,
                    strategy=_strategy_for_case(due, risk),
                    case_metadata={"preview": True, "lead_status": lead.status},
                )
            )
        return {
            "dry_run": True,
            "scanned": scanned,
            "created_cases": len(created_cases),
            "scheduled_actions": 0,
            "cases": created_cases,
            "generated_at": _now(),
        }

    now = _now()
    for lead in leads:
        due = float(((lead.lead_metadata or {}).get("amount_due")) or 0.0)
        if due < float(min_amount_due):
            continue

        existing = (
            await session.execute(
                select(CollectionsCase).where(
                    CollectionsCase.organization_id == organization_id,
                    CollectionsCase.lead_id == lead.id,
                    CollectionsCase.status.in_(["open", "negotiating"]),
                )
            )
        ).scalars().first()
        if existing:
            continue

        last = lead.last_interaction_at or (now - timedelta(hours=48))
        age_hours = max((now - last).total_seconds() / 3600.0, 0.0)
        risk = _risk_by_age(age_hours)
        case = CollectionsCase(
            organization_id=organization_id,
            lead_id=lead.id,
            customer_id=(lead.external_id or None),
            amount_due=due,
            currency=str(((lead.lead_metadata or {}).get("currency")) or "USD"),
            due_at=None,
            status="open",
            risk_level=risk,
            strategy=_strategy_for_case(due, risk),
            case_metadata={"source": "collections_cycle", "lead_status": lead.status},
        )
        session.add(case)
        await session.commit()
        await session.refresh(case)
        created_cases.append(case)

        action = CollectionsAction(
            organization_id=organization_id,
            case_id=case.id,
            action_type="send_reminder",
            channel="whatsapp",
            message=(
                f"Hola {lead.name or 'cliente'}, detectamos un saldo pendiente de {due:.2f} {case.currency}. "
                "Podemos apoyarte con opciones de pago."
            ),
            status="sent",
            result={"automated": True},
        )
        session.add(action)
        await session.commit()
        scheduled_actions += 1

    return {
        "dry_run": False,
        "scanned": scanned,
        "created_cases": len(created_cases),
        "scheduled_actions": int(scheduled_actions),
        "cases": created_cases,
        "generated_at": _now(),
    }


async def list_collections_cases(
    session: AsyncSession,
    *,
    organization_id: UUID,
    limit: int = 100,
) -> List[CollectionsCase]:
    stmt = (
        select(CollectionsCase)
        .where(CollectionsCase.organization_id == organization_id)
        .order_by(desc(CollectionsCase.updated_at))
        .limit(max(1, min(limit, 500)))
    )
    return list((await session.execute(stmt)).scalars().all())


async def apply_payment_agreement(
    session: AsyncSession,
    *,
    organization_id: UUID,
    case_id: UUID,
    amount: float,
    due_at: datetime | None,
    note: str | None,
    metadata: Dict[str, Any],
) -> Dict[str, Any]:
    row = await session.get(CollectionsCase, case_id)
    if not row or row.organization_id != organization_id:
        raise ValueError("Case not found")

    agreement = {
        "amount": float(amount),
        "due_at": due_at.isoformat() if due_at else None,
        "note": note,
        **(metadata or {}),
    }
    row.status = "negotiating"
    row.case_metadata = {**(row.case_metadata or {}), "agreement": agreement}
    await session.commit()
    await session.refresh(row)

    action = CollectionsAction(
        organization_id=organization_id,
        case_id=row.id,
        action_type="agreement_created",
        channel="system",
        message=note or "Agreement created",
        status="ok",
        result={"agreement": agreement},
    )
    session.add(action)
    await session.commit()
    await session.refresh(action)
    return {
        "case_id": str(row.id),
        "status": row.status,
        "agreement": agreement,
        "action_id": str(action.id),
        "updated_at": _now(),
    }

