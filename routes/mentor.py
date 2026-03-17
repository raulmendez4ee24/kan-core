from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from brain.business_mentor import BusinessMentor
from database import get_session
from security import validate_client_credentials

router = APIRouter(prefix="/mentor", tags=["mentor"])

# ---------------------------------------------------------------------------
# In-memory job store  (single-process; fine for Railway)
# ---------------------------------------------------------------------------

_jobs: dict[str, dict[str, Any]] = {}


def _default_client_id() -> str:
    for env_name in ("KAN_CLIENT_ID", "CLIENT_ID", "YCLOUD_DEFAULT_CLIENT_ID"):
        value = str(os.getenv(env_name) or "").strip()
        if value:
            return value
    return ""


async def require_mentor_token(
    x_client_token: str = Header(..., alias="X-Client-Token"),
    x_client_id: str | None = Header(default=None, alias="X-Client-Id"),
    session: AsyncSession = Depends(get_session),
) -> str:
    client_id = str(x_client_id or "").strip() or _default_client_id()
    if not client_id:
        raise HTTPException(status_code=401, detail="Missing client id")
    return await validate_client_credentials(
        session,
        x_client_id=client_id,
        x_client_token=x_client_token,
    )


def _new_job(question: str) -> str:
    job_id = str(uuid.uuid4())
    _jobs[job_id] = {
        "job_id": job_id,
        "status": "processing",
        "question": question,
        "answer": None,
        "error": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "completed_at": None,
    }
    return job_id


def _finish_job(job_id: str, *, answer: str | None = None, error: str | None = None) -> None:
    if job_id not in _jobs:
        return
    _jobs[job_id].update(
        status="done" if answer else "error",
        answer=answer,
        error=error,
        completed_at=datetime.now(timezone.utc).isoformat(),
    )


# ---------------------------------------------------------------------------
# Background worker
# ---------------------------------------------------------------------------


async def _process_question(job_id: str, question: str) -> None:
    mentor = BusinessMentor()
    try:
        answer = await mentor.answer_question(question)
        _finish_job(job_id, answer=answer)
        # Send answer to Raul via WhatsApp
        await mentor._send_to_raul(f"Mentor /ask:\n\n{answer}")
    except Exception as exc:
        _finish_job(job_id, error=str(exc))


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class MentorAskRequest(BaseModel):
    question: str


class MentorAskAccepted(BaseModel):
    job_id: str
    status: str = "processing"


class MentorJobStatus(BaseModel):
    job_id: str
    status: str          # processing | done | error
    question: str
    answer: str | None = None
    error: str | None = None
    created_at: str
    completed_at: str | None = None


class MentorBriefingResponse(BaseModel):
    status: str
    message_preview: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/ask", response_model=MentorAskAccepted, status_code=202)
async def mentor_ask(
    req: MentorAskRequest,
    background_tasks: BackgroundTasks,
    _: str = Depends(require_mentor_token),
):
    """
    Receive a question and return immediately with a job_id.
    The Anthropic call runs in background; answer is sent to Raul via WhatsApp when done.
    Poll GET /mentor/ask/{job_id} to check status.
    """
    question = str(req.question or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="question is required")

    job_id = _new_job(question)
    background_tasks.add_task(_process_question, job_id, question)
    return MentorAskAccepted(job_id=job_id)


@router.get("/ask/{job_id}", response_model=MentorJobStatus)
async def mentor_ask_status(
    job_id: str,
    _: str = Depends(require_mentor_token),
):
    """Poll the status of a /mentor/ask job."""
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    return MentorJobStatus(**job)


@router.post("/briefing", response_model=MentorBriefingResponse)
async def mentor_send_briefing(
    _: str = Depends(require_mentor_token),
):
    mentor = BusinessMentor()
    briefing = await mentor.daily_briefing()
    preview = str(briefing.message or "").strip()
    if len(preview) > 240:
        preview = preview[:237] + "..."
    return MentorBriefingResponse(
        status="sent",
        message_preview=preview,
    )
