from datetime import datetime, timezone
from typing import Any, Dict, List
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from models import AnalyticsInsight, AnalyticsUpload


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except Exception:
        return 0.0


def _compute_metrics(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    count = len(rows)
    numeric_keys: Dict[str, List[float]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        for k, v in row.items():
            fv = _safe_float(v)
            if isinstance(v, (int, float, str)) and not (isinstance(v, str) and not v.strip()):
                numeric_keys.setdefault(str(k), []).append(fv)

    metrics: Dict[str, Any] = {"rows": count}
    for key, values in numeric_keys.items():
        if not values:
            continue
        metrics[f"{key}_sum"] = round(sum(values), 4)
        metrics[f"{key}_avg"] = round(sum(values) / len(values), 4)
        metrics[f"{key}_max"] = round(max(values), 4)
        metrics[f"{key}_min"] = round(min(values), 4)
    return metrics


def _recommendations(metrics: Dict[str, Any], objective: str | None) -> Dict[str, Any]:
    recs: List[str] = []
    rows = int(metrics.get("rows") or 0)
    if rows == 0:
        recs.append("No hay datos suficientes; carga más filas para detectar patrones.")
    if objective:
        recs.append(f"Optimizar objetivo declarado: {objective}")
    high_variance_keys = [k for k in metrics.keys() if k.endswith("_max")]
    if high_variance_keys:
        recs.append("Segmentar por top performers y replicar patrones de los mejores casos.")
    recs.append("Programar insight diario automático y alertas de desviación.")
    return {"items": recs}


async def upload_dataset(
    session: AsyncSession,
    *,
    organization_id: UUID,
    file_name: str,
    mime_type: str,
    rows: List[Dict[str, Any]],
    metadata: Dict[str, Any],
) -> AnalyticsUpload:
    payload = {"rows": rows, **(metadata or {})}
    row = AnalyticsUpload(
        organization_id=organization_id,
        file_name=file_name,
        mime_type=mime_type or "text/csv",
        rows_count=len(rows),
        payload=payload,
        status="uploaded",
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def run_insight_generation(
    session: AsyncSession,
    *,
    organization_id: UUID,
    upload_id: UUID | None,
    objective: str | None,
    metadata: Dict[str, Any],
) -> AnalyticsInsight:
    rows: List[Dict[str, Any]] = []
    ref_upload_id: UUID | None = None
    if upload_id:
        upload = await session.get(AnalyticsUpload, upload_id)
        if not upload or upload.organization_id != organization_id:
            raise ValueError("Upload not found")
        ref_upload_id = upload.id
        rows = list((upload.payload or {}).get("rows") or [])
    metrics = _compute_metrics(rows)
    recs = _recommendations(metrics, objective)
    summary = (
        f"Analicé {len(rows)} filas y detecté {len([k for k in metrics.keys() if k.endswith('_avg')])} "
        "métricas numéricas útiles para decisión."
    )
    insight = AnalyticsInsight(
        organization_id=organization_id,
        upload_id=ref_upload_id,
        status="ready",
        summary=summary,
        metrics=metrics,
        recommendations={"objective": objective, **recs, **(metadata or {})},
    )
    session.add(insight)
    await session.commit()
    await session.refresh(insight)
    return insight


async def get_insight(
    session: AsyncSession,
    *,
    organization_id: UUID,
    insight_id: UUID,
) -> AnalyticsInsight | None:
    row = await session.get(AnalyticsInsight, insight_id)
    if not row or row.organization_id != organization_id:
        return None
    return row

