from __future__ import annotations

import os
import time
import uuid
from collections import defaultdict
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from brain.action_policy import requires_human_approval
from brain.business_monitor import ingest_business_event
from brain.core import CognitiveEngine
from database import AsyncSessionLocal
from execution.desktop_executor import is_desktop_action, send_to_desktop_agent
from observability import capture_exception
from schemas.action_contract import ActionContract
from schemas.chat import ChatMessageRequest, ChatMessageResponse
from security import require_client_token
from tools.n8n_client import send_to_n8n_with_response

router = APIRouter(prefix="/chat", tags=["chat"])

# In-memory rate limiter: tracks request timestamps per client_id.
_rate_limit_store: dict = defaultdict(list)
_RATE_LIMIT_RPM = int(os.getenv("CHAT_RATE_LIMIT_RPM", "60"))


def _check_rate_limit(client_id: str) -> bool:
    """Return True if the client is within the allowed rate, False if exceeded."""
    now = time.monotonic()
    window = 60.0
    calls = _rate_limit_store[client_id]
    _rate_limit_store[client_id] = [t for t in calls if now - t < window]
    if len(_rate_limit_store[client_id]) >= _RATE_LIMIT_RPM:
        return False
    _rate_limit_store[client_id].append(now)
    return True


async def _record_chat_business_events(
    *,
    client_id: str,
    payload: ChatMessageRequest,
    ai_result: dict,
) -> None:
    try:
        org_id = UUID(client_id)
    except ValueError:
        return

    try:
        async with AsyncSessionLocal() as session:
            await ingest_business_event(
                session,
                organization_id=org_id,
                event_type="message_in",
                source="chat.message",
                channel=payload.channel,
                entity_id=payload.session_id,
                payload={
                    "session_id": payload.session_id,
                    "user_id": payload.user_id,
                    "message": payload.message,
                    "metadata": payload.metadata,
                },
            )
            await ingest_business_event(
                session,
                organization_id=org_id,
                event_type="message_out",
                source="chat.message",
                channel=payload.channel,
                entity_id=payload.session_id,
                payload={
                    "session_id": payload.session_id,
                    "type": ai_result.get("type"),
                    "action": ai_result.get("payload") if ai_result.get("type") == "action" else None,
                },
            )
    except Exception:
        # Event ingestion is best-effort and cannot block chat responses.
        return


async def _maybe_record_autonomous_case(
    *,
    client_id: str,
    payload: ChatMessageRequest,
    action: Optional[dict],
    action_id: Optional[str],
) -> None:
    if os.getenv("ENABLE_AUTONOMOUS_CASE_MANAGER", "false").lower() not in {"1", "true", "yes"}:
        return
    try:
        org_id = UUID(client_id)
    except ValueError:
        return

    try:
        from brain.autonomous_case_manager import create_or_update_case

        async with AsyncSessionLocal() as session:
            await create_or_update_case(
                session,
                organization_id=org_id,
                case_type="auto",
                current_goal=payload.message,
                business_context={
                    "source": "chat.message",
                    "session_id": payload.session_id,
                    "user_id": payload.user_id,
                    "channel": payload.channel,
                    "case_key": str(action_id or payload.session_id),
                    "action": action or {},
                    "metadata": payload.metadata,
                },
                run_now=bool(action),
            )
    except Exception:
        return


def _validate_action_payload(payload: Optional[dict]) -> Optional[ActionContract]:
    if not isinstance(payload, dict):
        return None
    try:
        return ActionContract.model_validate(payload)
    except Exception:
        return None


def _build_desktop_metadata(data: ChatMessageRequest, action_model: ActionContract) -> dict:
    return {
        "session_id": data.session_id,
        "user_id": data.user_id,
        "channel": data.channel,
        "message": data.message,
        "rationale": action_model.rationale,
        "integration": action_model.integration,
        "action_type": action_model.action_type,
        "metadata": data.metadata,
    }


@router.post("/message", response_model=ChatMessageResponse)
async def chat_message(
    data: ChatMessageRequest,
    client_id: str = Depends(require_client_token),
):
    if not _check_rate_limit(client_id):
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded: max {_RATE_LIMIT_RPM} requests/minute per client.",
            headers={"Retry-After": "60"},
        )

    try:
        engine = CognitiveEngine.get_instance()
        result = await engine.generate_response(
            client_id=client_id,
            session_id=data.session_id,
            message=data.message,
        )
    except Exception as exc:
        capture_exception(exc, {"source": "chat", "client_id": client_id})
        raise HTTPException(status_code=502, detail="AI engine failed") from exc

    await _record_chat_business_events(
        client_id=client_id,
        payload=data,
        ai_result=result,
    )

    if result.get("type") != "action":
        return ChatMessageResponse(type="message", content=result.get("content", ""))

    action_model = _validate_action_payload(result.get("payload"))
    action = action_model.model_dump() if action_model else None
    dispatched = False
    action_id: str | None = None
    approval_required = bool(action_model and requires_human_approval(action_model))
    approval_status = "not_required"

    if approval_required:
        approval_status = "approved" if data.human_approval_granted else "pending"

    if data.auto_dispatch_actions and action:
        action_id = str(uuid.uuid4())
        if approval_required and not data.human_approval_granted:
            await send_to_n8n_with_response(
                {
                    "source": "chat_action_approval_required",
                    "action_id": action_id,
                    "client_id": client_id,
                    "session_id": data.session_id,
                    "user_id": data.user_id,
                    "channel": data.channel,
                    "metadata": data.metadata,
                    "message": data.message,
                    "action": action,
                    "approval": {"required": True, "status": "pending"},
                }
            )
        elif action_model and is_desktop_action(action_model, data.metadata):
            desktop_result = await send_to_desktop_agent(
                client_id=client_id,
                action=action_model.action,
                parameters=action_model.arguments,
                metadata=_build_desktop_metadata(data, action_model),
                requested_by_user_id=(data.approver_id or data.user_id),
            )
            dispatched = bool(desktop_result.get("accepted"))
            if not dispatched and str(desktop_result.get("reason") or "") == "runner_offline":
                from execution.desktop_executor import runner_offline_instructions
                return ChatMessageResponse(
                    type="message",
                    content=runner_offline_instructions(),
                )
            desktop_command_id = desktop_result.get("command_id")
            if isinstance(desktop_command_id, str) and desktop_command_id:
                action_id = desktop_command_id
        else:
            dispatch_result = await send_to_n8n_with_response(
                {
                    "source": "chat_action",
                    "action_id": action_id,
                    "client_id": client_id,
                    "session_id": data.session_id,
                    "user_id": data.user_id,
                    "channel": data.channel,
                    "metadata": data.metadata,
                    "message": data.message,
                    "action": action,
                    "approval": {
                        "required": approval_required,
                        "status": "approved" if approval_required else "not_required",
                        "approver_id": data.approver_id,
                    },
                }
            )
            dispatched = bool(dispatch_result.get("dispatched"))

    await _maybe_record_autonomous_case(
        client_id=client_id,
        payload=data,
        action=action,
        action_id=action_id,
    )

    return ChatMessageResponse(
        type="action",
        content=result.get("content", ""),
        action=action,
        action_dispatched=dispatched,
        action_id=action_id,
        approval_required=approval_required,
        approval_status=approval_status,
    )
