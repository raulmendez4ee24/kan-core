import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from brain.business_monitor import build_metrics_snapshot
from brain.business_optimizer import analyze_goal_driven_plan
from brain.business_state_engine import get_latest_business_state
from brain.autonomous_case_manager import (
    create_or_update_case as create_autonomous_case,
    get_case as get_autonomous_case,
    list_case_queue as list_autonomous_case_queue,
    list_case_steps as list_autonomous_case_steps,
    list_rollbackable_guardrails,
    run_case_queue as run_autonomous_case_queue,
    run_case as run_autonomous_case,
)
from brain.case_progress_store import load_case_progress
from brain.execution_guard import summarize_guard_audit_entries
from brain.execution_rollback import run_execution_guard_rollback
from brain.mcp_runtime import get_tool_catalog
from brain.mission_planner import build_mission_steps, normalize_industry
from brain.skill_registry import list_skills
from database import get_session
from models import (
    AuditLog,
    BusinessMetricsSnapshot,
    Mission,
    MissionStep,
    Objective,
    ObjectiveRun,
    Recommendation,
    RecommendationAction,
)
from routes.integrations import run_integration_autopilot
from schemas.autonomy import (
    AutonomousCaseCreateRequest,
    AutonomousCaseQueueItem,
    AutonomousCaseQueueResponse,
    AutonomousCaseRead,
    AutonomousCaseRunResponse,
    AutonomousCaseStepRead,
    BusinessStateRead,
    CaseProgressRead,
    CaseResumeResponse,
    CycleRunItem,
    CycleRunRequest,
    CycleRunResponse,
    GoalCreateRequest,
    GoalCreateResponse,
    GuardrailRollbackResponse,
    MissionCreateRequest,
    MissionExecuteResponse,
    MissionRead,
    MissionStepRead,
    MonitorSnapshotResponse,
    ObjectiveCreateRequest,
    ObjectiveRead,
    ObjectiveUpdateRequest,
    RecommendationDryRunResponse,
    RecommendationImplementRequest,
    RecommendationImplementResponse,
    RecommendationRead,
    RollbackableGuardrailRead,
    SkillRead,
    ToolCatalogItem,
    ToolCatalogResponse,
)
from schemas.integration import IntegrationAutopilotRequest
from security import require_client_token

router = APIRouter(prefix="/autonomy", tags=["autonomy"])


def _is_enabled(flag: str, default: str = "true") -> bool:
    return os.getenv(flag, default).lower() in {"1", "true", "yes"}


def _ensure_enabled(flag: str) -> None:
    if not _is_enabled(flag):
        raise HTTPException(status_code=404, detail=f"Feature {flag} disabled")


def _parse_uuid(raw: str, *, field: str) -> UUID:
    try:
        return UUID(raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid {field}") from exc


def _to_objective_read(obj: Objective) -> ObjectiveRead:
    return ObjectiveRead(
        id=str(obj.id),
        goal=obj.goal,
        metric_key=obj.metric_key,
        target_value=obj.target_value,
        status=obj.status,
        autonomy_mode=obj.autonomy_mode,
        priority=int(obj.priority),
        metadata=obj.objective_metadata or {},
        created_at=obj.created_at,
        updated_at=obj.updated_at,
    )


def _to_recommendation_read(item: Recommendation) -> RecommendationRead:
    desired = item.desired_functions or {}
    if isinstance(desired, dict):
        desired_functions = [str(x) for x in desired.get("items", []) if str(x).strip()]
    elif isinstance(desired, list):
        desired_functions = [str(x) for x in desired if str(x).strip()]
    else:
        desired_functions = []

    return RecommendationRead(
        id=str(item.id),
        objective_id=str(item.objective_id) if item.objective_id else None,
        recommendation_key=item.recommendation_key,
        priority=item.priority,
        confidence=float(item.confidence or 0.0),
        detected_issue=item.detected_issue,
        proposal=item.proposal,
        expected_impact=item.expected_impact,
        desired_functions=desired_functions,
        request_text=item.request_text,
        requires_confirmation=bool(item.requires_confirmation),
        status=item.status,
        reasoning=item.reasoning,
        metadata=item.recommendation_metadata or {},
        created_at=item.created_at,
    )


def _to_step_read(step: MissionStep) -> MissionStepRead:
    desired = step.desired_functions or {}
    desired_functions = desired.get("items", []) if isinstance(desired, dict) else desired
    return MissionStepRead(
        id=str(step.id),
        step_order=step.step_order,
        step_type=step.step_type,
        title=step.title,
        desired_functions=[str(x) for x in (desired_functions or []) if str(x).strip()],
        request_text=step.request_text,
        status=step.status,
        result=step.result or {},
    )


def _to_case_read(payload: Dict[str, Any]) -> AutonomousCaseRead:
    return AutonomousCaseRead(
        id=str(payload.get("id") or ""),
        organization_id=str(payload.get("organization_id") or ""),
        case_type=str(payload.get("case_type") or ""),
        status=str(payload.get("status") or ""),
        priority_score=float(payload.get("priority_score") or 0.0),
        risk_score=float(payload.get("risk_score") or 0.0),
        current_goal=str(payload.get("current_goal") or ""),
        business_context=dict(payload.get("business_context") or {}),
        world_state=dict(payload.get("world_state") or {}),
        last_decision=dict(payload.get("last_decision") or {}),
        last_execution_result=dict(payload.get("last_execution_result") or {}),
        resolution_summary=dict(payload.get("resolution_summary") or {}),
        created_at=payload.get("created_at"),
        updated_at=payload.get("updated_at"),
    )


def _to_case_step_read(payload: Dict[str, Any]) -> AutonomousCaseStepRead:
    return AutonomousCaseStepRead(
        id=str(payload.get("id") or ""),
        case_id=str(payload.get("case_id") or ""),
        step_index=int(payload.get("step_index") or 0),
        status=str(payload.get("status") or ""),
        decision_payload=dict(payload.get("decision_payload") or {}),
        execution_payload=dict(payload.get("execution_payload") or {}),
        execution_result=dict(payload.get("execution_result") or {}),
        outcome_evaluation=dict(payload.get("outcome_evaluation") or {}),
        created_at=payload.get("created_at"),
    )


def _to_case_progress_read(payload: Dict[str, Any]) -> CaseProgressRead:
    return CaseProgressRead(
        case_id=str(payload.get("case_id") or ""),
        organization_id=str(payload.get("organization_id") or ""),
        current_goal=str(payload.get("current_goal") or ""),
        active_domain=str(payload.get("active_domain") or ""),
        last_completed_step=int(payload.get("last_completed_step") or 0),
        next_expected_step=str(payload.get("next_expected_step") or ""),
        last_successful_tool=str(payload.get("last_successful_tool") or ""),
        last_failure=dict(payload.get("last_failure") or {}),
        retry_count_by_signature=dict(payload.get("retry_count_by_signature") or {}),
        rollback_options=list(payload.get("rollback_options") or []),
        open_questions=[str(x) for x in (payload.get("open_questions") or []) if str(x).strip()],
        artifacts_created=[str(x) for x in (payload.get("artifacts_created") or []) if str(x).strip()],
        updated_at=(str(payload.get("updated_at")) if payload.get("updated_at") else None),
    )


async def _build_execution_guard_snapshot(
    session: AsyncSession,
    *,
    organization_id: UUID,
    window_start: datetime,
) -> Dict[str, Any]:
    try:
        stmt = (
            select(
                AuditLog.action,
                AuditLog.status,
                AuditLog.created_at,
                AuditLog.audit_metadata,
            )
            .where(
                AuditLog.organization_id == organization_id,
                AuditLog.created_at >= window_start,
                AuditLog.action.in_(["execution_guard.preflight", "execution_guard.paused", "execution_guard.tripwire"]),
            )
            .order_by(desc(AuditLog.created_at))
            .limit(200)
        )
        rows = list((await session.execute(stmt)).all())
    except Exception:
        return summarize_guard_audit_entries([])

    entries = [
        {
            "action": row.action,
            "status": row.status,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "metadata": dict(row.audit_metadata or {}),
        }
        for row in rows
    ]
    return summarize_guard_audit_entries(entries)


async def _get_recommendation(
    session: AsyncSession,
    *,
    org_id: UUID,
    recommendation_id: UUID,
) -> Recommendation:
    stmt = select(Recommendation).where(
        Recommendation.organization_id == org_id,
        Recommendation.id == recommendation_id,
    )
    row = (await session.execute(stmt)).scalars().first()
    if not row:
        raise HTTPException(status_code=404, detail="Recommendation not found")
    return row


async def _dry_run_recommendation(
    *,
    recommendation: Recommendation,
    client_id: str,
    session: AsyncSession,
) -> RecommendationDryRunResponse:
    desired = recommendation.desired_functions or {}
    desired_functions = desired.get("items", []) if isinstance(desired, dict) else desired
    autopilot_req = IntegrationAutopilotRequest(
        request_text=recommendation.request_text,
        desired_functions=[str(x) for x in (desired_functions or [])],
        dry_run=True,
        auto_generate_workflow=True,
        continue_on_error=True,
        enable_provider_fallback=True,
        auto_repair_max_attempts=2,
    )
    try:
        preview = await run_integration_autopilot(
            req=autopilot_req,
            client_id=client_id,
            session=session,
        )
    except Exception as exc:
        return RecommendationDryRunResponse(
            recommendation_id=str(recommendation.id),
            status="failed",
            reason=f"dry_run failed: {exc!s}",
            risk_score=1.0,
            autopilot_preview={},
        )

    statuses = {item.status for item in preview.results}
    blocked = not preview.results or statuses.issubset({"failed", "manual_action_required"})
    risk_score = round(max(0.0, 1.0 - float(recommendation.confidence or 0.0)), 4)
    return RecommendationDryRunResponse(
        recommendation_id=str(recommendation.id),
        status="blocked" if blocked else "ok",
        reason=(
            "Dry-run con riesgo alto o sin viabilidad" if blocked else "Dry-run viable para ejecucion"
        ),
        risk_score=risk_score,
        autopilot_preview=preview.model_dump(),
    )


@router.post("/objectives", response_model=ObjectiveRead)
async def create_objective(
    req: ObjectiveCreateRequest,
    client_id: str = Depends(require_client_token),
    session: AsyncSession = Depends(get_session),
):
    _ensure_enabled("ENABLE_AUTONOMY_CORE")
    org_id = _parse_uuid(client_id, field="client_id")

    row = Objective(
        organization_id=org_id,
        goal=req.goal,
        metric_key=req.metric_key,
        target_value=req.target_value,
        status="active",
        autonomy_mode=req.autonomy_mode,
        priority=req.priority,
        objective_metadata=req.metadata,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return _to_objective_read(row)


@router.post("/goals", response_model=GoalCreateResponse)
async def create_goal(
    req: GoalCreateRequest,
    client_id: str = Depends(require_client_token),
    session: AsyncSession = Depends(get_session),
):
    _ensure_enabled("ENABLE_AUTONOMY_CORE")
    org_id = _parse_uuid(client_id, field="client_id")

    # 1) Analyze goal to get recommended steps.
    analysis = await analyze_goal_driven_plan(
        session,
        client_id=client_id,
        client_uuid=org_id,
        goal=req.goal_text,
        window_days=req.window_days,
        max_recommendations=req.max_recommendations,
        include_llm_summary=req.include_llm_summary,
    )

    # Map execution_mode to autonomy_mode (Objective/Mission field).
    exec_mode = str(req.execution_mode or "").strip().lower()
    autonomy_mode = "autonomous" if exec_mode == "full_auto" else "confirm"

    # 2) Create objective.
    objective = Objective(
        organization_id=org_id,
        goal=req.goal_text,
        metric_key="sales",
        target_value=None,
        status="active",
        autonomy_mode=autonomy_mode,
        priority=7,
        objective_metadata={"industry_hint": req.industry_hint, "execution_mode": exec_mode},
    )
    session.add(objective)
    await session.commit()
    await session.refresh(objective)

    # 3) Create mission with hierarchical steps.
    recommendation_payload = [
        {
            "proposal": item.proposal,
            "desired_functions": list(item.desired_functions or []),
            "request_text": item.request_text,
        }
        for item in (analysis.recommendations or [])
    ]
    industry = normalize_industry(req.industry_hint)
    planned_steps = build_mission_steps(
        goal=req.goal_text,
        recommendations=recommendation_payload,
        industry=industry,
    )

    mission = Mission(
        organization_id=org_id,
        objective_id=objective.id,
        recommendation_id=None,
        title=f"Goal: {req.goal_text}"[:240],
        status="pending",
        autonomy_mode=autonomy_mode,
        mission_context={
            "industry": industry,
            "goal": req.goal_text,
            "analysis_summary": analysis.summary,
            "llm_summary": analysis.llm_summary,
        },
    )
    session.add(mission)
    await session.commit()
    await session.refresh(mission)

    for step in planned_steps:
        row = MissionStep(
            mission_id=mission.id,
            organization_id=org_id,
            step_order=int(step.get("step_order") or 1),
            step_type=str(step.get("step_type") or "task"),
            title=str(step.get("title") or "Mission step"),
            desired_functions={"items": list(step.get("desired_functions", []))},
            request_text=str(step.get("request_text") or req.goal_text),
            status="pending",
            result={},
        )
        session.add(row)

    await session.commit()

    steps_stmt = select(MissionStep).where(MissionStep.mission_id == mission.id).order_by(MissionStep.step_order.asc())
    steps = list((await session.execute(steps_stmt)).scalars().all())
    return GoalCreateResponse(
        objective_id=str(objective.id),
        mission_id=str(mission.id),
        summary=analysis.summary,
        created_recommendations=[],
        next_actions=[_to_step_read(step) for step in steps],
    )


@router.get("/objectives", response_model=List[ObjectiveRead])
async def list_objectives(
    status: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    client_id: str = Depends(require_client_token),
    session: AsyncSession = Depends(get_session),
):
    _ensure_enabled("ENABLE_AUTONOMY_CORE")
    org_id = _parse_uuid(client_id, field="client_id")

    stmt = select(Objective).where(Objective.organization_id == org_id)
    if status:
        stmt = stmt.where(Objective.status == status)
    stmt = stmt.order_by(desc(Objective.updated_at)).limit(limit)
    rows = list((await session.execute(stmt)).scalars().all())
    return [_to_objective_read(item) for item in rows]


@router.patch("/objectives/{objective_id}", response_model=ObjectiveRead)
async def patch_objective(
    objective_id: str,
    req: ObjectiveUpdateRequest,
    client_id: str = Depends(require_client_token),
    session: AsyncSession = Depends(get_session),
):
    _ensure_enabled("ENABLE_AUTONOMY_CORE")
    org_id = _parse_uuid(client_id, field="client_id")
    objective_uuid = _parse_uuid(objective_id, field="objective_id")

    stmt = select(Objective).where(
        Objective.organization_id == org_id,
        Objective.id == objective_uuid,
    )
    row = (await session.execute(stmt)).scalars().first()
    if not row:
        raise HTTPException(status_code=404, detail="Objective not found")

    for key in ["goal", "metric_key", "target_value", "status", "autonomy_mode", "priority"]:
        value = getattr(req, key)
        if value is not None:
            setattr(row, key, value)
    if req.metadata is not None:
        row.objective_metadata = req.metadata

    await session.commit()
    await session.refresh(row)
    return _to_objective_read(row)


@router.post("/cycles/run", response_model=CycleRunResponse)
async def run_autonomy_cycles(
    req: CycleRunRequest,
    client_id: str = Depends(require_client_token),
    session: AsyncSession = Depends(get_session),
):
    _ensure_enabled("ENABLE_AUTONOMY_CORE")
    org_id = _parse_uuid(client_id, field="client_id")

    objectives_stmt = select(Objective).where(
        Objective.organization_id == org_id,
        Objective.status == "active",
    )
    if req.objective_ids:
        objective_uuids = [_parse_uuid(item, field="objective_id") for item in req.objective_ids]
        objectives_stmt = objectives_stmt.where(Objective.id.in_(objective_uuids))

    objectives = list((await session.execute(objectives_stmt)).scalars().all())
    if not objectives:
        raise HTTPException(status_code=404, detail="No active objectives found")

    snapshot = await build_metrics_snapshot(
        session,
        organization_id=org_id,
        window_minutes=5,
    )
    risk_blocked = float((snapshot.metrics or {}).get("integration_error_rate", 0.0)) >= float(
        os.getenv("AUTONOMY_MAX_ERROR_RATE", "0.6")
    )

    output: List[CycleRunItem] = []
    for objective in objectives:
        analysis = await analyze_goal_driven_plan(
            session,
            client_id=client_id,
            client_uuid=org_id,
            goal=objective.goal,
            window_days=req.window_days,
            max_recommendations=req.max_recommendations,
            include_llm_summary=req.include_llm_summary,
        )

        objective_run = ObjectiveRun(
            objective_id=objective.id,
            organization_id=org_id,
            status="blocked" if risk_blocked else "completed",
            risk_score=float((snapshot.metrics or {}).get("integration_error_rate", 0.0)),
            confidence=max((r.confidence for r in analysis.recommendations), default=0.0),
            summary=analysis.summary,
            run_metadata={
                "metrics": analysis.metrics.model_dump(),
                "llm_summary": analysis.llm_summary,
            },
            started_at=datetime.now(timezone.utc),
            finished_at=datetime.now(timezone.utc),
        )
        session.add(objective_run)
        await session.commit()
        await session.refresh(objective_run)

        rec_rows: List[Recommendation] = []
        for rec in analysis.recommendations:
            row = Recommendation(
                organization_id=org_id,
                objective_id=objective.id,
                objective_run_id=objective_run.id,
                recommendation_key=rec.id,
                priority=rec.priority,
                confidence=float(rec.confidence),
                detected_issue=rec.detected_issue,
                proposal=rec.proposal,
                expected_impact=rec.expected_impact,
                desired_functions={"items": list(rec.desired_functions)},
                request_text=rec.request_text,
                requires_confirmation=bool(rec.requires_confirmation),
                status="blocked" if risk_blocked else "proposed",
                reasoning=(
                    "Ejecucion bloqueada por riesgo operativo alto"
                    if risk_blocked
                    else "Recomendacion generada en ciclo autonomo"
                ),
                recommendation_metadata={"goal": objective.goal},
            )
            session.add(row)
            rec_rows.append(row)

        await session.commit()
        for row in rec_rows:
            await session.refresh(row)

        output.append(
            CycleRunItem(
                objective_id=str(objective.id),
                objective_run_id=str(objective_run.id),
                summary=analysis.summary,
                llm_summary=analysis.llm_summary,
                recommendations=[_to_recommendation_read(item) for item in rec_rows],
            )
        )

    return CycleRunResponse(runs=output)


@router.get("/recommendations", response_model=List[RecommendationRead])
async def list_recommendations(
    status: Optional[str] = Query(default=None),
    objective_id: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    client_id: str = Depends(require_client_token),
    session: AsyncSession = Depends(get_session),
):
    _ensure_enabled("ENABLE_AUTONOMY_CORE")
    org_id = _parse_uuid(client_id, field="client_id")

    stmt = select(Recommendation).where(Recommendation.organization_id == org_id)
    if status:
        stmt = stmt.where(Recommendation.status == status)
    if objective_id:
        stmt = stmt.where(Recommendation.objective_id == _parse_uuid(objective_id, field="objective_id"))
    stmt = stmt.order_by(desc(Recommendation.created_at)).limit(limit)

    rows = list((await session.execute(stmt)).scalars().all())
    return [_to_recommendation_read(item) for item in rows]


@router.post("/recommendations/{recommendation_id}/dry-run", response_model=RecommendationDryRunResponse)
async def dry_run_recommendation(
    recommendation_id: str,
    client_id: str = Depends(require_client_token),
    session: AsyncSession = Depends(get_session),
):
    _ensure_enabled("ENABLE_AUTONOMY_CORE")
    org_id = _parse_uuid(client_id, field="client_id")
    rec = await _get_recommendation(
        session,
        org_id=org_id,
        recommendation_id=_parse_uuid(recommendation_id, field="recommendation_id"),
    )

    dry_run = await _dry_run_recommendation(
        recommendation=rec,
        client_id=client_id,
        session=session,
    )
    action = RecommendationAction(
        recommendation_id=rec.id,
        organization_id=org_id,
        action_type="dry_run",
        status=dry_run.status,
        result=dry_run.autopilot_preview,
        risk_score=dry_run.risk_score,
    )
    session.add(action)
    await session.commit()
    return dry_run


@router.post(
    "/recommendations/{recommendation_id}/implement",
    response_model=RecommendationImplementResponse,
)
async def implement_recommendation(
    recommendation_id: str,
    req: RecommendationImplementRequest,
    client_id: str = Depends(require_client_token),
    session: AsyncSession = Depends(get_session),
):
    _ensure_enabled("ENABLE_AUTONOMY_CORE")
    org_id = _parse_uuid(client_id, field="client_id")
    rec = await _get_recommendation(
        session,
        org_id=org_id,
        recommendation_id=_parse_uuid(recommendation_id, field="recommendation_id"),
    )

    if rec.requires_confirmation and not req.confirm_sensitive and not req.force:
        return RecommendationImplementResponse(
            recommendation_id=str(rec.id),
            status="blocked",
            reason="Accion sensible: confirma con confirm_sensitive=true",
            result={},
        )

    dry_run = await _dry_run_recommendation(recommendation=rec, client_id=client_id, session=session)
    if dry_run.status != "ok" and not req.force:
        return RecommendationImplementResponse(
            recommendation_id=str(rec.id),
            status="blocked",
            reason=dry_run.reason,
            result={"dry_run": dry_run.model_dump()},
        )

    desired = rec.desired_functions or {}
    desired_functions = desired.get("items", []) if isinstance(desired, dict) else desired
    autopilot_req = IntegrationAutopilotRequest(
        request_text=rec.request_text,
        desired_functions=[str(x) for x in (desired_functions or [])],
        dry_run=False,
        auto_generate_workflow=True,
        continue_on_error=True,
        enable_provider_fallback=True,
        auto_repair_max_attempts=2,
    )

    try:
        response = await run_integration_autopilot(req=autopilot_req, client_id=client_id, session=session)
    except Exception as exc:
        rec.status = "failed"
        await session.commit()
        return RecommendationImplementResponse(
            recommendation_id=str(rec.id),
            status="failed",
            reason=f"Implementation failed: {exc!s}",
            result={"dry_run": dry_run.model_dump()},
        )

    statuses = {item.status for item in response.results}
    is_ok = bool(response.results) and not statuses.issubset({"failed", "manual_action_required"})

    rec.status = "implemented" if is_ok else "failed"
    rec.reasoning = "Applied by autonomy implement endpoint"

    first_workflow_id = None
    first_workflow_url = None
    for item in response.results:
        if item.workflowId is not None and first_workflow_id is None:
            first_workflow_id = str(item.workflowId)
        if item.url and first_workflow_url is None:
            first_workflow_url = str(item.url)

    action = RecommendationAction(
        recommendation_id=rec.id,
        organization_id=org_id,
        action_type="implement",
        status="success" if is_ok else "failed",
        result={
            "dry_run": dry_run.model_dump(),
            "execution": response.model_dump(),
        },
        workflow_id=first_workflow_id,
        workflow_url=first_workflow_url,
        risk_score=dry_run.risk_score,
    )
    session.add(action)
    await session.commit()

    return RecommendationImplementResponse(
        recommendation_id=str(rec.id),
        status="implemented" if is_ok else "failed",
        reason="Recomendacion implementada" if is_ok else "No viable durante ejecucion",
        result=response.model_dump(),
    )


@router.post("/missions", response_model=MissionRead)
async def create_mission(
    req: MissionCreateRequest,
    client_id: str = Depends(require_client_token),
    session: AsyncSession = Depends(get_session),
):
    _ensure_enabled("ENABLE_AUTONOMY_CORE")
    org_id = _parse_uuid(client_id, field="client_id")

    recommendation_rows: List[Recommendation] = []
    recommendation_uuid = None
    if req.recommendation_id:
        recommendation_uuid = _parse_uuid(req.recommendation_id, field="recommendation_id")
        recommendation_rows = [
            await _get_recommendation(
                session,
                org_id=org_id,
                recommendation_id=recommendation_uuid,
            )
        ]

    recommendation_payload = [
        {
            "proposal": item.proposal,
            "desired_functions": (item.desired_functions or {}).get("items", []),
            "request_text": item.request_text,
        }
        for item in recommendation_rows
    ]

    industry = normalize_industry(req.context.get("industry"))
    planned_steps = build_mission_steps(
        goal=req.goal,
        recommendations=recommendation_payload,
        industry=industry,
    )

    mission = Mission(
        organization_id=org_id,
        objective_id=_parse_uuid(req.objective_id, field="objective_id") if req.objective_id else None,
        recommendation_id=recommendation_uuid,
        title=req.title,
        status="pending",
        autonomy_mode=req.autonomy_mode,
        mission_context={**req.context, "industry": industry, "goal": req.goal},
    )
    session.add(mission)
    await session.commit()
    await session.refresh(mission)

    for step in planned_steps:
        row = MissionStep(
            mission_id=mission.id,
            organization_id=org_id,
            step_order=int(step.get("step_order") or 1),
            step_type=str(step.get("step_type") or "task"),
            title=str(step.get("title") or "Mission step"),
            desired_functions={"items": list(step.get("desired_functions", []))},
            request_text=str(step.get("request_text") or req.goal),
            status="pending",
            result={},
        )
        session.add(row)

    await session.commit()

    steps_stmt = select(MissionStep).where(MissionStep.mission_id == mission.id).order_by(MissionStep.step_order.asc())
    steps = list((await session.execute(steps_stmt)).scalars().all())
    return MissionRead(
        id=str(mission.id),
        title=mission.title,
        status=mission.status,
        autonomy_mode=mission.autonomy_mode,
        objective_id=str(mission.objective_id) if mission.objective_id else None,
        recommendation_id=str(mission.recommendation_id) if mission.recommendation_id else None,
        context=mission.mission_context or {},
        steps=[_to_step_read(step) for step in steps],
        created_at=mission.created_at,
        updated_at=mission.updated_at,
    )


@router.get("/missions/{mission_id}", response_model=MissionRead)
async def get_mission(
    mission_id: str,
    client_id: str = Depends(require_client_token),
    session: AsyncSession = Depends(get_session),
):
    _ensure_enabled("ENABLE_AUTONOMY_CORE")
    org_id = _parse_uuid(client_id, field="client_id")
    mission_uuid = _parse_uuid(mission_id, field="mission_id")

    stmt = select(Mission).where(
        Mission.organization_id == org_id,
        Mission.id == mission_uuid,
    )
    mission = (await session.execute(stmt)).scalars().first()
    if not mission:
        raise HTTPException(status_code=404, detail="Mission not found")

    steps_stmt = select(MissionStep).where(MissionStep.mission_id == mission.id).order_by(MissionStep.step_order.asc())
    steps = list((await session.execute(steps_stmt)).scalars().all())
    return MissionRead(
        id=str(mission.id),
        title=mission.title,
        status=mission.status,
        autonomy_mode=mission.autonomy_mode,
        objective_id=str(mission.objective_id) if mission.objective_id else None,
        recommendation_id=str(mission.recommendation_id) if mission.recommendation_id else None,
        context=mission.mission_context or {},
        steps=[_to_step_read(step) for step in steps],
        created_at=mission.created_at,
        updated_at=mission.updated_at,
    )


@router.post("/missions/{mission_id}/execute", response_model=MissionExecuteResponse)
async def execute_mission(
    mission_id: str,
    client_id: str = Depends(require_client_token),
    session: AsyncSession = Depends(get_session),
):
    _ensure_enabled("ENABLE_AUTONOMY_CORE")
    org_id = _parse_uuid(client_id, field="client_id")
    mission_uuid = _parse_uuid(mission_id, field="mission_id")

    stmt = select(Mission).where(
        Mission.organization_id == org_id,
        Mission.id == mission_uuid,
    )
    mission = (await session.execute(stmt)).scalars().first()
    if not mission:
        raise HTTPException(status_code=404, detail="Mission not found")

    steps_stmt = select(MissionStep).where(MissionStep.mission_id == mission.id).order_by(MissionStep.step_order.asc())
    steps = list((await session.execute(steps_stmt)).scalars().all())
    if not steps:
        raise HTTPException(status_code=400, detail="Mission has no steps")

    mission.status = "running"
    await session.commit()

    executed = 0
    for step in steps:
        step.started_at = datetime.now(timezone.utc)
        step.status = "running"
        await session.commit()

        desired = step.desired_functions or {}
        desired_functions = desired.get("items", []) if isinstance(desired, dict) else desired
        request_text = step.request_text or mission.title
        autopilot_req = IntegrationAutopilotRequest(
            request_text=request_text,
            desired_functions=[str(x) for x in (desired_functions or [])],
            dry_run=False,
            auto_generate_workflow=True,
            continue_on_error=True,
            enable_provider_fallback=True,
            auto_repair_max_attempts=2,
        )
        try:
            result = await run_integration_autopilot(req=autopilot_req, client_id=client_id, session=session)
            step.result = result.model_dump()
            statuses = {item.status for item in result.results}
            step.status = "completed" if result.results and not statuses.issubset({"failed", "manual_action_required"}) else "failed"
        except Exception as exc:
            step.status = "failed"
            step.result = {"error": str(exc)}

        step.completed_at = datetime.now(timezone.utc)
        await session.commit()
        executed += 1

    await session.refresh(mission)
    final_steps = list((await session.execute(steps_stmt)).scalars().all())
    mission.status = "completed" if all(step.status == "completed" for step in final_steps) else "failed"
    await session.commit()

    return MissionExecuteResponse(
        mission_id=str(mission.id),
        status=mission.status,
        executed_steps=executed,
        step_results=[_to_step_read(step) for step in final_steps],
    )


@router.get("/monitor/snapshot", response_model=MonitorSnapshotResponse)
async def monitor_snapshot(
    refresh: bool = Query(default=True),
    window_minutes: int = Query(default=5, ge=1, le=120),
    client_id: str = Depends(require_client_token),
    session: AsyncSession = Depends(get_session),
):
    _ensure_enabled("ENABLE_AUTONOMY_CORE")
    org_id = _parse_uuid(client_id, field="client_id")

    snapshot: Optional[BusinessMetricsSnapshot]
    if refresh:
        snapshot = await build_metrics_snapshot(
            session,
            organization_id=org_id,
            window_minutes=window_minutes,
        )
    else:
        stmt = (
            select(BusinessMetricsSnapshot)
            .where(BusinessMetricsSnapshot.organization_id == org_id)
            .order_by(desc(BusinessMetricsSnapshot.window_end))
            .limit(1)
        )
        snapshot = (await session.execute(stmt)).scalars().first()
        if not snapshot:
            snapshot = await build_metrics_snapshot(
                session,
                organization_id=org_id,
                window_minutes=window_minutes,
            )

    metrics = dict(snapshot.metrics or {})
    metrics["execution_guard"] = await _build_execution_guard_snapshot(
        session,
        organization_id=org_id,
        window_start=snapshot.window_start,
    )

    return MonitorSnapshotResponse(
        window_minutes=snapshot.window_minutes,
        window_start=snapshot.window_start,
        window_end=snapshot.window_end,
        metrics=metrics,
    )


@router.post("/cases", response_model=AutonomousCaseRunResponse)
async def create_case_endpoint(
    req: AutonomousCaseCreateRequest,
    client_id: str = Depends(require_client_token),
    session: AsyncSession = Depends(get_session),
):
    _ensure_enabled("ENABLE_AUTONOMY_CORE")
    org_id = _parse_uuid(client_id, field="client_id")
    result = await create_autonomous_case(
        session,
        organization_id=org_id,
        case_type=req.case_type,
        current_goal=req.current_goal,
        business_context=req.business_context,
        run_now=req.run_now,
    )
    run_payload = dict(result.get("run") or {})
    step_payload = run_payload.get("step")
    return AutonomousCaseRunResponse(
        case=_to_case_read(result["case"]),
        decision=dict(run_payload.get("decision") or {}),
        execution_result=dict(run_payload.get("execution_result") or {}),
        outcome_evaluation=dict(run_payload.get("outcome_evaluation") or {}),
        supervision=dict(run_payload.get("supervision") or {}),
        step=_to_case_step_read(step_payload) if isinstance(step_payload, dict) else None,
    )


@router.get("/cases/queue", response_model=List[AutonomousCaseRead])
async def list_case_queue_endpoint(
    limit: int = Query(default=25, ge=1, le=100),
    client_id: str = Depends(require_client_token),
    session: AsyncSession = Depends(get_session),
):
    _ensure_enabled("ENABLE_AUTONOMY_CORE")
    org_id = _parse_uuid(client_id, field="client_id")
    items = await list_autonomous_case_queue(session, organization_id=org_id, limit=limit)
    return [_to_case_read(item) for item in items]


@router.post("/cases/queue/run", response_model=AutonomousCaseQueueResponse)
async def run_case_queue_endpoint(
    limit: int = Query(default=5, ge=1, le=20),
    client_id: str = Depends(require_client_token),
    session: AsyncSession = Depends(get_session),
):
    _ensure_enabled("ENABLE_AUTONOMY_CORE")
    org_id = _parse_uuid(client_id, field="client_id")
    payload = await run_autonomous_case_queue(session, organization_id=org_id, limit=limit)
    processed = []
    for item in payload.get("processed") or []:
        case_payload = item.get("case")
        if not isinstance(case_payload, dict):
            continue
        processed.append(
            AutonomousCaseQueueItem(
                case=_to_case_read(case_payload),
                decision=dict(item.get("decision") or {}),
                execution_result=dict(item.get("execution_result") or {}),
                supervision=dict(item.get("supervision") or {}),
            )
        )
    remaining = [
        _to_case_read(item)
        for item in (payload.get("remaining") or [])
        if isinstance(item, dict)
    ]
    return AutonomousCaseQueueResponse(
        processed=processed,
        processed_count=int(payload.get("processed_count") or 0),
        remaining_count=int(payload.get("remaining_count") or 0),
        remaining=remaining,
    )


@router.get("/cases/{case_id}", response_model=AutonomousCaseRead)
async def get_case_endpoint(
    case_id: str,
    client_id: str = Depends(require_client_token),
    session: AsyncSession = Depends(get_session),
):
    _ensure_enabled("ENABLE_AUTONOMY_CORE")
    org_id = _parse_uuid(client_id, field="client_id")
    case_uuid = _parse_uuid(case_id, field="case_id")
    payload = await get_autonomous_case(
        session,
        organization_id=org_id,
        case_id=case_uuid,
    )
    if not payload:
        raise HTTPException(status_code=404, detail="Case not found")
    return _to_case_read(payload)


@router.post("/cases/{case_id}/run", response_model=AutonomousCaseRunResponse)
async def run_case_endpoint(
    case_id: str,
    client_id: str = Depends(require_client_token),
    session: AsyncSession = Depends(get_session),
):
    _ensure_enabled("ENABLE_AUTONOMY_CORE")
    org_id = _parse_uuid(client_id, field="client_id")
    case_uuid = _parse_uuid(case_id, field="case_id")
    try:
        payload = await run_autonomous_case(
            session,
            organization_id=org_id,
            case_id=case_uuid,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return AutonomousCaseRunResponse(
        case=_to_case_read(payload["case"]),
        decision=dict(payload.get("decision") or {}),
        execution_result=dict(payload.get("execution_result") or {}),
        outcome_evaluation=dict(payload.get("outcome_evaluation") or {}),
        supervision=dict(payload.get("supervision") or {}),
        step=_to_case_step_read(payload["step"]) if isinstance(payload.get("step"), dict) else None,
    )


@router.get("/cases/{case_id}/steps", response_model=List[AutonomousCaseStepRead])
async def list_case_steps_endpoint(
    case_id: str,
    client_id: str = Depends(require_client_token),
    session: AsyncSession = Depends(get_session),
):
    _ensure_enabled("ENABLE_AUTONOMY_CORE")
    org_id = _parse_uuid(client_id, field="client_id")
    case_uuid = _parse_uuid(case_id, field="case_id")
    items = await list_autonomous_case_steps(
        session,
        organization_id=org_id,
        case_id=case_uuid,
    )
    return [_to_case_step_read(item) for item in items]


@router.get("/cases/{case_id}/progress", response_model=CaseProgressRead)
async def case_progress_endpoint(
    case_id: str,
    client_id: str = Depends(require_client_token),
    session: AsyncSession = Depends(get_session),
):
    _ensure_enabled("ENABLE_AUTONOMY_CORE")
    org_id = _parse_uuid(client_id, field="client_id")
    case_uuid = _parse_uuid(case_id, field="case_id")
    payload = await load_case_progress(session, organization_id=org_id, case_id=case_uuid)
    return _to_case_progress_read(payload)


@router.post("/cases/{case_id}/resume", response_model=CaseResumeResponse)
async def resume_case_endpoint(
    case_id: str,
    client_id: str = Depends(require_client_token),
    session: AsyncSession = Depends(get_session),
):
    _ensure_enabled("ENABLE_AUTONOMY_CORE")
    org_id = _parse_uuid(client_id, field="client_id")
    case_uuid = _parse_uuid(case_id, field="case_id")
    case_payload = await get_autonomous_case(
        session,
        organization_id=org_id,
        case_id=case_uuid,
    )
    if not case_payload:
        raise HTTPException(status_code=404, detail="Case not found")
    progress = await load_case_progress(session, organization_id=org_id, case_id=case_uuid)
    run_payload = await run_autonomous_case(
        session,
        organization_id=org_id,
        case_id=case_uuid,
    )
    return CaseResumeResponse(
        case=_to_case_read(run_payload["case"]),
        progress=_to_case_progress_read(progress),
        run=AutonomousCaseRunResponse(
            case=_to_case_read(run_payload["case"]),
            decision=dict(run_payload.get("decision") or {}),
            execution_result=dict(run_payload.get("execution_result") or {}),
            outcome_evaluation=dict(run_payload.get("outcome_evaluation") or {}),
            supervision=dict(run_payload.get("supervision") or {}),
            step=_to_case_step_read(run_payload["step"]) if isinstance(run_payload.get("step"), dict) else None,
        ),
    )


@router.get("/business-state", response_model=BusinessStateRead)
async def business_state_endpoint(
    refresh: bool = Query(default=False),
    window_minutes: int = Query(default=60, ge=5, le=1440),
    client_id: str = Depends(require_client_token),
    session: AsyncSession = Depends(get_session),
):
    _ensure_enabled("ENABLE_AUTONOMY_CORE")
    org_id = _parse_uuid(client_id, field="client_id")
    payload = await get_latest_business_state(
        session,
        organization_id=org_id,
        refresh=refresh,
        window_minutes=window_minutes,
    )
    return BusinessStateRead(**payload)


@router.get("/skills", response_model=List[SkillRead])
async def list_skills_endpoint(
    client_id: str = Depends(require_client_token),
) -> List[SkillRead]:
    _ensure_enabled("ENABLE_AUTONOMY_CORE")
    _parse_uuid(client_id, field="client_id")
    return [
        SkillRead(
            name=str(item.get("name") or ""),
            path=str(item.get("path") or ""),
            summary=str(item.get("summary") or ""),
        )
        for item in list_skills()
    ]


@router.get("/skills/{skill_name}", response_model=SkillRead)
async def get_skill_endpoint(
    skill_name: str,
    client_id: str = Depends(require_client_token),
) -> SkillRead:
    _ensure_enabled("ENABLE_AUTONOMY_CORE")
    _parse_uuid(client_id, field="client_id")
    for item in list_skills():
        if str(item.get("name") or "") == skill_name:
            return SkillRead(
                name=str(item.get("name") or ""),
                path=str(item.get("path") or ""),
                summary=str(item.get("summary") or ""),
            )
    raise HTTPException(status_code=404, detail="Skill not found")


@router.get("/tools/catalog", response_model=ToolCatalogResponse)
async def tool_catalog_endpoint(
    client_id: str = Depends(require_client_token),
) -> ToolCatalogResponse:
    _ensure_enabled("ENABLE_AUTONOMY_CORE")
    _parse_uuid(client_id, field="client_id")
    return ToolCatalogResponse(
        items=[
            ToolCatalogItem(
                name=str(item.get("name") or ""),
                description=str(item.get("description") or ""),
                input_schema=dict(item.get("input_schema") or {}),
                reversible=bool(item.get("reversible", True)),
            )
            for item in get_tool_catalog()
        ]
    )


@router.get("/guardrails/rollbackable", response_model=List[RollbackableGuardrailRead])
async def rollbackable_guardrails_endpoint(
    limit: int = Query(default=25, ge=1, le=100),
    client_id: str = Depends(require_client_token),
    session: AsyncSession = Depends(get_session),
):
    _ensure_enabled("ENABLE_AUTONOMY_CORE")
    org_id = _parse_uuid(client_id, field="client_id")
    items = await list_rollbackable_guardrails(
        session,
        organization_id=org_id,
        limit=limit,
    )
    return [
        RollbackableGuardrailRead(
            audit_log_id=item["audit_log_id"],
            action=item["action"],
            status=item["status"],
            created_at=item["created_at"],
            operation_type=item.get("operation_type"),
            action_name=item.get("action_name"),
            rollback_snapshot=dict(item.get("rollback_snapshot") or {}),
            context_signature=item.get("context_signature"),
        )
        for item in items
    ]


@router.post("/guardrails/rollback/{audit_log_id}", response_model=GuardrailRollbackResponse)
async def rollback_guardrail_action(
    audit_log_id: str,
    client_id: str = Depends(require_client_token),
    session: AsyncSession = Depends(get_session),
):
    _ensure_enabled("ENABLE_AUTONOMY_CORE")
    org_id = _parse_uuid(client_id, field="client_id")
    audit_uuid = _parse_uuid(audit_log_id, field="audit_log_id")

    try:
        result = await run_execution_guard_rollback(
            session,
            organization_id=org_id,
            audit_log_id=audit_uuid,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return GuardrailRollbackResponse(
        audit_log_id=result["audit_log_id"],
        source_action=result["source_action"],
        rollback=result["rollback"],
    )
