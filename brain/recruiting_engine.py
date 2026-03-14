from datetime import datetime, timezone
from typing import Any, Dict, List
from uuid import UUID

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from models import RecruitingCandidate, RecruitingInterview, RecruitingScore


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _tokenize(items: List[str]) -> List[str]:
    out: List[str] = []
    for item in items:
        token = str(item or "").strip().lower()
        if token and token not in out:
            out.append(token)
    return out


def _score_candidate(
    cv_text: str,
    *,
    must_have: List[str],
    nice_to_have: List[str],
    reject: List[str],
) -> Dict[str, Any]:
    text = str(cv_text or "").lower()
    must = _tokenize(must_have)
    nice = _tokenize(nice_to_have)
    rej = _tokenize(reject)

    must_hits = sum(1 for k in must if k in text)
    nice_hits = sum(1 for k in nice if k in text)
    reject_hits = sum(1 for k in rej if k in text)

    must_score = (must_hits / len(must)) if must else 1.0
    nice_score = (nice_hits / len(nice)) if nice else 0.5
    reject_penalty = min(reject_hits * 0.2, 0.8)

    total = max(0.0, min((must_score * 0.7) + (nice_score * 0.3) - reject_penalty, 1.0))
    if total >= 0.75:
        recommendation = "strong_hire"
    elif total >= 0.55:
        recommendation = "interview"
    elif total >= 0.35:
        recommendation = "maybe"
    else:
        recommendation = "reject"

    return {
        "total_score": round(total, 4),
        "recommendation": recommendation,
        "dimensions": {
            "must_hits": must_hits,
            "must_total": len(must),
            "nice_hits": nice_hits,
            "nice_total": len(nice),
            "reject_hits": reject_hits,
        },
    }


async def import_candidates(
    session: AsyncSession,
    *,
    organization_id: UUID,
    job_id: str,
    candidates: List[Dict[str, Any]],
) -> List[RecruitingCandidate]:
    inserted: List[RecruitingCandidate] = []
    for item in candidates:
        row = RecruitingCandidate(
            organization_id=organization_id,
            job_id=str(job_id),
            name=str(item.get("name") or "").strip(),
            email=(str(item.get("email")).strip() if item.get("email") else None),
            phone=(str(item.get("phone")).strip() if item.get("phone") else None),
            source=(str(item.get("source")).strip() if item.get("source") else None),
            cv_text=str(item.get("cv_text") or ""),
            score=0.0,
            status="new",
            candidate_metadata=dict(item.get("metadata") or {}),
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        inserted.append(row)
    return inserted


async def screen_candidate(
    session: AsyncSession,
    *,
    organization_id: UUID,
    candidate_id: UUID,
    must_have: List[str],
    nice_to_have: List[str],
    reject: List[str],
    metadata: Dict[str, Any],
) -> Dict[str, Any]:
    candidate = await session.get(RecruitingCandidate, candidate_id)
    if not candidate or candidate.organization_id != organization_id:
        raise ValueError("Candidate not found")

    score_payload = _score_candidate(
        candidate.cv_text,
        must_have=must_have,
        nice_to_have=nice_to_have,
        reject=reject,
    )
    candidate.score = float(score_payload["total_score"])
    candidate.status = "screened"
    candidate.candidate_metadata = {**(candidate.candidate_metadata or {}), **(metadata or {})}
    await session.commit()
    await session.refresh(candidate)

    rationale = (
        f"must_hits={score_payload['dimensions']['must_hits']}/{score_payload['dimensions']['must_total']}, "
        f"nice_hits={score_payload['dimensions']['nice_hits']}/{score_payload['dimensions']['nice_total']}, "
        f"reject_hits={score_payload['dimensions']['reject_hits']}"
    )
    score_row = RecruitingScore(
        organization_id=organization_id,
        candidate_id=candidate.id,
        dimensions=dict(score_payload["dimensions"]),
        total_score=float(score_payload["total_score"]),
        recommendation=str(score_payload["recommendation"]),
        rationale=rationale,
    )
    session.add(score_row)
    await session.commit()
    await session.refresh(score_row)
    return {
        "candidate_id": str(candidate.id),
        "score": float(score_row.total_score),
        "recommendation": str(score_row.recommendation or ""),
        "rationale": str(score_row.rationale or ""),
        "dimensions": dict(score_row.dimensions or {}),
        "created_at": score_row.created_at,
    }


async def schedule_interview(
    session: AsyncSession,
    *,
    organization_id: UUID,
    candidate_id: UUID,
    scheduled_at: datetime,
    channel: str,
    interviewer: str | None,
    metadata: Dict[str, Any],
) -> RecruitingInterview:
    candidate = await session.get(RecruitingCandidate, candidate_id)
    if not candidate or candidate.organization_id != organization_id:
        raise ValueError("Candidate not found")

    row = RecruitingInterview(
        organization_id=organization_id,
        candidate_id=candidate_id,
        scheduled_at=scheduled_at,
        channel=str(channel or "meet"),
        interviewer=(str(interviewer).strip() if interviewer else None),
        status="scheduled",
        result=dict(metadata or {}),
    )
    session.add(row)
    candidate.status = "interview_scheduled"
    await session.commit()
    await session.refresh(row)
    return row


async def ranking_by_job(
    session: AsyncSession,
    *,
    organization_id: UUID,
    job_id: str,
) -> List[RecruitingCandidate]:
    stmt = (
        select(RecruitingCandidate)
        .where(
            RecruitingCandidate.organization_id == organization_id,
            RecruitingCandidate.job_id == str(job_id),
        )
        .order_by(desc(RecruitingCandidate.score), desc(RecruitingCandidate.updated_at))
    )
    return list((await session.execute(stmt)).scalars().all())

