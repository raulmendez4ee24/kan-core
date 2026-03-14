"""
ProactiveMonitor — autonomous background agent that detects business anomalies
and dispatches corrective actions WITHOUT requiring human input.

This is the core autonomy upgrade: the system acts on its own initiative
based on observed metrics, not just in response to user messages.
"""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from database import AsyncSessionLocal
from models import (
    BusinessEvent,
    Client,
    IntegrationRunLog,
    Recommendation,
)
from observability import capture_exception
from tools.n8n_client import send_to_n8n_with_response

logger = logging.getLogger("kan_core.proactive_monitor")


@dataclass
class ProactiveAnomaly:
    org_id: UUID
    anomaly_type: str
    severity: str  # "critical" | "high" | "medium"
    details: Dict[str, Any] = field(default_factory=dict)


class ProactiveMonitor:
    """Singleton background monitor that runs autonomously on a schedule."""

    _running: bool = False
    _task: Optional[asyncio.Task] = None
    _last_run: Optional[datetime] = None
    _last_result: Optional[Dict[str, Any]] = None

    @classmethod
    def start(cls) -> None:
        if not cls._running:
            cls._running = True
            cls._task = asyncio.create_task(cls._monitor_loop())
            logger.info("ProactiveMonitor started (interval=%sm)", os.getenv("PROACTIVE_MONITOR_INTERVAL_MINUTES", "30"))

    @classmethod
    def stop(cls) -> None:
        cls._running = False
        if cls._task and not cls._task.done():
            cls._task.cancel()
        logger.info("ProactiveMonitor stopped")

    @classmethod
    def status(cls) -> Dict[str, Any]:
        return {
            "running": cls._running,
            "last_run": cls._last_run.isoformat() if cls._last_run else None,
            "last_result": cls._last_result,
        }

    @classmethod
    async def _monitor_loop(cls) -> None:
        # Wait for the app to finish starting up.
        await asyncio.sleep(int(os.getenv("PROACTIVE_MONITOR_STARTUP_DELAY_SECONDS", "45")))
        interval = int(os.getenv("PROACTIVE_MONITOR_INTERVAL_MINUTES", "30")) * 60
        while cls._running:
            try:
                result = await cls.run_cycle()
                cls._last_run = datetime.now(timezone.utc)
                cls._last_result = result
            except asyncio.CancelledError:
                break
            except Exception as exc:
                capture_exception(exc, {"source": "proactive_monitor"})
                logger.exception("ProactiveMonitor cycle failed")
            await asyncio.sleep(interval)

    @classmethod
    async def run_cycle(cls) -> Dict[str, Any]:
        """
        Run one full proactive monitoring cycle across all active organizations.
        Returns a summary dict of what was found and done.
        """
        logger.info("ProactiveMonitor: starting cycle")
        result: Dict[str, Any] = {
            "orgs_checked": 0,
            "anomalies_found": 0,
            "recommendations_created": 0,
            "actions_dispatched": 0,
            "cycle_at": datetime.now(timezone.utc).isoformat(),
        }

        async with AsyncSessionLocal() as session:
            orgs = await _get_active_orgs(session)
            result["orgs_checked"] = len(orgs)

            for org in orgs:
                try:
                    anomalies = await _detect_anomalies(session, org.id)
                    result["anomalies_found"] += len(anomalies)

                    for anomaly in anomalies:
                        rec = await _create_recommendation(session, anomaly)
                        if rec is None:
                            continue
                        result["recommendations_created"] += 1

                        # Auto-dispatch when confidence exceeds the threshold.
                        threshold = float(os.getenv("PROACTIVE_AUTO_DISPATCH_THRESHOLD", "0.85"))
                        if rec.confidence >= threshold:
                            if os.getenv("ENABLE_AUTONOMOUS_CASE_MANAGER", "false").lower() in {"1", "true", "yes"}:
                                from brain.autonomous_case_manager import (
                                    classify_case_type,
                                    create_or_update_case as create_autonomous_case,
                                )

                                resolved_case_type = classify_case_type(
                                    current_goal=f"Responder anomalia: {anomaly.anomaly_type}",
                                    business_context={
                                        "source": "proactive_monitor",
                                        "anomaly_type": anomaly.anomaly_type,
                                        "severity": anomaly.severity,
                                    },
                                    requested_case_type=None,
                                    allow_generic=True,
                                ) or "ops_anomaly_response"

                                case_result = await create_autonomous_case(
                                    session,
                                    organization_id=org.id,
                                    case_type=resolved_case_type,
                                    current_goal=f"Responder anomalia: {anomaly.anomaly_type}",
                                    business_context={
                                        "source": "proactive_monitor",
                                        "anomaly_type": anomaly.anomaly_type,
                                        "severity": anomaly.severity,
                                        "details": anomaly.details,
                                        "recommendation_id": str(rec.id),
                                    },
                                    run_now=True,
                                )
                                if case_result.get("run"):
                                    result["actions_dispatched"] += 1
                            else:
                                dispatched = await _dispatch_recommendation(rec)
                                if dispatched:
                                    result["actions_dispatched"] += 1
                                    logger.info(
                                        "ProactiveMonitor: auto-dispatched %s for org %s (confidence=%.2f)",
                                        rec.recommendation_key,
                                        org.id,
                                        rec.confidence,
                                    )
                except Exception as exc:
                    capture_exception(exc, {"source": "proactive_monitor", "org_id": str(org.id)})
                    logger.exception("ProactiveMonitor: failed to process org %s", org.id)

        logger.info("ProactiveMonitor: cycle complete %s", result)
        return result


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

async def _get_active_orgs(session: AsyncSession) -> List[Client]:
    stmt = select(Client).where(Client.is_active.is_(True)).limit(200)
    return list((await session.execute(stmt)).scalars().all())


async def _detect_anomalies(session: AsyncSession, org_id: UUID) -> List[ProactiveAnomaly]:
    anomalies: List[ProactiveAnomaly] = []
    window_hours = int(os.getenv("PROACTIVE_WINDOW_HOURS", "4"))
    since = datetime.now(timezone.utc) - timedelta(hours=window_hours)

    # 1. High integration error rate.
    total_stmt = select(func.count(IntegrationRunLog.id)).where(
        IntegrationRunLog.client_id == org_id,
        IntegrationRunLog.created_at >= since,
    )
    total_runs = (await session.execute(total_stmt)).scalar() or 0

    if total_runs >= 5:
        failed_stmt = select(func.count(IntegrationRunLog.id)).where(
            IntegrationRunLog.client_id == org_id,
            IntegrationRunLog.created_at >= since,
            IntegrationRunLog.status.in_(["error", "failed", "timeout"]),
        )
        failed_runs = (await session.execute(failed_stmt)).scalar() or 0
        error_rate = failed_runs / total_runs
        threshold = float(os.getenv("PROACTIVE_ERROR_RATE_THRESHOLD", "0.40"))

        if error_rate >= threshold:
            anomalies.append(
                ProactiveAnomaly(
                    org_id=org_id,
                    anomaly_type="high_integration_error_rate",
                    severity="critical" if error_rate >= 0.6 else "high",
                    details={
                        "error_rate": round(error_rate, 3),
                        "failed_runs": failed_runs,
                        "total_runs": total_runs,
                        "window_hours": window_hours,
                    },
                )
            )

    # 2. Objectives with critically low confidence (stalled automation).
    # Use minimal columns (no client_id/user_id) for schema compatibility.
    stale_run_count = 0
    avg_confidence = 0.0
    try:
        stale_result = await session.execute(
            text(
                "SELECT id, confidence FROM objective_runs "
                "WHERE organization_id = :org_id AND confidence < 0.4 AND created_at >= :since LIMIT 10"
            ),
            {"org_id": org_id, "since": since},
        )
        rows = stale_result.fetchall()
        stale_run_count = len(rows)
        if rows:
            avg_confidence = round(sum(float(r[1] or 0) for r in rows) / len(rows), 3)
    except Exception:
        pass

    if stale_run_count:
        anomalies.append(
            ProactiveAnomaly(
                org_id=org_id,
                anomaly_type="low_confidence_objectives",
                severity="medium",
                details={
                    "stale_run_count": stale_run_count,
                    "avg_confidence": avg_confidence,
                },
            )
        )

    # 3. System inactivity: no messages in the observation window for an org
    #    that was previously active.
    msg_stmt = select(func.count(BusinessEvent.id)).where(
        BusinessEvent.organization_id == org_id,
        BusinessEvent.event_type == "message_in",
        BusinessEvent.created_at >= since,
    )
    msg_count = (await session.execute(msg_stmt)).scalar() or 0

    if msg_count == 0 and total_runs == 0:
        hist_stmt = select(func.count(BusinessEvent.id)).where(
            BusinessEvent.organization_id == org_id,
        )
        hist_count = (await session.execute(hist_stmt)).scalar() or 0
        # Only flag if the org has a meaningful history (avoids noise for new orgs).
        if hist_count >= int(os.getenv("PROACTIVE_INACTIVITY_MIN_HISTORY", "20")):
            anomalies.append(
                ProactiveAnomaly(
                    org_id=org_id,
                    anomaly_type="system_inactivity",
                    severity="medium",
                    details={"hours_inactive": window_hours, "historical_events": hist_count},
                )
            )

    return anomalies


_ANOMALY_TEMPLATES: Dict[str, Dict[str, Any]] = {
    "high_integration_error_rate": {
        "key": "fix_integration_errors",
        "issue": "Alta tasa de errores en integraciones detectada automáticamente.",
        "proposal": "Revisar y reparar integraciones fallidas. Verificar credenciales y endpoints.",
        "impact": "Reducir pérdida de datos y mejorar confiabilidad del sistema.",
        "request": "Analiza los errores de integración recientes y propón acciones de reparación.",
        "priority": "high",
        "base_confidence": 0.82,
    },
    "low_confidence_objectives": {
        "key": "reoptimize_objectives",
        "issue": "Objetivos con baja confianza detectados automáticamente.",
        "proposal": "Re-analizar objetivos con estrategias alternativas.",
        "impact": "Aumentar tasa de éxito en automatizaciones de negocio.",
        "request": "Revisa los objetivos con baja confianza y genera nuevas recomendaciones de optimización.",
        "priority": "medium",
        "base_confidence": 0.70,
    },
    "system_inactivity": {
        "key": "check_system_health",
        "issue": "Sistema sin actividad durante el período de monitoreo.",
        "proposal": "Verificar salud del sistema y conectividad de webhooks.",
        "impact": "Detectar y prevenir interrupciones de servicio no detectadas.",
        "request": "Verifica el estado de salud del sistema y la conectividad de webhooks activos.",
        "priority": "medium",
        "base_confidence": 0.65,
    },
}

_SEVERITY_CONFIDENCE_BOOST = {"critical": 0.10, "high": 0.05, "medium": 0.0}


async def _create_recommendation(
    session: AsyncSession, anomaly: ProactiveAnomaly
) -> Optional[Recommendation]:
    template = _ANOMALY_TEMPLATES.get(anomaly.anomaly_type)
    if not template:
        return None

    confidence = min(
        0.99,
        float(template["base_confidence"]) + _SEVERITY_CONFIDENCE_BOOST.get(anomaly.severity, 0.0),
    )

    rec = Recommendation(
        organization_id=anomaly.org_id,
        recommendation_key=f"proactive_{template['key']}",
        priority=template["priority"],
        confidence=confidence,
        detected_issue=template["issue"],
        proposal=template["proposal"],
        expected_impact=template["impact"],
        desired_functions={},
        request_text=template["request"],
        requires_confirmation=False,
        status="proposed",
        reasoning=(
            f"Auto-detectado por ProactiveMonitor. "
            f"Anomalía: {anomaly.anomaly_type} (severidad={anomaly.severity}). "
            f"Detalles: {anomaly.details}"
        ),
        recommendation_metadata={
            "source": "proactive_monitor",
            "anomaly_type": anomaly.anomaly_type,
            "severity": anomaly.severity,
            "anomaly_details": anomaly.details,
        },
    )
    session.add(rec)
    await session.commit()
    await session.refresh(rec)
    return rec


async def _dispatch_recommendation(rec: Recommendation) -> bool:
    try:
        result = await send_to_n8n_with_response(
            {
                "source": "proactive_monitor",
                "recommendation_id": str(rec.id),
                "recommendation_key": rec.recommendation_key,
                "request_text": rec.request_text,
                "confidence": rec.confidence,
                "anomaly_type": (rec.recommendation_metadata or {}).get("anomaly_type"),
                "auto_dispatched": True,
            }
        )
        execution_status = str(result.get("execution_status") or "")
        return bool(result.get("dispatched")) and execution_status not in {"error", "failed", "canceled", "crashed"}
    except Exception as exc:
        capture_exception(exc, {"source": "proactive_monitor.dispatch", "rec_id": str(rec.id)})
        return False
