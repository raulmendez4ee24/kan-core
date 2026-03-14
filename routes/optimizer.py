from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from brain.business_optimizer import GoalAnalysis, analyze_goal_driven_plan
from brain.continuous_improvement import run_continuous_optimization
from database import get_session
from routes.integrations import run_integration_autopilot
from schemas.integration import IntegrationAutopilotRequest, IntegrationAutopilotResponse
from schemas.optimizer import (
    GoalDrivenAnalyzeRequest,
    GoalDrivenAnalyzeResponse,
    GoalDrivenImplementRequest,
    GoalDrivenImplementResponse,
    GoalExecutionResult,
    GoalRecommendation,
)
from security import require_client_token

router = APIRouter(prefix="/optimizer", tags=["optimizer"])


def _parse_client_uuid(client_id: str) -> UUID:
    try:
        return UUID(client_id)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="Invalid client id") from exc


def _build_autopilot_request(recommendation: GoalRecommendation) -> IntegrationAutopilotRequest:
    return _build_autopilot_request_with_mode(recommendation, dry_run=False)


def _build_autopilot_request_with_mode(
    recommendation: GoalRecommendation,
    *,
    dry_run: bool,
) -> IntegrationAutopilotRequest:
    return IntegrationAutopilotRequest(
        request_text=recommendation.request_text,
        desired_functions=recommendation.desired_functions,
        dry_run=dry_run,
        auto_generate_workflow=True,
        continue_on_error=True,
        enable_provider_fallback=True,
        auto_repair_max_attempts=2,
    )


def _summarize_autopilot_result(
    *,
    recommendation: GoalRecommendation,
    response: IntegrationAutopilotResponse,
) -> GoalExecutionResult:
    integrated = [
        item.function_name
        for item in response.results
        if item.status in {"integrated", "saved_without_workflow", "already_covered"}
    ]
    urls = [str(item.url) for item in response.results if item.url]
    status = "implemented" if integrated else "failed"
    reason = None
    if status == "failed":
        reasons = [item.reason for item in response.results if item.reason]
        reason = reasons[0] if reasons else "No se pudo implementar automaticamente."

    return GoalExecutionResult(
        recommendation_id=recommendation.id,
        status=status,  # type: ignore[arg-type]
        requested_functions=list(recommendation.desired_functions),
        integrated_functions=integrated,
        workflow_urls=urls,
        reason=reason,
        raw_result=response.model_dump(),
    )


async def _execute_recommendations(
    *,
    analysis: GoalAnalysis,
    recommendations: List[GoalRecommendation],
    client_id: str,
    session: AsyncSession,
    min_confidence_to_execute: float,
    require_dry_run_before_execute: bool,
    only_high_priority_auto_execute: bool,
    block_when_error_rate_above: float,
) -> List[GoalExecutionResult]:
    executions: List[GoalExecutionResult] = []
    if analysis.metrics.total_messages < 20 and analysis.metrics.total_runs < 8:
        reason = (
            "Auto-ejecucion bloqueada por evidencia insuficiente: "
            f"total_messages={analysis.metrics.total_messages}, total_runs={analysis.metrics.total_runs}."
        )
        for recommendation in recommendations:
            executions.append(
                GoalExecutionResult(
                    recommendation_id=recommendation.id,
                    status="skipped",
                    requested_functions=list(recommendation.desired_functions),
                    reason=reason,
                    raw_result={},
                )
            )
        return executions

    if analysis.metrics.error_rate >= block_when_error_rate_above:
        reason = (
            "Auto-ejecucion bloqueada por riesgo operativo alto: "
            f"error_rate={analysis.metrics.error_rate:.2f}% >= {block_when_error_rate_above:.2f}%."
        )
        for recommendation in recommendations:
            executions.append(
                GoalExecutionResult(
                    recommendation_id=recommendation.id,
                    status="skipped",
                    requested_functions=list(recommendation.desired_functions),
                    reason=reason,
                    raw_result={},
                )
            )
        return executions

    for recommendation in recommendations:
        if recommendation.confidence < min_confidence_to_execute:
            executions.append(
                GoalExecutionResult(
                    recommendation_id=recommendation.id,
                    status="skipped",
                    requested_functions=list(recommendation.desired_functions),
                    reason=(
                        f"confidence {recommendation.confidence:.2f} < "
                        f"threshold {min_confidence_to_execute:.2f}"
                    ),
                    raw_result={},
                )
            )
            continue
        if only_high_priority_auto_execute and recommendation.priority != "high":
            executions.append(
                GoalExecutionResult(
                    recommendation_id=recommendation.id,
                    status="skipped",
                    requested_functions=list(recommendation.desired_functions),
                    reason="priority no permitida para auto-ejecucion (solo high).",
                    raw_result={},
                )
            )
            continue

        try:
            dry_run_payload = {}
            if require_dry_run_before_execute:
                dry_run_req = _build_autopilot_request_with_mode(
                    recommendation,
                    dry_run=True,
                )
                dry_run_response = await run_integration_autopilot(
                    req=dry_run_req,
                    client_id=client_id,
                    session=session,
                )
                dry_run_payload = dry_run_response.model_dump()
                dry_run_statuses = {item.status for item in dry_run_response.results}
                if not dry_run_response.results or dry_run_statuses.issubset(
                    {"failed", "manual_action_required"}
                ):
                    executions.append(
                        GoalExecutionResult(
                            recommendation_id=recommendation.id,
                            status="skipped",
                            requested_functions=list(recommendation.desired_functions),
                            reason="dry_run detecto riesgo alto o falta de viabilidad.",
                            raw_result={"dry_run": dry_run_payload},
                        )
                    )
                    continue

            req = _build_autopilot_request(recommendation)
            autopilot_response = await run_integration_autopilot(
                req=req,
                client_id=client_id,
                session=session,
            )
            execution = _summarize_autopilot_result(
                recommendation=recommendation,
                response=autopilot_response,
            )
            if dry_run_payload:
                execution.raw_result = {
                    "dry_run": dry_run_payload,
                    "execution": execution.raw_result,
                }
            executions.append(execution)
        except Exception as exc:
            executions.append(
                GoalExecutionResult(
                    recommendation_id=recommendation.id,
                    status="failed",
                    requested_functions=list(recommendation.desired_functions),
                    reason=str(exc),
                    raw_result={},
                )
            )
    return executions


def _pick_recommendations(
    analysis: GoalAnalysis,
    *,
    recommendation_ids: Optional[List[str]],
    limit: int,
) -> List[GoalRecommendation]:
    if not recommendation_ids:
        return analysis.recommendations[: max(1, limit)]
    wanted = {item.strip() for item in recommendation_ids if item.strip()}
    selected = [item for item in analysis.recommendations if item.id in wanted]
    return selected[: max(1, limit)]


@router.post("/analyze", response_model=GoalDrivenAnalyzeResponse)
async def analyze_business_goal(
    req: GoalDrivenAnalyzeRequest,
    client_id: str = Depends(require_client_token),
    session: AsyncSession = Depends(get_session),
):
    client_uuid = _parse_client_uuid(client_id)
    analysis = await analyze_goal_driven_plan(
        session,
        client_id=client_id,
        client_uuid=client_uuid,
        goal=req.goal,
        window_days=req.window_days,
        max_recommendations=req.max_recommendations,
        include_llm_summary=req.include_llm_summary,
    )

    executions: List[GoalExecutionResult] = []
    if req.auto_execute and analysis.recommendations:
        selected = analysis.recommendations[: max(1, req.max_auto_execute)]
        executions = await _execute_recommendations(
            analysis=analysis,
            recommendations=selected,
            client_id=client_id,
            session=session,
            min_confidence_to_execute=req.min_confidence_to_execute,
            require_dry_run_before_execute=req.require_dry_run_before_execute,
            only_high_priority_auto_execute=req.only_high_priority_auto_execute,
            block_when_error_rate_above=req.block_when_error_rate_above,
        )

    return GoalDrivenAnalyzeResponse(
        goal=analysis.goal,
        summary=analysis.summary,
        metrics=analysis.metrics,
        recommendations=analysis.recommendations,
        llm_summary=analysis.llm_summary,
        executions=executions,
    )


@router.post("/implement", response_model=GoalDrivenImplementResponse)
async def implement_business_goal_recommendations(
    req: GoalDrivenImplementRequest,
    client_id: str = Depends(require_client_token),
    session: AsyncSession = Depends(get_session),
):
    client_uuid = _parse_client_uuid(client_id)
    analysis = await analyze_goal_driven_plan(
        session,
        client_id=client_id,
        client_uuid=client_uuid,
        goal=req.goal,
        window_days=req.window_days,
        max_recommendations=req.max_recommendations,
        include_llm_summary=False,
    )
    selected = _pick_recommendations(
        analysis,
        recommendation_ids=req.recommendation_ids,
        limit=req.max_execute,
    )
    if not selected:
        raise HTTPException(status_code=404, detail="No recommendations selected/found")

    executions = await _execute_recommendations(
        analysis=analysis,
        recommendations=selected,
        client_id=client_id,
        session=session,
        min_confidence_to_execute=req.min_confidence_to_execute,
        require_dry_run_before_execute=req.require_dry_run_before_execute,
        only_high_priority_auto_execute=req.only_high_priority_auto_execute,
        block_when_error_rate_above=req.block_when_error_rate_above,
    )
    return GoalDrivenImplementResponse(
        goal=req.goal,
        executed=len(executions),
        selected_recommendations=[item.id for item in selected],
        executions=executions,
    )


@router.post("/continuous-improvement")
async def continuous_improvement_cycle(
    lookback_days: int = 14,
    client_id: str = Depends(require_client_token),
    session: AsyncSession = Depends(get_session),
):
    client_uuid = _parse_client_uuid(client_id)
    result = await run_continuous_optimization(
        session,
        organization_id=client_uuid,
        lookback_days=max(1, min(lookback_days, 120)),
    )
    return {
        "objective_runs_analyzed": result.objective_runs_analyzed,
        "actions_analyzed": result.actions_analyzed,
        "suggested_confidence_threshold": result.suggested_confidence_threshold,
        "strategy": result.strategy,
        "metadata": result.metadata,
    }
