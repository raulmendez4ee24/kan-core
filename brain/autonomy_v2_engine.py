from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Dict, List
from uuid import UUID, uuid4

from sqlalchemy import asc, desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from brain.autonomy_web_research import run_desktop_autonomy_with_research
from learning.reinforcement_optimizer import (
    calibrate_org_learning_policy,
    refresh_learning_from_sources,
)
from models import AutonomyV2Benchmark
from schemas.autonomy_v2 import (
    AutonomyV2AgentTrace,
    AutonomyV2BenchmarkItem,
    AutonomyV2RunRequest,
    AutonomyV2RunResponse,
)
from schemas.desktop_autonomy import DesktopAutonomyResearchRunRequest


def _parse_client_uuid(client_id: str) -> UUID:
    try:
        return UUID(client_id)
    except ValueError as exc:
        raise ValueError("Invalid client_id") from exc


def _plan_steps(goal: str, *, max_items: int = 8) -> List[str]:
    normalized = " ".join(goal.strip().split())
    raw_chunks = [x.strip(" -•\n\t") for x in normalized.split(".") if x.strip()]
    if not raw_chunks:
        raw_chunks = [normalized]
    steps = [f"Entender objetivo: {raw_chunks[0]}"]
    for chunk in raw_chunks[1:max_items]:
        steps.append(f"Subobjetivo: {chunk}")
    steps.extend(
        [
            "Reunir evidencia web verificable",
            "Ejecutar acciones en desktop/browser",
            "Validar calidad y resultados",
        ]
    )
    deduped: List[str] = []
    seen = set()
    for item in steps:
        token = item.lower()
        if token in seen:
            continue
        seen.add(token)
        deduped.append(item)
    return deduped[:max_items]


def _extract_quality(execution: Dict[str, Any]) -> Dict[str, Any]:
    steps = execution.get("steps") if isinstance(execution, dict) else []
    if not isinstance(steps, list):
        return {}
    evaluate_steps = [s for s in steps if isinstance(s, dict) and s.get("phase") == "evaluate"]
    if not evaluate_steps:
        return {}
    last = evaluate_steps[-1]
    meta = last.get("metadata") or {}
    quality = meta.get("quality") if isinstance(meta, dict) else {}
    if isinstance(quality, dict):
        return quality
    return {}


def _classify_failure(execution: Dict[str, Any]) -> Dict[str, Any]:
    steps = execution.get("steps") if isinstance(execution, dict) else []
    if not isinstance(steps, list):
        return {"kind": "unknown", "counts": {}}
    failed_details = [
        str(s.get("detail") or "").lower()
        for s in steps
        if isinstance(s, dict) and str(s.get("status") or "") in {"failed", "blocked"}
    ]
    counts = {
        "timeout": sum("timeout" in x for x in failed_details),
        "approval_pending": sum("approval" in x for x in failed_details),
        "quality_gate": sum("quality" in x for x in failed_details),
    }
    kind = "generic"
    if counts["timeout"] > 0:
        kind = "timeout"
    elif counts["approval_pending"] > 0:
        kind = "approval_pending"
    elif counts["quality_gate"] > 0:
        kind = "quality_gate"
    return {"kind": kind, "counts": counts}


def _build_fallback_graph(failure: Dict[str, Any]) -> List[Dict[str, str]]:
    kind = str(failure.get("kind") or "generic")
    graph = [
        {"trigger": "timeout", "strategy": "retry_same_action_with_shorter_timeout"},
        {"trigger": "timeout", "strategy": "switch_to_alt_action_path"},
        {"trigger": "approval_pending", "strategy": "collect_pending_approvals_then_resume"},
        {"trigger": "quality_gate", "strategy": "tighten_scope_and_research_more_sources"},
        {"trigger": "generic", "strategy": "replan_goal_into_smaller_subtasks"},
    ]
    return [x for x in graph if x["trigger"] == kind] + [x for x in graph if x["trigger"] != kind]


def _next_actions_from_quality(quality: Dict[str, Any]) -> List[str]:
    actions: List[str] = []
    score = float(quality.get("score") or 0.0)
    threshold = float(quality.get("threshold") or 0.72)
    passed = bool(quality.get("quality_gate_passed"))
    if passed:
        actions.append("Escalar el mismo plan a mas canales/segmentos.")
        actions.append("Convertir este flujo en task recurrente con scheduler.")
        return actions
    if score < threshold:
        actions.append("Subir evidencia requerida (mas fuentes verificadas).")
        actions.append("Reducir alcance y reintentar con max_steps mas bajo.")
        actions.append("Refinar contexto con selectores/URLs concretos.")
    notes = quality.get("notes")
    if isinstance(notes, list):
        if any("manual_approvals_pending" in str(n) for n in notes):
            actions.append("Resolver approvals pendientes y re-ejecutar.")
        if any("has_failed_actions" in str(n) for n in notes):
            actions.append("Activar estrategia alternativa de herramienta para pasos fallidos.")
    return actions


async def _verifier_quality_module(execution: Dict[str, Any]) -> Dict[str, Any]:
    await asyncio.sleep(0)
    return _extract_quality(execution)


async def _verifier_failure_module(execution: Dict[str, Any]) -> Dict[str, Any]:
    await asyncio.sleep(0)
    return _classify_failure(execution)


async def _run_verifier_modules(execution: Dict[str, Any]) -> Dict[str, Any]:
    quality, failure = await asyncio.gather(
        _verifier_quality_module(execution),
        _verifier_failure_module(execution),
    )
    return {"quality": quality, "failure": failure}


def _compute_benchmark(
    *,
    run_id: str,
    goal: str,
    status: str,
    quality: Dict[str, Any],
    unique_sources: int,
    planned_steps: int,
    started_at: datetime,
    finished_at: datetime,
) -> AutonomyV2BenchmarkItem:
    score = float(quality.get("score") or 0.0)
    passed = bool(quality.get("quality_gate_passed"))
    latency = max(0.0, (finished_at - started_at).total_seconds())
    reliability = max(0.0, min(1.0, round((score * 0.7) + (0.3 if status == "completed" else 0.0), 4)))
    return AutonomyV2BenchmarkItem(
        run_id=run_id,
        goal=goal[:240],
        status=status,
        quality_score=round(score, 4),
        quality_passed=passed,
        latency_seconds=round(latency, 3),
        sources=int(unique_sources),
        planned_steps=int(planned_steps),
        reliability_index=reliability,
        timestamp=finished_at,
    )


def _row_to_benchmark_item(row: AutonomyV2Benchmark) -> AutonomyV2BenchmarkItem:
    return AutonomyV2BenchmarkItem(
        run_id=row.run_id,
        goal=row.goal,
        status=row.status,
        quality_score=float(row.quality_score or 0.0),
        quality_passed=bool(row.quality_passed),
        latency_seconds=float(row.latency_seconds or 0.0),
        sources=int(row.sources or 0),
        planned_steps=int(row.planned_steps or 0),
        reliability_index=float(row.reliability_index or 0.0),
        timestamp=row.created_at,
    )


async def _persist_benchmark(
    *,
    session: AsyncSession,
    organization_id: UUID,
    benchmark: AutonomyV2BenchmarkItem,
    metadata: Dict[str, Any],
) -> None:
    row = AutonomyV2Benchmark(
        organization_id=organization_id,
        run_id=benchmark.run_id,
        goal=benchmark.goal,
        status=benchmark.status,
        quality_score=benchmark.quality_score,
        quality_passed=benchmark.quality_passed,
        latency_seconds=benchmark.latency_seconds,
        sources=benchmark.sources,
        planned_steps=benchmark.planned_steps,
        reliability_index=benchmark.reliability_index,
        benchmark_metadata=metadata,
    )
    session.add(row)
    await session.commit()


async def get_autonomy_v2_benchmarks(
    *,
    session: AsyncSession,
    organization_id: UUID,
    limit: int = 50,
    order_by: str = "reliability",
    descending: bool = True,
) -> List[AutonomyV2BenchmarkItem]:
    safe_limit = max(1, min(int(limit), 200))
    order_token = str(order_by or "reliability").strip().lower()
    order_col = {
        "reliability": AutonomyV2Benchmark.reliability_index,
        "quality": AutonomyV2Benchmark.quality_score,
        "latency": AutonomyV2Benchmark.latency_seconds,
        "created_at": AutonomyV2Benchmark.created_at,
    }.get(order_token, AutonomyV2Benchmark.reliability_index)
    order_expr = desc(order_col) if descending else asc(order_col)
    stmt = (
        select(AutonomyV2Benchmark)
        .where(AutonomyV2Benchmark.organization_id == organization_id)
        .order_by(order_expr, desc(AutonomyV2Benchmark.created_at))
        .limit(safe_limit)
    )
    rows = list((await session.execute(stmt)).scalars().all())
    return [_row_to_benchmark_item(x) for x in rows]


async def run_autonomy_v2(
    *,
    client_id: str,
    req: AutonomyV2RunRequest,
    session: AsyncSession,
) -> AutonomyV2RunResponse:
    run_id = str(uuid4())
    now = datetime.now(timezone.utc)
    started_at = now
    traces: List[AutonomyV2AgentTrace] = []

    plan_steps = _plan_steps(req.goal)
    traces.append(
        AutonomyV2AgentTrace(
            stage="planner",
            status="ok",
            summary=f"Plan generado con {len(plan_steps)} pasos.",
            metadata={"plan_steps": plan_steps},
        )
    )

    research_goal = req.goal + "\n\nPlan:\n- " + "\n- ".join(plan_steps)
    traces.append(
        AutonomyV2AgentTrace(
            stage="researcher",
            status="ok",
            summary="Objetivo enriquecido con plan para web research y ejecucion.",
            metadata={"enriched_goal_preview": research_goal[:500]},
        )
    )

    internal_req = DesktopAutonomyResearchRunRequest(
        goal=research_goal,
        max_steps=req.max_steps,
        execution_mode=req.execution_mode,
        start_url=req.start_url,
        context={**dict(req.context or {}), "autonomy_v2": {"plan_steps": plan_steps}},
        dry_run=False,
        wait_for_results=True,
        step_timeout_seconds=req.step_timeout_seconds,
        enable_recovery=req.enable_recovery,
        stop_on_failure=req.stop_on_failure,
        enable_self_healing=req.enable_self_healing,
        self_heal_max_attempts=req.self_heal_max_attempts,
        quality_min_score=req.quality_min_score,
        enforce_quality_gate=req.enforce_quality_gate,
        dynamic_risk_controls=req.dynamic_risk_controls,
        min_sources=req.min_sources,
        max_results_per_query=req.max_results_per_query,
        include_brave_fallback=req.include_brave_fallback,
        require_verified_sources=req.require_verified_sources,
    )

    run = await run_desktop_autonomy_with_research(
        client_id=client_id,
        req=internal_req,
        session=session,
    )
    traces.append(
        AutonomyV2AgentTrace(
            stage="executor",
            status="ok" if run.status in {"completed", "dry_run"} else "warn",
            summary=f"Ejecucion finalizo con status={run.status}",
            metadata={
                "research_verified": bool(getattr(run.research, "verified", False)),
                "providers": list(getattr(run.research, "providers_used", []) or []),
            },
        )
    )

    execution_dict = run.execution.model_dump() if run.execution else {}
    verifier_result = await _run_verifier_modules(execution_dict)
    quality = verifier_result["quality"]
    failure = verifier_result["failure"]
    fallback_graph = _build_fallback_graph(failure)
    quality_passed = bool(quality.get("quality_gate_passed")) if quality else False
    traces.append(
        AutonomyV2AgentTrace(
            stage="verifier",
            status="ok" if quality_passed else "warn",
            summary=(
                f"Quality score={quality.get('score')} threshold={quality.get('threshold')}"
                if quality
                else "No quality metadata available."
            ),
            metadata={"quality": quality, "failure": failure, "fallback_graph": fallback_graph},
        )
    )

    learning: Dict[str, Any] = {}
    if req.enable_learning:
        org_uuid = _parse_client_uuid(client_id)
        refreshed = await refresh_learning_from_sources(session)
        calibrated = await calibrate_org_learning_policy(
            session,
            organization_id=org_uuid,
        )
        learning = {"refreshed": refreshed, "calibrated": calibrated}
        traces.append(
            AutonomyV2AgentTrace(
                stage="optimizer",
                status="ok",
                summary="Learning policy refrescada y calibrada para la organizacion.",
                metadata={"learning": learning},
            )
        )
    else:
        traces.append(
            AutonomyV2AgentTrace(
                stage="optimizer",
                status="skipped",
                summary="Learning optimizer desactivado por request.",
                metadata={},
            )
        )

    next_actions = _next_actions_from_quality(quality)
    if run.status in {"failed", "blocked"}:
        next_actions = [f"Fallback: {x['strategy']}" for x in fallback_graph[:3]] + next_actions

    status = run.status
    finished_at = datetime.now(timezone.utc)
    benchmark = _compute_benchmark(
        run_id=run_id,
        goal=req.goal,
        status=status,
        quality=quality,
        unique_sources=int(getattr(run.research, "unique_sources", 0)),
        planned_steps=len(plan_steps),
        started_at=started_at,
        finished_at=finished_at,
    )
    org_uuid = _parse_client_uuid(client_id)
    try:
        await _persist_benchmark(
            session=session,
            organization_id=org_uuid,
            benchmark=benchmark,
            metadata={
                "quality": quality,
                "sources": [
                    item.model_dump() if hasattr(item, "model_dump") else item
                    for item in list(getattr(run.research, "top_sources", []) or [])
                ],
                "fallback_graph": fallback_graph,
            },
        )
    except Exception as _bench_exc:
        import logging as _logging
        _logging.getLogger("kan_core.autonomy_v2").warning(
            "benchmark recording skipped (schema mismatch?): %s", _bench_exc
        )

    summary = (
        f"Autonomy v2 status={status}. quality_score={quality.get('score') if quality else 'n/a'}. "
        f"sources={getattr(run.research, 'unique_sources', 0)}"
    )
    return AutonomyV2RunResponse(
        run_id=run_id,
        goal=req.goal,
        status=status,
        summary=summary,
        traces=traces,
        research=run.research,
        execution=run.execution,
        quality=quality,
        benchmark=benchmark.model_dump(),
        learning=learning,
        next_actions=next_actions,
        error=(run.execution.error if run.execution else None),
        created_at=now,
        updated_at=finished_at,
    )
