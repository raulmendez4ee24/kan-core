from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import IdempotencyRecord


def build_step_hash(payload: Dict[str, Any]) -> str:
    encoded = json.dumps(payload or {}, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def build_idempotency_key(*, case_id: UUID, step_payload: Dict[str, Any]) -> str:
    return f"{case_id}:{build_step_hash(step_payload)}"


async def get_idempotent_result(
    session: AsyncSession,
    *,
    organization_id: UUID,
    idempotency_key: str,
) -> Optional[Dict[str, Any]]:
    stmt = (
        select(IdempotencyRecord)
        .where(
            IdempotencyRecord.organization_id == organization_id,
            IdempotencyRecord.idempotency_key == idempotency_key,
        )
        .limit(1)
    )
    row = (await session.execute(stmt)).scalars().first()
    return dict(row.result_payload or {}) if row else None


async def store_idempotent_result(
    session: AsyncSession,
    *,
    organization_id: UUID,
    case_id: UUID,
    idempotency_key: str,
    step_hash: str,
    result_payload: Dict[str, Any],
) -> IdempotencyRecord:
    stmt = (
        select(IdempotencyRecord)
        .where(
            IdempotencyRecord.organization_id == organization_id,
            IdempotencyRecord.idempotency_key == idempotency_key,
        )
        .limit(1)
    )
    row = (await session.execute(stmt)).scalars().first()
    if row:
        row.result_payload = dict(result_payload or {})
        await session.commit()
        await session.refresh(row)
        return row
    row = IdempotencyRecord(
        organization_id=organization_id,
        case_id=case_id,
        idempotency_key=idempotency_key,
        step_hash=step_hash,
        result_payload=dict(result_payload or {}),
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row
