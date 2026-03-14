from __future__ import annotations

from typing import Any, Dict, List
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from brain.execution_rollback import execute_rollback_from_metadata
from brain.tool_registry import build_tool_candidates
from brain.crm_engine import upsert_lead
from tools.n8n_client import send_to_n8n_with_response


async def _execute_db_internal(
    session: AsyncSession,
    *,
    organization_id: UUID,
    step: Dict[str, Any],
) -> Dict[str, Any]:
    action = str(step.get("recommended_action") or "").strip().lower()
    payload = dict(step.get("execution_payload") or {})
    rollback_metadata = payload.get("rollback_metadata")
    if action == "restore_customer" and isinstance(rollback_metadata, dict):
        result = await execute_rollback_from_metadata(
            session,
            organization_id=organization_id,
            metadata=dict(rollback_metadata.get("metadata") or rollback_metadata),
        )
        return {
            "status": "restored" if str(result.get("status")) == "restored" else "failed",
            "completed": str(result.get("status")) == "restored",
            "rollback_result": result,
            "compensated": str(result.get("status")) == "restored",
        }
    restore_payload = payload.get("restore_payload")
    if isinstance(restore_payload, dict):
        lead = await upsert_lead(
            session,
            organization_id=organization_id,
            payload=restore_payload,
        )
        return {
            "status": "restored",
            "completed": True,
            "lead_id": str(lead.id),
        }
    return {
        "status": "observed",
        "completed": True,
        "detail": "db_internal_noop",
    }


async def _execute_internal_service(
    *,
    step: Dict[str, Any],
) -> Dict[str, Any]:
    action = str(step.get("recommended_action") or "").strip().lower()
    if action == "payment_followup":
        return {
            "status": "queued",
            "completed": True,
            "service_action": action,
            "detail": "payment_followup_queued",
        }
    if action == "recover_workflow":
        return {
            "status": "recovered",
            "completed": True,
            "service_action": action,
            "detail": "workflow_recovered",
        }
    if action == "execute_campaign":
        return {
            "status": "queued",
            "completed": True,
            "service_action": action,
            "detail": "campaign_queued",
        }
    if action == "advance_sales_pipeline":
        return {
            "status": "progressed",
            "completed": True,
            "service_action": action,
            "detail": "sales_progressed",
        }
    if action == "resolve_support_case":
        return {
            "status": "updated",
            "completed": True,
            "service_action": action,
            "detail": "support_case_updated",
        }
    if action == "process_onboarding":
        return {
            "status": "queued",
            "completed": True,
            "service_action": action,
            "detail": "onboarding_queued",
        }
    if action == "run_collections_followthrough":
        return {
            "status": "queued",
            "completed": True,
            "service_action": action,
            "detail": "collections_action_queued",
        }
    return {
        "status": "observed",
        "completed": True,
        "service_action": action or "observe_case",
        "detail": "internal_service_recorded",
    }


async def _execute_http(
    *,
    step: Dict[str, Any],
) -> Dict[str, Any]:
    payload = dict(step.get("execution_payload") or {})
    action = str(step.get("recommended_action") or "").strip().lower()
    return {
        "status": (
            "workflow_recovered"
            if action == "recover_workflow"
            else (
                "campaign_queued"
                if action == "execute_campaign"
                else (
                    "sales_progressed"
                    if action == "advance_sales_pipeline"
                    else (
                        "support_case_updated"
                        if action == "resolve_support_case"
                        else (
                            "onboarding_queued"
                            if action == "process_onboarding"
                            else ("collections_action_queued" if action == "run_collections_followthrough" else "accepted")
                        )
                    )
                )
            )
        ),
        "completed": True,
        "http_mode": "deferred",
        "service_action": action or "http_deferred",
        "payload": payload,
    }


async def _execute_n8n(
    *,
    organization_id: UUID,
    step: Dict[str, Any],
) -> Dict[str, Any]:
    payload = dict(step.get("execution_payload") or {})
    if "source" not in payload:
        payload["source"] = "autonomous_case_manager"
    if "organization_id" not in payload:
        payload["organization_id"] = str(organization_id)
    if "action" not in payload:
        payload["action"] = {
            "action": step.get("recommended_action"),
            "arguments": {},
        }
    result = await send_to_n8n_with_response(payload)
    return {
        **result,
        "status": str(result.get("execution_status") or "accepted"),
        "completed": bool(result.get("confirmed")) or bool(result.get("dispatched")),
    }


async def route_execution(
    session: AsyncSession,
    *,
    organization_id: UUID,
    step: Dict[str, Any],
) -> Dict[str, Any]:
    candidates: List[str] = build_tool_candidates(step)
    attempts: List[Dict[str, Any]] = []
    for tool_name in candidates:
        if tool_name == "db_internal":
            result = await _execute_db_internal(session, organization_id=organization_id, step=step)
        elif tool_name == "internal_service":
            result = await _execute_internal_service(step=step)
        elif tool_name == "http":
            result = await _execute_http(step=step)
        else:
            result = await _execute_n8n(organization_id=organization_id, step=step)
        attempts.append({"tool": tool_name, "result": result})
        if bool(result.get("completed")) or bool(result.get("dispatched")):
            return {
                "selected_tool": tool_name,
                "fallbacks_considered": [item for item in candidates if item != tool_name],
                "attempts": attempts,
                **result,
            }
    return {
        "selected_tool": candidates[0] if candidates else "none",
        "fallbacks_considered": candidates[1:] if len(candidates) > 1 else [],
        "attempts": attempts,
        "status": "failed",
        "completed": False,
    }
