from __future__ import annotations

from typing import List, Tuple

from .models import DiagnosticFinding, DiagnosticSeverity, EntityInsight, OperatorThresholds


def _finding(
    *,
    code: str,
    title: str,
    severity: DiagnosticSeverity,
    confidence: float,
    risk_score: float,
    explanation: str,
    evidence: dict,
) -> DiagnosticFinding:
    return DiagnosticFinding(
        code=code,
        title=title,
        severity=severity,
        confidence=max(0.0, min(1.0, confidence)),
        risk_score=max(0.0, min(1.0, risk_score)),
        explanation=explanation,
        evidence=evidence,
    )


def detect_findings(entity: EntityInsight, thresholds: OperatorThresholds) -> Tuple[List[DiagnosticFinding], bool]:
    metrics = entity.metrics
    findings: List[DiagnosticFinding] = []
    insufficient_data = (
        metrics.spend < thresholds.min_spend_for_decision
        or metrics.impressions < thresholds.min_impressions_for_decision
        or metrics.link_clicks < thresholds.min_link_clicks_for_decision
    )
    if insufficient_data:
        findings.append(
            _finding(
                code="insufficient_data",
                title="Datos insuficientes",
                severity=DiagnosticSeverity.LOW,
                confidence=0.95,
                risk_score=0.1,
                explanation="No hay suficiente gasto, impresiones o clics para tomar una decision fuerte sin riesgo innecesario.",
                evidence={
                    "spend": metrics.spend,
                    "impressions": metrics.impressions,
                    "link_clicks": metrics.link_clicks,
                },
            )
        )
        return findings, True

    if metrics.link_ctr and metrics.link_ctr < thresholds.low_link_ctr:
        findings.append(
            _finding(
                code="weak_creative",
                title="Creativo debil",
                severity=DiagnosticSeverity.MEDIUM,
                confidence=0.86,
                risk_score=0.4,
                explanation="El anuncio no esta generando interes suficiente en la audiencia fria o tibia.",
                evidence={"link_ctr": metrics.link_ctr, "threshold": thresholds.low_link_ctr},
            )
        )

    if metrics.frequency >= thresholds.fatigue_frequency and (
        metrics.ctr_drop_pct >= thresholds.fatigue_ctr_drop_pct
        or (metrics.link_ctr and metrics.link_ctr < thresholds.strong_link_ctr)
    ):
        findings.append(
            _finding(
                code="creative_fatigue",
                title="Fatiga creativa",
                severity=DiagnosticSeverity.MEDIUM,
                confidence=0.82,
                risk_score=0.45,
                explanation="La frecuencia ya esta alta y el interes viene cayendo; es probable que el creativo este saturado.",
                evidence={
                    "frequency": metrics.frequency,
                    "ctr_drop_pct": metrics.ctr_drop_pct,
                    "link_ctr": metrics.link_ctr,
                },
            )
        )

    if metrics.link_ctr >= thresholds.strong_link_ctr and (
        (metrics.qualified_rate and metrics.qualified_rate < 0.2)
        or (metrics.booked_rate and metrics.booked_rate < thresholds.low_booked_rate)
    ):
        findings.append(
            _finding(
                code="low_intent_traffic",
                title="Trafico de baja intencion",
                severity=DiagnosticSeverity.MEDIUM,
                confidence=0.79,
                risk_score=0.35,
                explanation="El anuncio despierta interes inicial, pero el trafico no avanza con suficiente intencion comercial.",
                evidence={
                    "link_ctr": metrics.link_ctr,
                    "qualified_rate": metrics.qualified_rate,
                    "booked_rate": metrics.booked_rate,
                },
            )
        )

    has_downstream_flow = (
        metrics.messaging_conversations > 0
        or metrics.leads > 0
        or (str(entity.channel).lower() == "whatsapp" and metrics.link_clicks >= thresholds.min_link_clicks_for_decision)
    )
    if has_downstream_flow and (
        metrics.reply_rate < thresholds.low_reply_rate
        or metrics.left_on_read_pct >= thresholds.high_left_on_read_pct
        or metrics.first_response_minutes >= thresholds.slow_first_response_minutes
    ):
        findings.append(
            _finding(
                code="poor_followup_downstream",
                title="Seguimiento debil despues del anuncio",
                severity=DiagnosticSeverity.HIGH,
                confidence=0.9,
                risk_score=0.65,
                explanation="Meta esta generando conversaciones o leads, pero el seguimiento por WhatsApp/ventas esta rompiendo la conversion.",
                evidence={
                    "reply_rate": metrics.reply_rate,
                    "left_on_read_pct": metrics.left_on_read_pct,
                    "first_response_minutes": metrics.first_response_minutes,
                },
            )
        )

    if metrics.audience_size > 0 and metrics.audience_size < thresholds.narrow_audience_size and metrics.link_ctr < thresholds.strong_link_ctr:
        findings.append(
            _finding(
                code="audience_mismatch",
                title="Audiencia desalineada",
                severity=DiagnosticSeverity.MEDIUM,
                confidence=0.68,
                risk_score=0.3,
                explanation="La audiencia parece demasiado estrecha o poco alineada con el mensaje actual.",
                evidence={"audience_size": metrics.audience_size, "link_ctr": metrics.link_ctr},
            )
        )

    if metrics.budget_change_pct >= thresholds.unstable_scaling_budget_change_pct and metrics.sales_change_pct <= thresholds.unstable_scaling_sales_change_pct:
        findings.append(
            _finding(
                code="unstable_scaling",
                title="Escalado inestable",
                severity=DiagnosticSeverity.HIGH,
                confidence=0.88,
                risk_score=0.7,
                explanation="Se subio presupuesto pero las ventas o conversiones no crecieron en la misma proporcion.",
                evidence={
                    "budget_change_pct": metrics.budget_change_pct,
                    "sales_change_pct": metrics.sales_change_pct,
                },
            )
        )

    return findings, False


def detect_over_fragmentation(entities: List[EntityInsight], thresholds: OperatorThresholds) -> List[DiagnosticFinding]:
    active = [item for item in entities if str(item.status).upper() in {"ACTIVE", "PAUSED", "LEARNING"}]
    total_spend = sum(item.metrics.spend for item in active)
    if not active or total_spend <= 0:
        return []
    adsets_per_100 = len(active) / max(total_spend / 100.0, 1.0)
    if adsets_per_100 <= thresholds.max_adsets_per_100_spend:
        return []
    return [
        _finding(
            code="over_fragmentation",
            title="Cuenta demasiado fragmentada",
            severity=DiagnosticSeverity.MEDIUM,
            confidence=0.77,
            risk_score=0.4,
            explanation="Hay demasiadas entidades compitiendo por poco presupuesto, lo que dificulta aprendizaje y lectura limpia.",
            evidence={"entity_count": len(active), "total_spend": total_spend, "adsets_per_100": adsets_per_100},
        )
    ]
