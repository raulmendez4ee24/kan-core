from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import ProcessAuditFinding, ProcessAuditJob


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _analyze_text(text: str) -> Tuple[str, List[Dict[str, Any]], float, Dict[str, Any]]:
    token = str(text or "").lower()
    findings: List[Dict[str, Any]] = []
    score = 0.0

    def _push(kind: str, severity: str, description: str, impact: float, recommendation: str) -> None:
        nonlocal score
        findings.append(
            {
                "finding_type": kind,
                "severity": severity,
                "description": description,
                "estimated_impact": impact,
                "recommendation": recommendation,
            }
        )
        score += max(0.0, impact)

    if any(k in token for k in ["manual", "copiar", "pegar", "excel"]):
        _push(
            "repetitive_task",
            "high",
            "Se detectan tareas manuales repetitivas en el flujo.",
            0.35,
            "Automatizar con Task Studio + desktop/browser agent y validación automática.",
        )
    if any(k in token for k in ["espera", "retraso", "demora", "pendiente"]):
        _push(
            "bottleneck",
            "medium",
            "Hay señales de cuellos de botella por tiempos de espera.",
            0.25,
            "Agregar SLA automático, alertas y auto-routing de tareas por prioridad.",
        )
    if any(k in token for k in ["error", "falla", "caida", "caído"]):
        _push(
            "reliability",
            "high",
            "Se detecta riesgo operativo por fallas recurrentes.",
            0.30,
            "Activar auto-recovery avanzado + circuit breaker por proceso.",
        )
    if any(k in token for k in ["costo", "caro", "gasto"]):
        _push(
            "cost_optimization",
            "medium",
            "Hay oportunidades de optimización de costos en operación.",
            0.20,
            "Aplicar optimizador de herramienta/modelo por costo-resultado.",
        )
    if not findings:
        _push(
            "generic_optimization",
            "low",
            "No se detectaron patrones fuertes; se sugiere auditoría de evidencia adicional.",
            0.1,
            "Recolectar 1-2 semanas de logs operativos y repetir auditoría.",
        )

    roi = round(min(score, 1.5), 4)
    plan = {
        "phases": [
            {"name": "diagnose", "duration_days": 3},
            {"name": "automate", "duration_days": 7},
            {"name": "optimize", "duration_days": 7},
        ],
        "priority_findings": findings[:3],
    }
    summary = f"Se detectaron {len(findings)} oportunidades de mejora con ROI estimado {roi:.2f}."
    return summary, findings, roi, plan


async def create_audit_job(
    session: AsyncSession,
    *,
    organization_id: UUID,
    title: str,
    input_text: str,
    documents: List[Dict[str, Any]],
    metadata: Dict[str, Any],
) -> ProcessAuditJob:
    summary, findings, roi, plan = _analyze_text(input_text)
    job = ProcessAuditJob(
        organization_id=organization_id,
        title=str(title).strip(),
        input_text=str(input_text),
        documents={"items": documents, **(metadata or {})},
        status="completed",
        findings_summary=summary,
        estimated_roi=roi,
        implementation_plan=plan,
    )
    session.add(job)
    await session.commit()
    await session.refresh(job)

    for item in findings:
        row = ProcessAuditFinding(
            organization_id=organization_id,
            job_id=job.id,
            finding_type=str(item["finding_type"]),
            severity=str(item["severity"]),
            description=str(item["description"]),
            estimated_impact=float(item["estimated_impact"]),
            recommendation=str(item["recommendation"]),
        )
        session.add(row)
    await session.commit()
    return job


async def get_audit_job(
    session: AsyncSession,
    *,
    organization_id: UUID,
    job_id: UUID,
) -> Tuple[ProcessAuditJob | None, List[ProcessAuditFinding]]:
    job = await session.get(ProcessAuditJob, job_id)
    if not job or job.organization_id != organization_id:
        return None, []
    findings = list(
        (
            await session.execute(
                select(ProcessAuditFinding).where(
                    ProcessAuditFinding.organization_id == organization_id,
                    ProcessAuditFinding.job_id == job_id,
                )
            )
        )
        .scalars()
        .all()
    )
    return job, findings


async def estimate_job_roi(
    session: AsyncSession,
    *,
    organization_id: UUID,
    job_id: UUID,
) -> Dict[str, Any]:
    job, findings = await get_audit_job(
        session,
        organization_id=organization_id,
        job_id=job_id,
    )
    if not job:
        raise ValueError("Job not found")
    base_roi = float(job.estimated_roi or 0.0)
    annual_savings = round(12000.0 * base_roi, 2)
    impl_cost = round(2500.0 + (len(findings) * 300.0), 2)
    return {
        "job_id": str(job.id),
        "estimated_roi": base_roi,
        "annual_savings_estimate": annual_savings,
        "implementation_cost_estimate": impl_cost,
        "assumptions": {
            "baseline_opex_usd_year": 12000.0,
            "finding_count": len(findings),
            "generated_at": _now().isoformat(),
        },
    }

