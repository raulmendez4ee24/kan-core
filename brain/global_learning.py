from datetime import datetime, timezone
from typing import Dict, List

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import GlobalLearning, IntegrationMemory


async def refresh_global_learnings(
    session: AsyncSession,
    *,
    min_samples: int = 3,
) -> List[GlobalLearning]:
    stmt = select(IntegrationMemory)
    rows = list((await session.execute(stmt)).scalars().all())

    grouped: Dict[tuple[str, str, str], Dict[str, float]] = {}
    for row in rows:
        key = (
            str(row.industry or "general"),
            str(row.function_name or "unknown"),
            str(row.provider_id or "unknown"),
        )
        bucket = grouped.setdefault(key, {"success": 0.0, "failure": 0.0})
        bucket["success"] += float(row.success_count or 0)
        bucket["failure"] += float(row.failure_count or 0)

    created: List[GlobalLearning] = []
    now = datetime.now(timezone.utc)
    for (industry, function_name, provider_id), bucket in grouped.items():
        sample_size = int(bucket["success"] + bucket["failure"])
        if sample_size < min_samples:
            continue
        success_rate = (bucket["success"] / sample_size) if sample_size else 0.0
        impact_score = max(min(success_rate * 100.0, 100.0), 0.0)

        pattern_key = f"{industry}:{function_name}:{provider_id}"
        existing_stmt = select(GlobalLearning).where(GlobalLearning.pattern_key == pattern_key)
        existing = (await session.execute(existing_stmt)).scalars().first()
        if existing:
            existing.sample_size = sample_size
            existing.success_rate = success_rate
            existing.avg_impact_score = impact_score
            existing.learning_metadata = {
                "updated_at": now.isoformat(),
                "source": "integration_memory",
            }
            created.append(existing)
            continue

        record = GlobalLearning(
            pattern_key=pattern_key,
            industry=industry,
            function_name=function_name,
            provider_id=provider_id,
            sample_size=sample_size,
            success_rate=success_rate,
            avg_impact_score=impact_score,
            learning_metadata={
                "created_at": now.isoformat(),
                "source": "integration_memory",
            },
        )
        session.add(record)
        created.append(record)

    await session.commit()
    for item in created:
        await session.refresh(item)
    return created


async def load_learning_bias(
    session: AsyncSession,
    *,
    industry: str,
    function_name: str,
) -> Dict[str, float]:
    stmt = select(GlobalLearning).where(
        GlobalLearning.industry.in_([industry, "general"]),
        GlobalLearning.function_name == function_name,
    )
    rows = list((await session.execute(stmt)).scalars().all())

    bias: Dict[str, float] = {}
    for row in rows:
        if not row.provider_id:
            continue
        delta = float(row.success_rate or 0.0) * 10.0 - 3.0
        bias[str(row.provider_id)] = round(delta, 3)
    return bias
