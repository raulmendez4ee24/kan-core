import os
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from brain.ai_coo_engine import run_ai_coo_cycle
from database import get_session
from schemas.ai_coo import AiCooActionPlanItem, AiCooRunRequest, AiCooRunResponse
from security import require_client_token

router = APIRouter(prefix="/optimizer", tags=["optimizer"])


def _is_enabled() -> bool:
    return os.getenv("ENABLE_AI_COO", "false").lower() in {"1", "true", "yes"}


def _parse_org_id(client_id: str) -> UUID:
    try:
        return UUID(client_id)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="Invalid client id") from exc


@router.post("/ai-coo/run", response_model=AiCooRunResponse)
async def run_ai_coo(
    req: AiCooRunRequest,
    client_id: str = Depends(require_client_token),
    session: AsyncSession = Depends(get_session),
):
    if not _is_enabled():
        raise HTTPException(status_code=404, detail="Feature ENABLE_AI_COO disabled")

    org_id = _parse_org_id(client_id)
    result = await run_ai_coo_cycle(
        session,
        organization_id=org_id,
        window_hours=req.window_hours,
        dry_run=bool(req.dry_run),
        max_actions=req.max_actions,
        execution_mode_override=req.force_execution_mode,
    )

    action_plan = []
    for item in result.get("action_plan") or []:
        if isinstance(item, dict):
            action_plan.append(AiCooActionPlanItem(**item))

    return AiCooRunResponse(
        organization_id=str(result.get("organization_id") or org_id),
        computed_at=result.get("computed_at"),
        execution_mode=result.get("execution_mode"),
        circuit_breaker_open=bool(result.get("circuit_breaker_open", False)),
        signals=result.get("signals") if isinstance(result.get("signals"), dict) else {},
        insights=result.get("insights") if isinstance(result.get("insights"), list) else [],
        tool_snapshot=result.get("tool_snapshot") if isinstance(result.get("tool_snapshot"), dict) else {},
        action_plan=action_plan,
        executed=bool(result.get("executed", False)),
        results=result.get("results") if isinstance(result.get("results"), list) else [],
    )

