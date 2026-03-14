from datetime import datetime, timezone
from typing import Any, Dict, List
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from brain.crm_engine import patch_lead, run_followups, upsert_lead
from models import CrmLead, OmnichannelThread, SalesPipelineEvent


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _stage_from_score(score: float) -> str:
    if score >= 0.85:
        return "qualified"
    if score >= 0.6:
        return "contacted"
    return "new"


async def ensure_leads_from_threads(
    session: AsyncSession,
    *,
    organization_id: UUID,
    max_items: int,
) -> int:
    created = 0
    stmt = (
        select(OmnichannelThread)
        .where(OmnichannelThread.organization_id == organization_id)
        .limit(max(1, min(max_items, 500)))
    )
    threads = list((await session.execute(stmt)).scalars().all())
    for thread in threads:
        existing = (
            await session.execute(
                select(CrmLead.id).where(
                    CrmLead.organization_id == organization_id,
                    CrmLead.external_id == f"omni:{thread.customer_id}",
                )
            )
        ).scalars().first()
        if existing:
            continue
        await upsert_lead(
            session,
            organization_id=organization_id,
            payload={
                "external_id": f"omni:{thread.customer_id}",
                "name": thread.thread_metadata.get("customer_name") if isinstance(thread.thread_metadata, dict) else None,
                "status": "new",
                "source": thread.primary_channel,
                "score": 0.45,
                "conversion_probability": 0.35,
                "metadata": {"source": "sales_autopilot.thread_import", "thread_id": str(thread.id)},
            },
        )
        created += 1
    return created


async def run_sales_autopilot_cycle(
    session: AsyncSession,
    *,
    organization_id: UUID,
    dry_run: bool,
    max_actions: int,
    create_followups: bool,
) -> Dict[str, Any]:
    imported = await ensure_leads_from_threads(
        session,
        organization_id=organization_id,
        max_items=max_actions,
    )

    leads = list(
        (
            await session.execute(
                select(CrmLead)
                .where(CrmLead.organization_id == organization_id)
                .order_by(CrmLead.updated_at.desc())
                .limit(max(1, min(max_actions, 500)))
            )
        )
        .scalars()
        .all()
    )

    actions: List[Dict[str, Any]] = []
    if dry_run:
        for lead in leads[:max_actions]:
            suggested_stage = _stage_from_score(float(lead.score or 0.0))
            actions.append(
                {
                    "action": "stage_recommendation",
                    "status": "suggested",
                    "reason": f"score={float(lead.score or 0.0):.2f}",
                    "payload": {"lead_id": str(lead.id), "suggested_stage": suggested_stage},
                }
            )
        return {
            "dry_run": True,
            "scanned_leads": len(leads),
            "action_count": len(actions),
            "actions": actions,
            "generated_at": _now(),
            "imported_from_omnichannel": imported,
        }

    for lead in leads[:max_actions]:
        suggested_stage = _stage_from_score(float(lead.score or 0.0))
        if str(lead.status or "").strip().lower() == suggested_stage:
            continue

        updated = await patch_lead(
            session,
            organization_id=organization_id,
            lead_id=lead.id,
            payload={"status": suggested_stage},
        )
        if not updated:
            continue

        evt = SalesPipelineEvent(
            organization_id=organization_id,
            thread_id=None,
            lead_id=lead.id,
            customer_id=None,
            stage=suggested_stage,
            event_type="stage_auto_advanced",
            score=float(updated.score or 0.0),
            event_metadata={"source": "sales_autopilot"},
        )
        session.add(evt)
        await session.commit()
        await session.refresh(evt)

        actions.append(
            {
                "action": "advance_stage",
                "status": "ok",
                "payload": {"lead_id": str(lead.id), "stage": suggested_stage, "event_id": str(evt.id)},
            }
        )

    if create_followups:
        followups = await run_followups(
            session,
            organization_id=organization_id,
            max_jobs=max(1, min(max_actions, 200)),
            dry_run=False,
            predictive=True,
        )
        actions.append(
            {
                "action": "predictive_followups",
                "status": "ok",
                "payload": {"executed": int(followups.get("executed", 0)), "skipped": int(followups.get("skipped", 0))},
            }
        )

    return {
        "dry_run": False,
        "scanned_leads": len(leads),
        "action_count": len(actions),
        "actions": actions,
        "generated_at": _now(),
        "imported_from_omnichannel": imported,
    }


async def get_sales_pipeline(
    session: AsyncSession,
    *,
    organization_id: UUID,
) -> Dict[str, Any]:
    rows = list(
        (
            await session.execute(
                select(
                    CrmLead.status,
                    func.count(CrmLead.id),
                    func.avg(CrmLead.conversion_probability),
                ).where(CrmLead.organization_id == organization_id).group_by(CrmLead.status)
            )
        )
        .all()
    )
    items = [
        {
            "stage": str(status or "unknown"),
            "count": int(count or 0),
            "conversion_probability_avg": round(float(avg or 0.0), 4),
        }
        for status, count, avg in rows
    ]
    total = sum(item["count"] for item in items)
    return {"items": items, "total_leads": int(total), "updated_at": _now()}


async def advance_lead_stage(
    session: AsyncSession,
    *,
    organization_id: UUID,
    lead_id: UUID,
    stage: str,
    score: float | None,
    reason: str | None,
    metadata: Dict[str, Any],
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {"status": str(stage)}
    if score is not None:
        payload["score"] = float(score)
    updated = await patch_lead(
        session,
        organization_id=organization_id,
        lead_id=lead_id,
        payload=payload,
    )
    if not updated:
        raise ValueError("Lead not found")

    evt = SalesPipelineEvent(
        organization_id=organization_id,
        thread_id=None,
        lead_id=lead_id,
        customer_id=None,
        stage=str(stage),
        event_type="stage_manual_advance",
        score=float(updated.score or 0.0),
        event_metadata={"reason": reason, **(metadata or {})},
    )
    session.add(evt)
    await session.commit()
    await session.refresh(evt)
    return {
        "lead_id": str(lead_id),
        "stage": str(stage),
        "score": float(updated.score or 0.0),
        "event_id": str(evt.id),
        "updated_at": _now(),
    }

