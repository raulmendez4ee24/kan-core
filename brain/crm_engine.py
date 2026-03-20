import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy import desc, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from brain.data_mapper import auto_map_fields
from brain.policy_engine import load_effective_policy
from brain.predictor_sales import (
    generate_best_contact_time_predictions,
    generate_lead_churn_predictions,
)
from models import CrmInteraction, CrmLead, FollowUpJob
from tools.n8n_client import send_to_n8n_with_response


def _to_tags(value: Any) -> List[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [x.strip() for x in value.split(",") if x.strip()]
    return []


def _lead_score(payload: Dict[str, Any]) -> float:
    score = 0.0
    if payload.get("email"):
        score += 0.25
    if payload.get("phone"):
        score += 0.2
    if payload.get("source"):
        score += 0.1
    if payload.get("industry"):
        score += 0.1
    tags = _to_tags(payload.get("tags"))
    score += min(len(tags) * 0.05, 0.15)
    return round(min(score, 0.95), 4)


async def upsert_lead(
    session: AsyncSession,
    *,
    organization_id: UUID,
    payload: Dict[str, Any],
) -> CrmLead:
    mapped, trace = auto_map_fields(
        payload,
        allowed_fields={
            "external_id",
            "name",
            "email",
            "phone",
            "status",
            "source",
            "industry",
            "owner",
            "tags",
            "score",
            "conversion_probability",
            "metadata",
        },
    )

    external_id = mapped.get("external_id")
    external_id_str = str(external_id) if external_id else None
    email = mapped.get("email")
    phone = mapped.get("phone")

    stmt = select(CrmLead).where(CrmLead.organization_id == organization_id)
    if external_id_str:
        stmt = stmt.where(CrmLead.external_id == external_id_str)
    elif email or phone:
        conditions = []
        if email:
            conditions.append(CrmLead.email == str(email))
        if phone:
            conditions.append(CrmLead.phone == str(phone))
        stmt = stmt.where(or_(*conditions))
    else:
        stmt = stmt.where(CrmLead.id == UUID("00000000-0000-0000-0000-000000000000"))

    lead = (await session.execute(stmt)).scalars().first()
    now = datetime.now(timezone.utc)

    if not lead:
        lead = CrmLead(
            organization_id=organization_id,
            external_id=external_id_str,
            name=str(mapped.get("name")) if mapped.get("name") else None,
            email=str(email) if email else None,
            phone=str(phone) if phone else None,
            status=str(mapped.get("status") or "new"),
            source=str(mapped.get("source")) if mapped.get("source") else None,
            industry=str(mapped.get("industry")) if mapped.get("industry") else None,
            owner=str(mapped.get("owner")) if mapped.get("owner") else None,
            tags={"items": _to_tags(mapped.get("tags"))},
            lead_metadata={
                "trace": trace,
                "input": payload,
                **(mapped.get("metadata") or {}),
            },
            score=float(mapped.get("score") or _lead_score(mapped)),
            conversion_probability=float(
                mapped.get("conversion_probability")
                if mapped.get("conversion_probability") is not None
                else _lead_score(mapped)
            ),
            last_interaction_at=now,
        )
        session.add(lead)
        await session.commit()
        await session.refresh(lead)
        return lead

    if mapped.get("name"):
        lead.name = str(mapped["name"])
    if external_id_str:
        lead.external_id = external_id_str
    if email:
        lead.email = str(email)
    if phone:
        lead.phone = str(phone)
    if mapped.get("status"):
        lead.status = str(mapped["status"])
    if mapped.get("source"):
        lead.source = str(mapped["source"])
    if mapped.get("industry"):
        lead.industry = str(mapped["industry"])
    if mapped.get("owner"):
        lead.owner = str(mapped["owner"])

    new_tags = _to_tags(mapped.get("tags"))
    if new_tags:
        existing_tags = _to_tags((lead.tags or {}).get("items"))
        merged = sorted(set(existing_tags + new_tags))
        lead.tags = {"items": merged}

    metadata = dict(lead.lead_metadata or {})
    metadata["trace"] = trace
    metadata.update(mapped.get("metadata") or {})
    lead.lead_metadata = metadata

    if mapped.get("score") is not None:
        lead.score = float(mapped["score"])
    else:
        lead.score = _lead_score(
            {
                "email": lead.email,
                "phone": lead.phone,
                "source": lead.source,
                "industry": lead.industry,
                "tags": (lead.tags or {}).get("items", []),
            }
        )

    if mapped.get("conversion_probability") is not None:
        lead.conversion_probability = float(mapped["conversion_probability"])
    else:
        lead.conversion_probability = max(lead.conversion_probability, lead.score)

    lead.last_interaction_at = now
    await session.commit()
    await session.refresh(lead)
    return lead


async def patch_lead(
    session: AsyncSession,
    *,
    organization_id: UUID,
    lead_id: UUID,
    payload: Dict[str, Any],
) -> Optional[CrmLead]:
    stmt = select(CrmLead).where(
        CrmLead.organization_id == organization_id,
        CrmLead.id == lead_id,
    )
    lead = (await session.execute(stmt)).scalars().first()
    if not lead:
        return None

    for key in ["name", "email", "phone", "status", "source", "industry", "owner"]:
        value = payload.get(key)
        if value is not None:
            setattr(lead, key, str(value) if value != "" else None)

    if payload.get("tags") is not None:
        lead.tags = {"items": _to_tags(payload.get("tags"))}

    if payload.get("metadata") is not None:
        metadata = dict(lead.lead_metadata or {})
        metadata.update(payload.get("metadata") or {})
        lead.lead_metadata = metadata

    if payload.get("score") is not None:
        lead.score = float(payload["score"])
    if payload.get("conversion_probability") is not None:
        lead.conversion_probability = float(payload["conversion_probability"])

    await session.commit()
    await session.refresh(lead)
    return lead


async def list_leads(
    session: AsyncSession,
    *,
    organization_id: UUID,
    status: Optional[str] = None,
    limit: int = 100,
) -> List[CrmLead]:
    stmt = select(CrmLead).where(CrmLead.organization_id == organization_id)
    if status:
        stmt = stmt.where(CrmLead.status == status)
    stmt = stmt.order_by(desc(CrmLead.updated_at)).limit(max(1, min(limit, 500)))
    return list((await session.execute(stmt)).scalars().all())


async def record_interaction(
    session: AsyncSession,
    *,
    organization_id: UUID,
    payload: Dict[str, Any],
) -> CrmInteraction:
    lead_id = payload.get("lead_id")
    lead_uuid = None
    if lead_id:
        try:
            lead_uuid = UUID(str(lead_id))
        except Exception:
            lead_uuid = None

    interaction = CrmInteraction(
        organization_id=organization_id,
        lead_id=lead_uuid,
        channel=str(payload.get("channel") or "chat"),
        direction=str(payload.get("direction") or "inbound"),
        content=str(payload.get("content") or ""),
        interaction_metadata=dict(payload.get("metadata") or {}),
    )
    session.add(interaction)

    if lead_uuid:
        lead_stmt = select(CrmLead).where(
            CrmLead.organization_id == organization_id,
            CrmLead.id == lead_uuid,
        )
        lead = (await session.execute(lead_stmt)).scalars().first()
        if lead:
            lead.last_interaction_at = datetime.now(timezone.utc)

    await session.commit()
    await session.refresh(interaction)
    return interaction


async def list_interactions(
    session: AsyncSession,
    *,
    organization_id: UUID,
    lead_id: Optional[UUID] = None,
    limit: int = 100,
) -> List[CrmInteraction]:
    stmt = select(CrmInteraction).where(CrmInteraction.organization_id == organization_id)
    if lead_id:
        stmt = stmt.where(CrmInteraction.lead_id == lead_id)
    stmt = stmt.order_by(desc(CrmInteraction.created_at)).limit(max(1, min(limit, 500)))
    return list((await session.execute(stmt)).scalars().all())


async def run_followups(
    session: AsyncSession,
    *,
    organization_id: UUID,
    max_jobs: int,
    dry_run: bool,
    predictive: bool = False,
) -> Dict[str, Any]:
    now = datetime.now(timezone.utc)
    stale_before = now - timedelta(hours=12)
    enable_predictive = predictive or os.getenv("ENABLE_PREDICTIVE_AUTOMATION", "false").lower() in {"1", "true", "yes"}

    hour_by_lead: Dict[str, int] = {}
    churn_by_lead: Dict[str, float] = {}
    business_start = 9
    business_end = 19
    if enable_predictive:
        try:
            policy = await load_effective_policy(session, organization_id=organization_id)
            business_start, business_end = policy.business_hours()
        except Exception:
            business_start, business_end = 9, 19
        try:
            churn_records = await generate_lead_churn_predictions(session, organization_id=organization_id, limit=max_jobs * 4)
            churn_by_lead = {str(r.entity_id): float(r.prediction_value or 0.0) for r in churn_records}
        except Exception:
            churn_by_lead = {}
        try:
            hour_records = await generate_best_contact_time_predictions(session, organization_id=organization_id, limit=max_jobs * 4)
            hour_by_lead = {
                str(r.entity_id): int(float(r.prediction_value or 10.0))
                for r in hour_records
            }
        except Exception:
            hour_by_lead = {}

    respect_schedule = os.getenv("PREDICTIVE_FOLLOWUPS_RESPECT_SCHEDULE", "false").lower() in {"1", "true", "yes"}

    def _within_business_hours(ts: datetime) -> bool:
        if business_end <= business_start:
            return True
        return business_start <= int(ts.hour) < business_end

    def _next_scheduled_time(target_hour: int) -> datetime:
        hour = int(target_hour)
        if hour < 0 or hour > 23:
            hour = business_start
        # Clamp to business hours if configured.
        if business_start <= business_end:
            if hour < business_start or hour >= business_end:
                hour = business_start
        candidate = now.replace(hour=hour, minute=0, second=0, microsecond=0)
        if candidate <= now:
            candidate = candidate + timedelta(days=1)
        return candidate

    leads_stmt = (
        select(CrmLead)
        .where(
            CrmLead.organization_id == organization_id,
            CrmLead.status.in_(["new", "contacted", "qualified"]),
            or_(CrmLead.last_interaction_at.is_(None), CrmLead.last_interaction_at <= stale_before),
        )
        .order_by(desc(CrmLead.score), desc(CrmLead.updated_at))
        .limit(max_jobs)
    )
    leads = list((await session.execute(leads_stmt)).scalars().all())

    jobs: List[FollowUpJob] = []
    skipped = 0
    for lead in leads:
        if lead.conversion_probability < 0.25:
            skipped += 1
            continue
        scheduled_for = now
        decision_bits: List[str] = [
            f"Lead stale >12h con conversion_probability={lead.conversion_probability:.2f}",
        ]
        if enable_predictive:
            lead_key = str(lead.id)
            churn = float(churn_by_lead.get(lead_key, 0.0))
            best_hour = int(hour_by_lead.get(lead_key, business_start))
            if not _within_business_hours(now):
                scheduled_for = _next_scheduled_time(business_start)
            elif respect_schedule:
                scheduled_for = _next_scheduled_time(best_hour)
            else:
                scheduled_for = now
            decision_bits.append(f"churn_risk={churn:.2f}")
            decision_bits.append(f"best_contact_hour={best_hour}")

        status = "scheduled"
        if not dry_run and scheduled_for <= now:
            status = "running"
        job = FollowUpJob(
            organization_id=organization_id,
            lead_id=lead.id,
            status=status,
            scheduled_for=scheduled_for,
            decision_reason=(
                "; ".join(decision_bits)[:800]
            ),
            payload={
                "lead_id": str(lead.id),
                "channel": "messaging",
                "message": "Hola, te ayudamos a completar tu proceso.",
                "predictive": bool(enable_predictive),
                "scheduled_for": scheduled_for.isoformat(),
            },
            result={},
        )
        session.add(job)
        jobs.append(job)

    await session.commit()

    executed = 0
    if not dry_run:
        for job in jobs:
            if job.status != "running":
                continue
            payload = dict(job.payload or {})
            result = await send_to_n8n_with_response(
                {
                    "source": "followup_scheduler",
                    "organization_id": str(organization_id),
                    "job_id": str(job.id),
                    "payload": payload,
                }
            )
            dispatched = bool(result.get("dispatched"))
            execution_status = str(result.get("execution_status") or "not_dispatched")
            confirmed = bool(result.get("confirmed"))
            if dispatched and confirmed and execution_status == "success":
                job.status = "completed"
            elif dispatched and execution_status not in {"error", "failed", "canceled", "crashed"}:
                job.status = "pending"
            else:
                job.status = "failed"
            job.executed_at = datetime.now(timezone.utc)
            job.result = {
                "dispatched": dispatched,
                "pending": bool(result.get("pending")),
                "confirmed": confirmed,
                "execution_status": execution_status,
            }
            executed += 1 if dispatched else 0
        await session.commit()

    for job in jobs:
        await session.refresh(job)

    return {
        "jobs": jobs,
        "executed": executed,
        "skipped": skipped,
    }


async def dispatch_due_followups(
    session: AsyncSession,
    *,
    organization_id: UUID,
    limit: int = 50,
) -> Dict[str, Any]:
    """Dispatch scheduled followup jobs that are due.

    Intended to be called from a background runner/cron.
    """
    now = datetime.now(timezone.utc)
    stmt = (
        select(FollowUpJob)
        .where(
            FollowUpJob.organization_id == organization_id,
            FollowUpJob.status == "scheduled",
            FollowUpJob.scheduled_for <= now,
        )
        .order_by(FollowUpJob.scheduled_for.asc())
        .limit(max(1, min(limit, 500)))
    )
    jobs = list((await session.execute(stmt)).scalars().all())
    dispatched = 0
    for job in jobs:
        payload = dict(job.payload or {})
        if str(payload.get("kind") or "") == "tomorrow_agenda_snapshot":
            from brain.commercial_agenda import (
                build_tomorrow_agenda_snapshot,
                ensure_tomorrow_agenda_snapshot_job,
            )

            try:
                snapshot = await build_tomorrow_agenda_snapshot(
                    session,
                    organization_id=organization_id,
                )
                job.status = "completed"
                job.executed_at = datetime.now(timezone.utc)
                job.result = {
                    "snapshot_generated": True,
                    "target_date": snapshot.get("target_date"),
                    "source_status": snapshot.get("source_status"),
                }
                await session.commit()
                await ensure_tomorrow_agenda_snapshot_job(session, organization_id=organization_id)
                dispatched += 1
            except Exception as exc:
                job.status = "failed"
                job.executed_at = datetime.now(timezone.utc)
                job.result = {
                    "snapshot_generated": False,
                    "error": str(exc),
                }
                await session.commit()
            continue
        result = await send_to_n8n_with_response(
            {
                "source": "followup_dispatcher",
                "organization_id": str(organization_id),
                "job_id": str(job.id),
                "payload": payload,
            }
        )
        dispatched_ok = bool(result.get("dispatched"))
        execution_status = str(result.get("execution_status") or "not_dispatched")
        confirmed = bool(result.get("confirmed"))
        if dispatched_ok and confirmed and execution_status == "success":
            job.status = "completed"
        elif dispatched_ok and execution_status not in {"error", "failed", "canceled", "crashed"}:
            job.status = "pending"
        else:
            job.status = "failed"
        job.executed_at = datetime.now(timezone.utc)
        job.result = {
            "dispatched": dispatched_ok,
            "pending": bool(result.get("pending")),
            "confirmed": confirmed,
            "execution_status": execution_status,
        }
        dispatched += 1 if dispatched_ok else 0
    await session.commit()
    for job in jobs:
        await session.refresh(job)
    return {"jobs": jobs, "dispatched": dispatched}
