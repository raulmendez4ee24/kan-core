"""Microsoft Teams webhook endpoint.

Receives Activity payloads from the Bot Framework (Azure Bot Service).
For simple Incoming Webhook integrations, Teams sends a POST with a JSON
Activity object.

Required env vars:
    TEAMS_CHANNEL_CLIENT_MAP_JSON — {"channel_id": "org_uuid", "*": "fallback_uuid"}
    TEAMS_WEBHOOK_URL             — Default outgoing webhook URL
    ENABLE_TEAMS_REPLY            — "true" to enable outbound messages

No request signature verification is required for Bot Framework activities
received inside Teams when using Incoming Webhooks (trust is network-level).
For App registrations, Bearer JWT validation can be added later.
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from fastapi import APIRouter, BackgroundTasks, Request, Response

from brain.dispatcher import handle_teams_event

logger = logging.getLogger("kan_core.routes.teams")

router = APIRouter(prefix="/webhooks/teams", tags=["teams"])


@router.post("")
async def teams_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
) -> Response:
    """Receive Teams Bot Framework activity payload."""
    try:
        payload: Dict[str, Any] = await request.json()
    except Exception:
        return Response(status_code=400)

    background_tasks.add_task(handle_teams_event, payload)
    return Response(status_code=200)
