import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from brain.business_monitor import ingest_business_event
from brain.crm_engine import run_followups
from brain.predictor import generate_lead_predictions
from models import BusinessEvent, ConversationLog, IntegrationRunLog, Recommendation


_INTEGRATION_SUCCESS_STATUSES = {"integrated", "saved_without_workflow", "already_covered"}
_INTEGRATION_FAILED_STATUSES = {"failed", "manual_action_required"}


def _env_bool(name: str, default: str) -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes"}


def _clamp01(value: float) -> float:
    return max(0.0, min(float(value), 1.0))


def _pct_change(current: float, previous: float) -> Optional[float]:
    if previous <= 0:
        return None
    return (current - previous) / previous


def _severity_from_drop(drop_pct: float) -> str:
    if drop_pct >= 0.6:
        return "critical"
    if drop_pct >= 0.35:
        return "warning"
    return "info"


@dataclass(frozen=True)
class WindowMetrics:
    start: datetime
    end: datetime
    leads: int
    messages_in: int
    messages_out: int
    appointments: int
    sales: int
    errors: int
    api_failures: int
    user_messages: int
    assistant_messages: int
    unanswered_messages: int
    integration_runs: int
    integration_failed_runs: int
    integration_error_rate: float


async def _aggregate_window(
    session: AsyncSession,
    *,
    organization_id: UUID,
    start: datetime,
    end: datetime,
) -> WindowMetrics:
    # Business events (preferred, already normalized).
    event_stmt = (
        select(BusinessEvent.event_type, func.count(BusinessEvent.id))
        .where(
            BusinessEvent.organization_id == organization_id,
            BusinessEvent.created_at >= start,
            BusinessEvent.created_at < end,
        )
        .group_by(BusinessEvent.event_type)
    )
    event_rows = list((await session.execute(event_stmt)).all())
    event_counts: Dict[str, int] = {str(k): int(v) for (k, v) in event_rows if k}

    leads = sum(event_counts.get(k, 0) for k in ("lead_created", "lead_updated"))
    messages_in = int(event_counts.get("message_in", 0))
    messages_out = int(event_counts.get("message_out", 0))
    appointments = sum(event_counts.get(k, 0) for k in ("appointment_created", "appointment_reminder"))
    # Sales can come either from explicit events or from successful "payments" integrations.
    sales_events = sum(event_counts.get(k, 0) for k in ("sale_closed", "payment_success"))
    errors = sum(event_counts.get(k, 0) for k in ("error", "workflow_failed"))
    api_failures = sum(event_counts.get(k, 0) for k in ("api_failure", "provider_failure"))

    # Conversation logs: estimate unanswered messages.
    convo_stmt = (
        select(ConversationLog.role, func.count(ConversationLog.id))
        .where(
            ConversationLog.client_id == organization_id,
            ConversationLog.timestamp >= start,
            ConversationLog.timestamp < end,
        )
        .group_by(ConversationLog.role)
    )
    convo_rows = list((await session.execute(convo_stmt)).all())
    convo_counts: Dict[str, int] = {str(k): int(v) for (k, v) in convo_rows if k}
    user_messages = int(convo_counts.get("user", 0))
    assistant_messages = int(convo_counts.get("assistant", 0))
    unanswered = max(user_messages - assistant_messages, 0)

    # Integration runs for operational health and payment signals.
    run_stmt = (
        select(IntegrationRunLog.function_name, IntegrationRunLog.status)
        .where(
            IntegrationRunLog.client_id == organization_id,
            IntegrationRunLog.created_at >= start,
            IntegrationRunLog.created_at < end,
        )
    )
    run_rows = list((await session.execute(run_stmt)).all())
    integration_runs = len(run_rows)
    integration_failed = sum(1 for (_fn, status) in run_rows if str(status) in _INTEGRATION_FAILED_STATUSES)
    integration_error_rate = (integration_failed / integration_runs) if integration_runs else 0.0

    payments_success = sum(
        1
        for (fn, status) in run_rows
        if str(fn) == "payments" and str(status) in _INTEGRATION_SUCCESS_STATUSES
    )
    sales = int(sales_events + payments_success)

    return WindowMetrics(
        start=start,
        end=end,
        leads=int(leads),
        messages_in=int(messages_in),
        messages_out=int(messages_out),
        appointments=int(appointments),
        sales=int(sales),
        errors=int(errors),
        api_failures=int(api_failures),
        user_messages=int(user_messages),
        assistant_messages=int(assistant_messages),
        unanswered_messages=int(unanswered),
        integration_runs=int(integration_runs),
        integration_failed_runs=int(integration_failed),
        integration_error_rate=float(integration_error_rate),
    )


def _metrics_to_dict(m: WindowMetrics) -> Dict[str, Any]:
    return {
        "start": m.start.isoformat(),
        "end": m.end.isoformat(),
        "leads": m.leads,
        "messages_in": m.messages_in,
        "messages_out": m.messages_out,
        "appointments": m.appointments,
        "sales": m.sales,
        "errors": m.errors,
        "api_failures": m.api_failures,
        "user_messages": m.user_messages,
        "assistant_messages": m.assistant_messages,
        "unanswered_messages": m.unanswered_messages,
        "integration_runs": m.integration_runs,
        "integration_failed_runs": m.integration_failed_runs,
        "integration_error_rate": round(m.integration_error_rate, 6),
    }


def _build_recommendation_payload(
    *,
    organization_id: UUID,
    key: str,
    priority: str,
    confidence: float,
    detected_issue: str,
    proposal: str,
    expected_impact: str,
    desired_functions: List[str],
    request_text: str,
    requires_confirmation: bool,
    context: Dict[str, Any],
) -> Dict[str, Any]:
    return {
        "organization_id": organization_id,
        "recommendation_key": key,
        "priority": priority,
        "confidence": float(confidence),
        "detected_issue": detected_issue,
        "proposal": proposal,
        "expected_impact": expected_impact,
        "desired_functions": {"items": list(desired_functions)},
        "request_text": request_text,
        "requires_confirmation": bool(requires_confirmation),
        "status": "proposed",
        "reasoning": "Contextual business reasoning",
        "metadata": context,
    }


async def _create_recommendations_if_needed(
    session: AsyncSession,
    *,
    organization_id: UUID,
    proposals: List[Dict[str, Any]],
    cooldown_hours: int,
) -> List[Recommendation]:
    if not proposals:
        return []

    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=max(1, int(cooldown_hours)))
    created: List[Recommendation] = []

    for item in proposals:
        rec_key = str(item.get("recommendation_key") or "").strip()
        if not rec_key:
            continue

        existing_stmt = (
            select(Recommendation.id)
            .where(
                Recommendation.organization_id == organization_id,
                Recommendation.recommendation_key == rec_key,
                Recommendation.created_at >= since,
            )
            .limit(1)
        )
        existing = (await session.execute(existing_stmt)).scalars().first()
        if existing:
            continue

        row = Recommendation(
            organization_id=organization_id,
            objective_id=None,
            objective_run_id=None,
            recommendation_key=rec_key,
            priority=str(item.get("priority") or "medium"),
            confidence=float(item.get("confidence") or 0.0),
            detected_issue=str(item.get("detected_issue") or ""),
            proposal=str(item.get("proposal") or ""),
            expected_impact=str(item.get("expected_impact") or ""),
            desired_functions=item.get("desired_functions") if isinstance(item.get("desired_functions"), dict) else {},
            request_text=str(item.get("request_text") or ""),
            requires_confirmation=bool(item.get("requires_confirmation", True)),
            status="proposed",
            reasoning=str(item.get("reasoning") or "Contextual business reasoning"),
            recommendation_metadata=item.get("metadata") if isinstance(item.get("metadata"), dict) else {},
        )
        session.add(row)
        created.append(row)

    if created:
        await session.commit()
        for row in created:
            await session.refresh(row)
    return created


async def analyze_business_context(
    session: AsyncSession,
    *,
    organization_id: UUID,
    window_hours: int = 24,
    min_prev_sales: int = 3,
    sales_drop_threshold: float = 0.3,
    min_prev_leads: int = 5,
    leads_drop_threshold: float = 0.35,
) -> Dict[str, Any]:
    now = datetime.now(timezone.utc)
    hours = max(1, min(int(window_hours), 24 * 14))
    current_start = now - timedelta(hours=hours)
    prev_start = now - timedelta(hours=hours * 2)

    current = await _aggregate_window(session, organization_id=organization_id, start=current_start, end=now)
    previous = await _aggregate_window(session, organization_id=organization_id, start=prev_start, end=current_start)

    insights: List[Dict[str, Any]] = []

    # 1) Sales drop detection.
    sales_change = _pct_change(float(current.sales), float(previous.sales))
    if (
        previous.sales >= max(0, int(min_prev_sales))
        and sales_change is not None
        and sales_change <= -abs(float(sales_drop_threshold))
    ):
        drop_pct = abs(float(sales_change))
        insights.append(
            {
                "key": "sales_drop",
                "severity": _severity_from_drop(drop_pct),
                "metric_key": "sales",
                "current": current.sales,
                "previous": previous.sales,
                "change_pct": round(float(sales_change), 4),
                "reason": (
                    f"Ventas bajaron {drop_pct * 100:.1f}% en las ultimas {hours}h "
                    f"({current.sales} vs {previous.sales})."
                ),
            }
        )

    # 2) Lead drop detection (proxy for top-of-funnel).
    leads_change = _pct_change(float(current.leads), float(previous.leads))
    if (
        previous.leads >= max(0, int(min_prev_leads))
        and leads_change is not None
        and leads_change <= -abs(float(leads_drop_threshold))
    ):
        drop_pct = abs(float(leads_change))
        insights.append(
            {
                "key": "leads_drop",
                "severity": _severity_from_drop(drop_pct),
                "metric_key": "leads",
                "current": current.leads,
                "previous": previous.leads,
                "change_pct": round(float(leads_change), 4),
                "reason": (
                    f"Leads bajaron {drop_pct * 100:.1f}% en las ultimas {hours}h "
                    f"({current.leads} vs {previous.leads})."
                ),
            }
        )

    # 3) Operational instability: integration error rate spike.
    if current.integration_runs >= 8 and current.integration_error_rate >= max(previous.integration_error_rate + 0.2, 0.5):
        insights.append(
            {
                "key": "integration_error_spike",
                "severity": "warning",
                "metric_key": "integration_error_rate",
                "current": round(current.integration_error_rate, 4),
                "previous": round(previous.integration_error_rate, 4),
                "change_pct": None,
                "reason": (
                    f"Error rate de integraciones alto en {hours}h: "
                    f"{current.integration_error_rate:.2f} con {current.integration_failed_runs}/{current.integration_runs} fallos."
                ),
            }
        )

    # 4) Support latency proxy.
    if current.user_messages >= 40 and current.unanswered_messages >= 15:
        insights.append(
            {
                "key": "unanswered_messages",
                "severity": "warning",
                "metric_key": "unanswered_messages",
                "current": current.unanswered_messages,
                "previous": previous.unanswered_messages,
                "change_pct": _pct_change(float(current.unanswered_messages), float(previous.unanswered_messages)),
                "reason": f"Hay {current.unanswered_messages} mensajes sin respuesta en las ultimas {hours}h.",
            }
        )

    context = {
        "organization_id": str(organization_id),
        "window_hours": hours,
        "current": _metrics_to_dict(current),
        "previous": _metrics_to_dict(previous),
    }

    return {
        "context": context,
        "insights": insights,
    }
def propose_context_actions(
    *,
    organization_id: UUID,
    analysis: Dict[str, Any],
) -> List[Dict[str, Any]]:
    insights = analysis.get("insights") if isinstance(analysis.get("insights"), list) else []
    context = analysis.get("context") if isinstance(analysis.get("context"), dict) else {}
    window_hours = int(context.get("window_hours") or 24)
    proposals: List[Dict[str, Any]] = []
    today = datetime.now(timezone.utc).date().isoformat()

    for ins in insights:
        key = str(ins.get("key") or "").strip()
        if not key:
            continue
        severity = str(ins.get("severity") or "info")
        priority = "high" if severity == "critical" else ("medium" if severity == "warning" else "low")

        # Basic confidence: increases with sample size and magnitude of change.
        prev = float(ins.get("previous") or 0.0)
        cur = float(ins.get("current") or 0.0)
        change_pct = ins.get("change_pct")
        magnitude = abs(float(change_pct)) if isinstance(change_pct, (int, float)) else 0.2
        evidence = 1.0 if prev >= 10 else (0.7 if prev >= 5 else 0.55)
        confidence = _clamp01(0.55 + (0.25 * magnitude) + (0.2 * (evidence - 0.5)))

        if key == "sales_drop":
            proposals.append(
                _build_recommendation_payload(
                    organization_id=organization_id,
                    key=f"context:sales_drop:{window_hours}h:{today}",
                    priority=priority,
                    confidence=confidence,
                    detected_issue=str(ins.get("reason") or "Baja de ventas detectada"),
                    proposal="Activar campaña de reactivacion para leads calientes + seguimiento automatico.",
                    expected_impact="Recuperar ventas perdidas con follow-ups y mejor velocidad de respuesta.",
                    desired_functions=["crm", "messaging", "email"],
                    request_text=(
                        "Detecte baja de ventas. Quiero una campaña de reactivacion: "
                        "priorizar leads con alta probabilidad, enviar mensajes y correos, "
                        "y programar seguimiento automatico. "
                        f"Ventana analizada: {window_hours}h."
                    ),
                    requires_confirmation=True,
                    context={"insight": ins, "business_context": context},
                )
            )
        elif key == "leads_drop":
            proposals.append(
                _build_recommendation_payload(
                    organization_id=organization_id,
                    key=f"context:leads_drop:{window_hours}h:{today}",
                    priority=priority,
                    confidence=confidence,
                    detected_issue=str(ins.get("reason") or "Baja de leads detectada"),
                    proposal="Activar captura de leads + nurturing por email/mensajeria.",
                    expected_impact="Aumentar leads entrantes y conversion a citas/ventas.",
                    desired_functions=["meta", "crm", "email"],
                    request_text=(
                        "Detecte baja de leads. Quiero activar captura de leads y un flujo de nurturing "
                        "por WhatsApp/email para convertirlos a citas/ventas."
                    ),
                    requires_confirmation=True,
                    context={"insight": ins, "business_context": context},
                )
            )
        elif key == "unanswered_messages":
            proposals.append(
                _build_recommendation_payload(
                    organization_id=organization_id,
                    key=f"context:unanswered_messages:{window_hours}h:{today}",
                    priority=priority,
                    confidence=confidence,
                    detected_issue=str(ins.get("reason") or "Mensajes sin respuesta"),
                    proposal="Activar respuestas automaticas y pipeline de CRM.",
                    expected_impact="Reducir leads perdidos fuera de horario.",
                    desired_functions=["meta", "crm"],
                    request_text="Quiero activar respuestas automaticas para mensajes no atendidos y guardar leads en CRM.",
                    requires_confirmation=True,
                    context={"insight": ins, "business_context": context},
                )
            )
        elif key == "integration_error_spike":
            proposals.append(
                _build_recommendation_payload(
                    organization_id=organization_id,
                    key=f"context:integration_error_spike:{window_hours}h:{today}",
                    priority=priority,
                    confidence=_clamp01(max(confidence, 0.7)),
                    detected_issue=str(ins.get("reason") or "Error rate alto en integraciones"),
                    proposal="Reparar integraciones con fallos recientes y habilitar fallback de proveedor para funciones criticas.",
                    expected_impact="Menos fallos operativos => menos leads perdidos y mayor conversion.",
                    desired_functions=["payments", "messaging", "email"],
                    request_text=(
                        "Detecte un spike de errores en integraciones. Quiero que el sistema: "
                        "1) diagnostique causa (token expirado, endpoint, rate-limit), "
                        "2) aplique auto-repair, "
                        "3) pruebe fallback de proveedor si es necesario, "
                        "4) deje logs y estado."
                    ),
                    requires_confirmation=True,
                    context={"insight": ins, "business_context": context},
                )
            )

    return proposals


async def run_business_context_cycle(
    session: AsyncSession,
    *,
    organization_id: UUID,
    window_hours: int,
    min_prev_sales: int,
    sales_drop_threshold: float,
    create_recommendations: bool,
    recommendation_cooldown_hours: int,
    execute_campaigns: bool,
    followups_max_jobs: int,
    followups_dry_run: bool,
) -> Dict[str, Any]:
    analysis = await analyze_business_context(
        session,
        organization_id=organization_id,
        window_hours=window_hours,
        min_prev_sales=min_prev_sales,
        sales_drop_threshold=sales_drop_threshold,
    )
    insights = analysis.get("insights") if isinstance(analysis.get("insights"), list) else []

    # Persist a single business event for observability.
    try:
        await ingest_business_event(
            session,
            organization_id=organization_id,
            event_type="context_insight",
            source="autonomy.business_context",
            payload={
                "context": analysis.get("context"),
                "insights": insights,
            },
            channel="system",
            entity_id="business_context",
        )
    except Exception:
        pass

    proposals = propose_context_actions(organization_id=organization_id, analysis=analysis)

    created: List[Recommendation] = []
    if create_recommendations and proposals:
        created = await _create_recommendations_if_needed(
            session,
            organization_id=organization_id,
            proposals=proposals,
            cooldown_hours=recommendation_cooldown_hours,
        )

    campaign_result: Dict[str, Any] = {"executed": False, "detail": {}}
    if execute_campaigns and insights and _env_bool("ENABLE_CRM_INTERNAL", "true"):
        has_sales_drop = any(str(item.get("key")) == "sales_drop" for item in insights)
        if has_sales_drop:
            # Update lead probabilities before followups.
            try:
                await generate_lead_predictions(session, organization_id=organization_id, limit=200)
            except Exception:
                pass
            try:
                result = await run_followups(
                    session,
                    organization_id=organization_id,
                    max_jobs=max(1, min(int(followups_max_jobs), 200)),
                    dry_run=bool(followups_dry_run),
                )
                campaign_result = {
                    "executed": not bool(followups_dry_run),
                    "detail": {
                        "jobs": [str(job.id) for job in (result.get("jobs") or [])],
                        "executed": int(result.get("executed") or 0),
                        "skipped": int(result.get("skipped") or 0),
                        "dry_run": bool(followups_dry_run),
                    },
                }
            except Exception as exc:
                campaign_result = {"executed": False, "detail": {"error": str(exc)}}

    return {
        "analysis": analysis,
        "created_recommendations": [str(x.id) for x in created],
        "campaign": campaign_result,
    }
