"""Proactive Outreach API.

Allows external systems (n8n, cron jobs, webhooks) to trigger LLM-generated
messages sent proactively to users or channels without waiting for user input.

Endpoints:
    POST /outreach/send     — Trigger a one-off proactive outreach
    POST /outreach/bulk     — Trigger multiple outreach jobs (batch, up to 50)

Required env vars:
    ENABLE_PROACTIVE_OUTREACH=true
    Channel-specific vars (TELEGRAM_BOT_TOKEN, SLACK_BOT_TOKEN, etc.)
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from brain.proactive_scheduler import ProactiveOutreach, ProactiveScheduler
from observability import capture_exception
from security import require_client_token

logger = logging.getLogger("kan_core.routes.outreach")

router = APIRouter(prefix="/outreach", tags=["outreach"])

_SUPPORTED_CHANNELS = {"telegram", "slack", "discord", "teams"}


class OutreachRequest(BaseModel):
    channel: str = Field(..., description="telegram | slack | discord | teams")
    target_id: str = Field(
        ...,
        min_length=1,
        max_length=512,
        description="Channel ID, chat ID, or webhook URL depending on the channel.",
    )
    trigger_type: str = Field(
        default="event",
        description="scheduled | event | alert",
    )
    context: Dict[str, Any] = Field(
        default_factory=dict,
        description="Contextual data for the LLM to craft the message.",
    )
    custom_message: Optional[str] = Field(
        default=None,
        max_length=4000,
        description="If provided, skip LLM generation and send this text directly.",
    )
    session_id: Optional[str] = Field(default=None, max_length=128)


class BulkOutreachRequest(BaseModel):
    jobs: List[OutreachRequest] = Field(..., min_length=1, max_length=50)


class OutreachResult(BaseModel):
    dispatched: bool
    channel: str
    target_id: str
    trigger_type: str


class BulkOutreachResult(BaseModel):
    total: int
    dispatched: int
    failed: int
    results: List[OutreachResult]


@router.post("/send", response_model=OutreachResult)
async def outreach_send(
    req: OutreachRequest,
    client_id: str = Depends(require_client_token),
):
    """Trigger a single proactive outreach message.

    The LLM crafts a message from ``context`` unless ``custom_message`` is set.
    """
    if req.channel.lower() not in _SUPPORTED_CHANNELS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported channel '{req.channel}'. Choose: {sorted(_SUPPORTED_CHANNELS)}",
        )

    outreach = ProactiveOutreach(
        client_id=client_id,
        channel=req.channel,
        target_id=req.target_id,
        trigger_type=req.trigger_type,
        context=req.context,
        session_id=req.session_id,
        custom_message=req.custom_message,
    )

    try:
        scheduler = ProactiveScheduler()
        dispatched = await scheduler.run_outreach(outreach)
    except Exception as exc:
        capture_exception(exc, {"source": "outreach_send", "client_id": client_id})
        raise HTTPException(status_code=502, detail="Outreach dispatch failed") from exc

    return OutreachResult(
        dispatched=dispatched,
        channel=req.channel,
        target_id=req.target_id,
        trigger_type=req.trigger_type,
    )


@router.post("/bulk", response_model=BulkOutreachResult)
async def outreach_bulk(
    req: BulkOutreachRequest,
    client_id: str = Depends(require_client_token),
):
    """Trigger multiple proactive outreach jobs concurrently.

    All jobs run in parallel. Invalid channels are marked as failed, not skipped.
    """
    scheduler = ProactiveScheduler()

    async def _run_one(job: OutreachRequest) -> OutreachResult:
        if job.channel.lower() not in _SUPPORTED_CHANNELS:
            return OutreachResult(
                dispatched=False,
                channel=job.channel,
                target_id=job.target_id,
                trigger_type=job.trigger_type,
            )
        outreach = ProactiveOutreach(
            client_id=client_id,
            channel=job.channel,
            target_id=job.target_id,
            trigger_type=job.trigger_type,
            context=job.context,
            session_id=job.session_id,
            custom_message=job.custom_message,
        )
        try:
            ok = await scheduler.run_outreach(outreach)
        except Exception as exc:
            capture_exception(exc, {"source": "outreach_bulk", "client_id": client_id})
            ok = False
        return OutreachResult(
            dispatched=ok,
            channel=job.channel,
            target_id=job.target_id,
            trigger_type=job.trigger_type,
        )

    results = await asyncio.gather(*[_run_one(j) for j in req.jobs])
    dispatched_count = sum(1 for r in results if r.dispatched)

    return BulkOutreachResult(
        total=len(results),
        dispatched=dispatched_count,
        failed=len(results) - dispatched_count,
        results=list(results),
    )
