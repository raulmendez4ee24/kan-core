import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, List, Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from brain.core import CognitiveEngine
from models import ApiIntegration, ConversationLog, IntegrationRunLog
from observability import capture_exception
from schemas.optimizer import GoalMetricsSnapshot, GoalRecommendation

_LEAD_HINTS = (
    "lead",
    "cliente",
    "prospecto",
    "cotizacion",
    "cotizar",
    "comprar",
    "precio",
    "quote",
)
_APPOINTMENT_HINTS = (
    "cita",
    "agendar",
    "agenda",
    "appointment",
    "calendar",
    "calendario",
    "reservar",
    "reserva",
)


@dataclass
class GoalAnalysis:
    goal: str
    metrics: GoalMetricsSnapshot
    recommendations: List[GoalRecommendation]
    summary: str
    llm_summary: str | None


def _normalize_text(value: str) -> str:
    token = (value or "").strip().lower()
    token = re.sub(r"\s+", " ", token)
    return token


def _infer_integration_function(integration: ApiIntegration) -> str:
    metadata = integration.integration_metadata or {}
    provider_function = metadata.get("selected_function")
    if isinstance(provider_function, str) and provider_function.strip():
        return provider_function.strip().lower()
    provider_id = str(metadata.get("provider_id") or "").strip().lower()
    if provider_id:
        return provider_id
    return integration.name.strip().lower()


def _build_summary(goal: str, recommendations: Sequence[GoalRecommendation]) -> str:
    if not recommendations:
        return (
            f"Se analizaron metricas para el objetivo '{goal}' y no se detectaron "
            "oportunidades criticas en este ciclo."
        )
    top = recommendations[0]
    return (
        f"Se detectaron {len(recommendations)} oportunidades para '{goal}'. "
        f"Prioridad principal: {top.detected_issue}. Propuesta: {top.proposal}"
    )


def _lead_and_appointment_counts(rows: Sequence[ConversationLog]) -> tuple[int, int]:
    leads = 0
    appointments = 0
    for row in rows:
        content = _normalize_text(row.content)
        if any(token in content for token in _LEAD_HINTS):
            leads += 1
        if any(token in content for token in _APPOINTMENT_HINTS):
            appointments += 1
    return leads, appointments


def detect_opportunities(metrics: GoalMetricsSnapshot) -> List[GoalRecommendation]:
    opportunities: List[GoalRecommendation] = []
    available = set(metrics.active_functions)

    if metrics.total_messages >= 40 and metrics.unanswered_messages >= 15:
        opportunities.append(
            GoalRecommendation(
                id="op_unanswered_messages",
                priority="high",
                confidence=0.86,
                detected_issue=(
                    f"Hay {metrics.unanswered_messages} mensajes sin respuesta en el periodo analizado."
                ),
                proposal=(
                    "Activar respuesta automatica omnicanal y seguimiento de leads con CRM."
                ),
                expected_impact="Menos leads perdidos fuera de horario y mayor velocidad de respuesta.",
                desired_functions=["meta", "crm"],
                request_text=(
                    "Necesito automatizar respuestas de WhatsApp/Meta para mensajes no atendidos y "
                    "guardar cada lead en CRM con seguimiento automatico."
                ),
            )
        )

    if (
        metrics.total_messages >= 30
        and metrics.leads_detected >= 20
        and metrics.appointment_rate < 0.3
    ):
        opportunities.append(
            GoalRecommendation(
                id="op_low_appointment_rate",
                priority="high",
                confidence=0.81,
                detected_issue=(
                    f"Se detectaron {metrics.leads_detected} leads y solo "
                    f"{metrics.appointments_detected} señales de cita ({metrics.appointment_rate:.2f})."
                ),
                proposal=(
                    "Implementar recordatorios y secuencias para convertir leads en citas."
                ),
                expected_impact="Incremento de citas agendadas y mejor conversion comercial.",
                desired_functions=["calendar", "crm", "messaging"],
                request_text=(
                    "Quiero automatizacion para convertir leads a citas: CRM + agenda + mensajes "
                    "de seguimiento y recordatorios."
                ),
            )
        )

    if metrics.total_runs >= 8 and (metrics.error_rate >= 30.0 or metrics.failed_runs >= 10):
        opportunities.append(
            GoalRecommendation(
                id="op_high_error_rate",
                priority="high",
                confidence=0.79,
                detected_issue=(
                    f"La tasa de error operativa es {metrics.error_rate:.2f}% "
                    f"con {metrics.failed_runs} ejecuciones fallidas."
                ),
                proposal=(
                    "Refactorizar automatizaciones criticas y reforzar proveedor alterno."
                ),
                expected_impact="Menos caidas de flujo y operaciones mas resilientes.",
                desired_functions=["messaging", "email"],
                request_text=(
                    "Necesito optimizar automatizaciones de mensajeria y correo con "
                    "fallback de proveedores y validacion de endpoints."
                ),
            )
        )

    if "payments" not in available and metrics.total_messages >= 30 and metrics.leads_detected >= 10:
        opportunities.append(
            GoalRecommendation(
                id="op_missing_payments",
                priority="medium",
                confidence=0.74,
                detected_issue=(
                    "Se detectan oportunidades comerciales sin una automatizacion de pagos activa."
                ),
                proposal="Conectar pagos para cerrar ventas dentro del flujo conversacional.",
                expected_impact="Mejor tasa de cierre y menor friccion en cobro.",
                desired_functions=["payments"],
                request_text=(
                    "Quiero integrar pagos para cobrar de forma automatica desde conversaciones "
                    "y registrar transacciones."
                ),
            )
        )

    if "email" not in available and metrics.total_messages >= 30 and metrics.leads_detected >= 15:
        opportunities.append(
            GoalRecommendation(
                id="op_missing_email_nurture",
                priority="medium",
                confidence=0.7,
                detected_issue="No hay automatizacion de email para nutrir leads detectados.",
                proposal="Activar secuencias de email para seguimiento post-contacto.",
                expected_impact="Mayor conversion diferida y recuperacion de leads tibios.",
                desired_functions=["email", "crm"],
                request_text=(
                    "Necesito integracion de email y CRM para secuencias automaticas de "
                    "seguimiento comercial."
                ),
            )
        )

    if not opportunities:
        opportunities.append(
            GoalRecommendation(
                id="op_growth_iteration",
                priority="low",
                confidence=0.65,
                detected_issue="No hay alertas criticas; oportunidad de optimizacion incremental.",
                proposal="Probar una automatizacion adicional de crecimiento y medir impacto.",
                expected_impact="Aprendizaje continuo y mejora incremental del embudo.",
                desired_functions=["crm"],
                request_text=(
                    "Quiero mejorar conversion comercial con optimizacion de CRM y "
                    "segmentacion de leads."
                ),
            )
        )

    weight = {"high": 0, "medium": 1, "low": 2}
    opportunities.sort(key=lambda item: (weight[item.priority], -item.confidence))
    return opportunities


async def _generate_llm_summary(
    *,
    client_id: str,
    goal: str,
    metrics: GoalMetricsSnapshot,
    recommendations: Sequence[GoalRecommendation],
) -> str | None:
    enabled = os.getenv("ENABLE_GOAL_LLM_RECOMMENDER", "true").lower() in {
        "1",
        "true",
        "yes",
    }
    if not enabled:
        return None

    try:
        engine = CognitiveEngine.get_instance()
    except Exception:
        return None

    compact_recs = [
        {
            "id": item.id,
            "priority": item.priority,
            "issue": item.detected_issue,
            "proposal": item.proposal,
            "impact": item.expected_impact,
            "functions": item.desired_functions,
        }
        for item in recommendations[:5]
    ]
    prompt = (
        "Analiza estas metricas y recomendaciones para un negocio y redacta una "
        "sugerencia ejecutiva breve en espanol con foco en ingresos. "
        "Incluye 1 accion prioritaria y porque.\n\n"
        f"Objetivo: {goal}\n"
        f"Metricas: {json.dumps(metrics.model_dump(), ensure_ascii=True)}\n"
        f"Recomendaciones: {json.dumps(compact_recs, ensure_ascii=True)}"
    )
    try:
        result = await engine.generate_response(
            client_id=client_id,
            session_id=f"goal-optimizer:{datetime.now(timezone.utc).date().isoformat()}",
            message=prompt,
        )
    except Exception as exc:
        capture_exception(
            exc,
            {"source": "goal_optimizer", "stage": "llm_summary", "client_id": client_id},
        )
        return None

    text = str(result.get("content") or "").strip()
    return text[:1800] if text else None


async def collect_goal_metrics(
    session: AsyncSession,
    *,
    client_uuid: UUID,
    window_days: int,
) -> GoalMetricsSnapshot:
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=max(1, window_days))

    convo_stmt = (
        select(ConversationLog)
        .where(
            ConversationLog.client_id == client_uuid,
            ConversationLog.timestamp >= since,
        )
        .order_by(ConversationLog.timestamp.desc())
    )
    convo_result = await session.execute(convo_stmt)
    conversation_rows = list(convo_result.scalars().all())

    run_stmt = (
        select(IntegrationRunLog)
        .where(
            IntegrationRunLog.client_id == client_uuid,
            IntegrationRunLog.created_at >= since,
        )
        .order_by(IntegrationRunLog.created_at.desc())
    )
    run_result = await session.execute(run_stmt)
    run_rows = list(run_result.scalars().all())

    integ_stmt = (
        select(ApiIntegration)
        .where(
            ApiIntegration.client_id == client_uuid,
            ApiIntegration.is_active.is_(True),
        )
        .order_by(ApiIntegration.updated_at.desc())
    )
    integ_result = await session.execute(integ_stmt)
    active_integrations = list(integ_result.scalars().all())

    user_messages = sum(1 for item in conversation_rows if item.role == "user")
    assistant_messages = sum(1 for item in conversation_rows if item.role == "assistant")
    unanswered = max(user_messages - assistant_messages, 0)
    leads_detected, appointments_detected = _lead_and_appointment_counts(conversation_rows)
    conversation_sessions = {item.session_id for item in conversation_rows if item.session_id}

    success_statuses = {"integrated", "saved_without_workflow", "already_covered"}
    failed_statuses = {"failed", "manual_action_required"}
    successful_runs = sum(1 for item in run_rows if item.status in success_statuses)
    failed_runs = sum(1 for item in run_rows if item.status in failed_statuses)
    total_runs = len(run_rows)
    error_rate = (failed_runs / total_runs * 100.0) if total_runs else 0.0
    avg_duration = (
        sum(float(item.duration_ms or 0.0) for item in run_rows) / total_runs
        if total_runs
        else 0.0
    )
    api_cost = sum(float(item.cost_usd or 0.0) for item in run_rows)

    active_functions = sorted(
        {
            _infer_integration_function(item)
            for item in active_integrations
            if _infer_integration_function(item)
        }
    )

    return GoalMetricsSnapshot(
        window_days=max(1, window_days),
        total_conversations=len(conversation_sessions),
        total_messages=len(conversation_rows),
        user_messages=user_messages,
        assistant_messages=assistant_messages,
        unanswered_messages=unanswered,
        leads_detected=leads_detected,
        appointments_detected=appointments_detected,
        appointment_rate=round((appointments_detected / leads_detected), 4) if leads_detected else 0.0,
        active_integrations=len(active_integrations),
        active_functions=active_functions,
        total_runs=total_runs,
        successful_runs=successful_runs,
        failed_runs=failed_runs,
        error_rate=round(error_rate, 2),
        avg_duration_ms=round(avg_duration, 2),
        api_cost_usd=round(api_cost, 6),
        auto_repair_used=sum(1 for item in run_rows if bool(item.auto_repair_used)),
        fallback_used=sum(1 for item in run_rows if bool(item.fallback_used)),
    )


async def analyze_goal_driven_plan(
    session: AsyncSession,
    *,
    client_id: str,
    client_uuid: UUID,
    goal: str,
    window_days: int,
    max_recommendations: int,
    include_llm_summary: bool,
) -> GoalAnalysis:
    metrics = await collect_goal_metrics(
        session,
        client_uuid=client_uuid,
        window_days=window_days,
    )
    recommendations = detect_opportunities(metrics)[: max(1, max_recommendations)]
    summary = _build_summary(goal, recommendations)

    llm_summary = None
    if include_llm_summary:
        llm_summary = await _generate_llm_summary(
            client_id=client_id,
            goal=goal,
            metrics=metrics,
            recommendations=recommendations,
        )

    return GoalAnalysis(
        goal=goal,
        metrics=metrics,
        recommendations=recommendations,
        summary=summary,
        llm_summary=llm_summary,
    )
