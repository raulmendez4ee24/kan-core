import os
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from brain.autonomous_case_manager import create_or_update_case
from brain.sales_autopilot import advance_lead_stage, get_sales_pipeline, run_sales_autopilot_cycle
from database import get_session
from schemas.sales_autopilot import (
    SalesAdvanceRequest,
    SalesAdvanceResponse,
    SalesAutopilotAction,
    SalesAutopilotRunRequest,
    SalesAutopilotRunResponse,
    SalesPipelineResponse,
    SalesPipelineSummaryItem,
)
from security import require_client_token

router = APIRouter(prefix="/sales-autopilot", tags=["sales-autopilot"])


def _enabled() -> bool:
    return os.getenv("ENABLE_SALES_AUTOPILOT", "true").lower() in {"1", "true", "yes"}


def _parse_uuid(raw: str, *, field: str) -> UUID:
    try:
        return UUID(raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid {field}") from exc


@router.post("/run", response_model=SalesAutopilotRunResponse)
async def sales_run(
    req: SalesAutopilotRunRequest,
    client_id: str = Depends(require_client_token),
    session: AsyncSession = Depends(get_session),
):
    if not _enabled():
        raise HTTPException(status_code=404, detail="Feature ENABLE_SALES_AUTOPILOT disabled")
    org_id = _parse_uuid(client_id, field="client_id")
    if (
        not req.dry_run
        and os.getenv("ENABLE_AUTONOMOUS_CASE_MANAGER", "false").lower() in {"1", "true", "yes"}
    ):
        case_result = await create_or_update_case(
            session,
            organization_id=org_id,
            case_type="sales_execution",
            current_goal="Ejecutar ciclo de sales autopilot",
            business_context={
                "source": "sales_autopilot.run",
                "case_key": "sales_autopilot_run",
                "max_actions": req.max_actions,
                "create_followups": req.create_followups,
                "metadata": req.metadata,
            },
            run_now=True,
        )
        run_payload = dict(case_result.get("run") or {})
        supervision = dict(run_payload.get("supervision") or {})
        return SalesAutopilotRunResponse(
            dry_run=False,
            scanned_leads=0,
            action_count=1 if run_payload else 0,
            actions=[
                SalesAutopilotAction(
                    action=str((run_payload.get("decision") or {}).get("recommended_action") or "advance_sales_pipeline"),
                    status=str(supervision.get("action") or "processed"),
                    reason=supervision.get("reason"),
                    payload={"case": case_result.get("case"), "execution_result": run_payload.get("execution_result", {})},
                )
            ],
            generated_at=(run_payload.get("step") or {}).get("created_at") or case_result["case"]["updated_at"],
        )
    result = await run_sales_autopilot_cycle(
        session,
        organization_id=org_id,
        dry_run=req.dry_run,
        max_actions=req.max_actions,
        create_followups=req.create_followups,
    )
    return SalesAutopilotRunResponse(
        dry_run=bool(result.get("dry_run", True)),
        scanned_leads=int(result.get("scanned_leads", 0)),
        action_count=int(result.get("action_count", 0)),
        actions=[SalesAutopilotAction(**item) for item in (result.get("actions") or [])],
        generated_at=result.get("generated_at"),
    )


@router.get("/pipeline", response_model=SalesPipelineResponse)
async def sales_pipeline(
    client_id: str = Depends(require_client_token),
    session: AsyncSession = Depends(get_session),
):
    if not _enabled():
        raise HTTPException(status_code=404, detail="Feature ENABLE_SALES_AUTOPILOT disabled")
    org_id = _parse_uuid(client_id, field="client_id")
    result = await get_sales_pipeline(session, organization_id=org_id)
    return SalesPipelineResponse(
        items=[SalesPipelineSummaryItem(**item) for item in (result.get("items") or [])],
        total_leads=int(result.get("total_leads", 0)),
        updated_at=result.get("updated_at"),
    )


@router.post("/leads/{lead_id}/advance", response_model=SalesAdvanceResponse)
async def sales_advance(
    lead_id: str,
    req: SalesAdvanceRequest,
    client_id: str = Depends(require_client_token),
    session: AsyncSession = Depends(get_session),
):
    if not _enabled():
        raise HTTPException(status_code=404, detail="Feature ENABLE_SALES_AUTOPILOT disabled")
    org_id = _parse_uuid(client_id, field="client_id")
    lead_uuid = _parse_uuid(lead_id, field="lead_id")
    try:
        result = await advance_lead_stage(
            session,
            organization_id=org_id,
            lead_id=lead_uuid,
            stage=req.stage,
            score=req.score,
            reason=req.reason,
            metadata=req.metadata,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return SalesAdvanceResponse(**result)
