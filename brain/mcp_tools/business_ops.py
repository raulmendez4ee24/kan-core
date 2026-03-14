from __future__ import annotations

from typing import Any, Dict
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from brain.business_state_engine import get_latest_business_state


async def execute_case_action(
    session: AsyncSession,
    *,
    organization_id: UUID,
    args: Dict[str, Any],
) -> Dict[str, Any]:
    from brain.execution_router import route_execution

    step = dict(args.get("step") or {})
    if not step:
        step = {
            "recommended_action": str(args.get("action") or "observe_case"),
            "recommended_tool": str(args.get("tool") or "internal_service"),
            "fallback_tools": list(args.get("fallback_tools") or []),
            "execution_payload": dict(args.get("payload") or {}),
        }
    return await route_execution(session, organization_id=organization_id, step=step)


async def get_case_state_tool(
    session: AsyncSession,
    *,
    organization_id: UUID,
    args: Dict[str, Any],
) -> Dict[str, Any]:
    from brain.autonomous_case_manager import get_case

    case_id = UUID(str(args["case_id"]))
    payload = await get_case(session, organization_id=organization_id, case_id=case_id)
    return payload or {"status": "not_found"}


async def list_case_queue_tool(
    session: AsyncSession,
    *,
    organization_id: UUID,
    args: Dict[str, Any],
) -> Dict[str, Any]:
    from brain.autonomous_case_manager import list_case_queue

    limit = int(args.get("limit") or 10)
    return {"items": await list_case_queue(session, organization_id=organization_id, limit=limit)}


async def run_case_queue_tool(
    session: AsyncSession,
    *,
    organization_id: UUID,
    args: Dict[str, Any],
) -> Dict[str, Any]:
    from brain.autonomous_case_manager import run_case_queue

    limit = int(args.get("limit") or 5)
    return await run_case_queue(session, organization_id=organization_id, limit=limit)


async def get_business_state_tool(
    session: AsyncSession,
    *,
    organization_id: UUID,
    args: Dict[str, Any],
) -> Dict[str, Any]:
    refresh = bool(args.get("refresh"))
    window_minutes = int(args.get("window_minutes") or 60)
    return await get_latest_business_state(
        session,
        organization_id=organization_id,
        refresh=refresh,
        window_minutes=window_minutes,
    )


async def run_case_tool(
    session: AsyncSession,
    *,
    organization_id: UUID,
    args: Dict[str, Any],
) -> Dict[str, Any]:
    from brain.autonomous_case_manager import run_case

    case_id = UUID(str(args["case_id"]))
    return await run_case(session, organization_id=organization_id, case_id=case_id)
