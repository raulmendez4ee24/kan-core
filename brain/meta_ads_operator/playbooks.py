from __future__ import annotations

from typing import List

from .models import (
    ActionProposal,
    ActionType,
    DiagnosticFinding,
    DiagnosticSeverity,
    EntityInsight,
    EntityReport,
    EntityType,
    OperatorThresholds,
)


def _action(
    *,
    action_type: ActionType,
    entity: EntityInsight,
    confidence: float,
    risk_score: float,
    reason: str,
    params: dict | None = None,
) -> ActionProposal:
    return ActionProposal(
        action_type=action_type,
        target_entity_type=entity.entity_type,
        target_entity_id=entity.entity_id,
        confidence=max(0.0, min(1.0, confidence)),
        risk_score=max(0.0, min(1.0, risk_score)),
        reason=reason,
        dry_run=True,
        params=dict(params or {}),
    )


def recommendations_for_entity(
    entity: EntityInsight,
    findings: List[DiagnosticFinding],
    *,
    thresholds: OperatorThresholds,
) -> EntityReport:
    flags = [item.code for item in findings]
    actions: List[ActionProposal] = []
    next_steps: List[str] = []
    finding_codes = {item.code for item in findings}

    if "insufficient_data" in finding_codes:
        actions.append(
            _action(
                action_type=ActionType.HOLD_STEADY,
                entity=entity,
                confidence=0.96,
                risk_score=0.1,
                reason="No hay suficiente data para tocar presupuesto o pausar sin riesgo de matar una posible ganadora.",
            )
        )
        next_steps.append("Esperar mas gasto o mas clics antes de tomar una decision fuerte.")
        return EntityReport(entity=entity, findings=findings, actions=actions, flags=flags, next_steps=next_steps)

    metrics = entity.metrics

    if "poor_followup_downstream" in finding_codes:
        actions.append(
            _action(
                action_type=ActionType.INSPECT_FOLLOWUP_FUNNEL,
                entity=entity,
                confidence=0.92,
                risk_score=0.2,
                reason="Las conversaciones estan entrando, pero el problema parece estar en WhatsApp, ventas o velocidad de respuesta, no en Meta.",
            )
        )
        next_steps.append("Revisar primer mensaje, tiempo de respuesta, secuencia de rescate y booking rate.")

    if "creative_fatigue" in finding_codes or "weak_creative" in finding_codes:
        actions.append(
            _action(
                action_type=ActionType.MARK_FOR_CREATIVE_REFRESH,
                entity=entity,
                confidence=0.85,
                risk_score=0.25,
                reason="El creativo necesita refresh; conviene cambiar hook, demo o angulo antes que tocar targeting agresivamente.",
            )
        )
        next_steps.append("Probar 3-5 creativos nuevos sin resetear toda la estructura.")

    if "unstable_scaling" in finding_codes:
        actions.append(
            _action(
                action_type=ActionType.DECREASE_BUDGET,
                entity=entity,
                confidence=0.79,
                risk_score=0.72,
                reason="Se escaló sin respuesta clara en ventas; bajar o estabilizar presupuesto es una accion de riesgo alto pero justificable.",
                params={"suggested_pct": 10},
            )
        )
        next_steps.append("Congelar escalado y validar que la calidad del lead no haya caido.")

    if "low_intent_traffic" in finding_codes:
        actions.append(
            _action(
                action_type=ActionType.INSPECT_FOLLOWUP_FUNNEL,
                entity=entity,
                confidence=0.74,
                risk_score=0.22,
                reason="Buen interes inicial pero baja intencion comercial; primero revisar oferta y mensaje de WhatsApp antes de culpar al anuncio.",
            )
        )
        next_steps.append("Ajustar el angulo de la oferta para atraer menos curiosos y mas compradores.")

    if "audience_mismatch" in finding_codes and "weak_creative" not in finding_codes:
        actions.append(
            _action(
                action_type=ActionType.HOLD_STEADY,
                entity=entity,
                confidence=0.64,
                risk_score=0.18,
                reason="Hay senal de desalineacion de audiencia, pero no lo suficiente para tomar una accion agresiva sin mas testing.",
            )
        )
        next_steps.append("Probar una version mas abierta o Advantage+ antes de pausar.")

    winner_signal = (
        metrics.link_ctr >= thresholds.strong_link_ctr
        and (metrics.roas >= thresholds.min_roas_for_winner or metrics.close_rate >= thresholds.min_close_rate_for_winner)
        and "poor_followup_downstream" not in finding_codes
        and "unstable_scaling" not in finding_codes
    )
    if winner_signal:
        actions.append(
            _action(
                action_type=ActionType.DUPLICATE_WINNER,
                entity=entity,
                confidence=0.73,
                risk_score=0.35,
                reason="La entidad parece ganadora y se puede escalar de forma controlada duplicando sin tocar la original.",
            )
        )
        actions.append(
            _action(
                action_type=ActionType.INCREASE_BUDGET,
                entity=entity,
                confidence=0.67,
                risk_score=0.58,
                reason="Hay base para aumentar presupuesto, pero es una accion de riesgo medio y no debe hacerse con senales debiles.",
                params={"suggested_pct": 10},
            )
        )
        next_steps.append("Escalar solo si el backend de conversion y WhatsApp aguanta la demanda.")

    if not actions:
        actions.append(
            _action(
                action_type=ActionType.HOLD_STEADY,
                entity=entity,
                confidence=0.6,
                risk_score=0.15,
                reason="No hay senal suficiente para una accion fuerte; conviene sostener, observar y seguir recolectando data.",
            )
        )
        next_steps.append("Mantener monitoreo 24-48h y validar tendencia antes de tocar la cuenta.")

    return EntityReport(entity=entity, findings=findings, actions=actions, flags=flags, next_steps=next_steps)
