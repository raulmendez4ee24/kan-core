from __future__ import annotations

import hashlib
import json
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import aiosqlite
from pydantic import BaseModel, ConfigDict, Field

from tools.ycloud_client import send_ycloud_whatsapp_text_message


_STATUSES = (
    "DETECTED",
    "EVALUATED",
    "VALIDATED",
    "PROSPECTED",
    "ENGAGED",
    "CONVERTED",
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _db_path() -> Path:
    configured = str(os.getenv("OPPORTUNITY_PIPELINE_DB_PATH") or "").strip()
    if configured:
        path = Path(configured)
    else:
        path = Path(__file__).resolve().parents[1] / "data" / "opportunity_pipeline.sqlite3"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


@asynccontextmanager
async def _connect():
    async with aiosqlite.connect(str(_db_path())) as conn:
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA foreign_keys = ON")
        await conn.execute("PRAGMA journal_mode = WAL")
        await _ensure_schema(conn)
        yield conn


async def _ensure_schema(conn: aiosqlite.Connection) -> None:
    await conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS opportunities (
            id TEXT PRIMARY KEY,
            domain TEXT NOT NULL,
            business_name TEXT NOT NULL,
            vertical TEXT,
            source TEXT,
            score REAL,
            status TEXT NOT NULL,
            payload_json TEXT,
            approval_requested INTEGER DEFAULT 0,
            approved_automatically INTEGER DEFAULT 0,
            created_at TIMESTAMP NOT NULL,
            updated_at TIMESTAMP NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_opportunities_status
            ON opportunities(status, updated_at DESC);
        CREATE INDEX IF NOT EXISTS idx_opportunities_domain
            ON opportunities(domain, updated_at DESC);

        CREATE TABLE IF NOT EXISTS opportunity_transitions (
            id TEXT PRIMARY KEY,
            opportunity_id TEXT NOT NULL,
            from_status TEXT,
            to_status TEXT NOT NULL,
            reason TEXT,
            created_at TIMESTAMP NOT NULL,
            FOREIGN KEY (opportunity_id) REFERENCES opportunities(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_opportunity_transitions_opportunity
            ON opportunity_transitions(opportunity_id, created_at DESC);
        """
    )
    await conn.commit()


def _stable_id(prefix: str, *parts: Any) -> str:
    digest = hashlib.sha256(
        "|".join(str(part or "").strip() for part in parts).encode("utf-8")
    ).hexdigest()[:24]
    return f"{prefix}_{digest}"


def _json_blob(payload: Optional[dict[str, Any]]) -> Optional[str]:
    if not payload:
        return None
    return json.dumps(payload, ensure_ascii=True, sort_keys=True)


def _loads_payload(raw: Optional[str]) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        loaded = json.loads(raw)
    except Exception:
        return {}
    return loaded if isinstance(loaded, dict) else {}


class OpportunityRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    domain: str
    business_name: str
    vertical: Optional[str] = None
    source: Optional[str] = None
    score: Optional[float] = None
    status: str
    payload: dict[str, Any] = Field(default_factory=dict)
    approval_requested: bool = False
    approved_automatically: bool = False
    created_at: str
    updated_at: str


class OpportunityTransition(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    opportunity_id: str
    from_status: Optional[str] = None
    to_status: str
    reason: Optional[str] = None
    created_at: str


class ApprovalResult(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    opportunity_id: str
    score: float
    status: str
    auto_validated: bool = False
    approval_requested: bool = False
    whatsapp_sent: bool = False


def _normalize_status(status: str) -> str:
    token = str(status or "").strip().upper()
    if token not in _STATUSES:
        raise ValueError(f"Unsupported opportunity status: {status}")
    return token


def _row_to_opportunity(row: aiosqlite.Row) -> OpportunityRecord:
    return OpportunityRecord(
        id=row["id"],
        domain=row["domain"],
        business_name=row["business_name"],
        vertical=row["vertical"],
        source=row["source"],
        score=row["score"],
        status=row["status"],
        payload=_loads_payload(row["payload_json"]),
        approval_requested=bool(row["approval_requested"]),
        approved_automatically=bool(row["approved_automatically"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_transition(row: aiosqlite.Row) -> OpportunityTransition:
    return OpportunityTransition(
        id=row["id"],
        opportunity_id=row["opportunity_id"],
        from_status=row["from_status"],
        to_status=row["to_status"],
        reason=row["reason"],
        created_at=row["created_at"],
    )


async def _fetchone(
    conn: aiosqlite.Connection,
    query: str,
    params: tuple[Any, ...] = (),
) -> Optional[aiosqlite.Row]:
    cursor = await conn.execute(query, params)
    try:
        return await cursor.fetchone()
    finally:
        await cursor.close()


async def _fetchall(
    conn: aiosqlite.Connection,
    query: str,
    params: tuple[Any, ...] = (),
) -> list[aiosqlite.Row]:
    cursor = await conn.execute(query, params)
    try:
        rows = await cursor.fetchall()
        return list(rows)
    finally:
        await cursor.close()


async def create_opportunity(
    *,
    domain: str,
    business_name: str,
    vertical: Optional[str] = None,
    source: Optional[str] = None,
    score: Optional[float] = None,
    payload: Optional[dict[str, Any]] = None,
    opportunity_id: Optional[str] = None,
) -> OpportunityRecord:
    now = _now_iso()
    token = opportunity_id or _stable_id("opp", domain, business_name, vertical or "", source or "")
    record_payload = dict(payload or {})
    async with _connect() as conn:
        await conn.execute(
            """
            INSERT INTO opportunities (
                id, domain, business_name, vertical, source, score, status,
                payload_json, approval_requested, approved_automatically,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                domain=excluded.domain,
                business_name=excluded.business_name,
                vertical=excluded.vertical,
                source=excluded.source,
                score=COALESCE(excluded.score, opportunities.score),
                payload_json=COALESCE(excluded.payload_json, opportunities.payload_json),
                updated_at=excluded.updated_at
            """,
            (
                token,
                domain,
                business_name,
                vertical,
                source,
                score,
                "DETECTED",
                _json_blob(record_payload),
                now,
                now,
            ),
        )
        existing = await _fetchone(
            conn,
            "SELECT 1 FROM opportunity_transitions WHERE opportunity_id = ? AND to_status = 'DETECTED' LIMIT 1",
            (token,),
        )
        if existing is None:
            await conn.execute(
                """
                INSERT INTO opportunity_transitions (
                    id, opportunity_id, from_status, to_status, reason, created_at
                ) VALUES (?, ?, NULL, 'DETECTED', ?, ?)
                """,
                (_stable_id("opp_tr", token, "DETECTED", now), token, "created", now),
            )
        await conn.commit()
        row = await _fetchone(conn, "SELECT * FROM opportunities WHERE id = ?", (token,))
    if row is None:
        raise RuntimeError("Failed to create opportunity")
    return _row_to_opportunity(row)


async def transition_opportunity(
    opportunity_id: str,
    to_status: str,
    *,
    reason: Optional[str] = None,
    score: Optional[float] = None,
    payload_patch: Optional[dict[str, Any]] = None,
    approval_requested: Optional[bool] = None,
    approved_automatically: Optional[bool] = None,
) -> OpportunityRecord:
    target_status = _normalize_status(to_status)
    now = _now_iso()
    async with _connect() as conn:
        row = await _fetchone(conn, "SELECT * FROM opportunities WHERE id = ?", (opportunity_id,))
        if row is None:
            raise ValueError(f"Unknown opportunity_id: {opportunity_id}")
        current = _row_to_opportunity(row)
        merged_payload = dict(current.payload)
        if payload_patch:
            merged_payload.update(payload_patch)
        next_score = current.score if score is None else score
        next_approval_requested = current.approval_requested if approval_requested is None else approval_requested
        next_approved_automatically = (
            current.approved_automatically if approved_automatically is None else approved_automatically
        )
        await conn.execute(
            """
            UPDATE opportunities
            SET status = ?,
                score = ?,
                payload_json = ?,
                approval_requested = ?,
                approved_automatically = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                target_status,
                next_score,
                _json_blob(merged_payload),
                1 if next_approval_requested else 0,
                1 if next_approved_automatically else 0,
                now,
                opportunity_id,
            ),
        )
        if current.status != target_status:
            await conn.execute(
                """
                INSERT INTO opportunity_transitions (
                    id, opportunity_id, from_status, to_status, reason, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    _stable_id("opp_tr", opportunity_id, current.status, target_status, now),
                    opportunity_id,
                    current.status,
                    target_status,
                    reason,
                    now,
                ),
            )
        await conn.commit()
        updated = await _fetchone(conn, "SELECT * FROM opportunities WHERE id = ?", (opportunity_id,))
    if updated is None:
        raise RuntimeError("Failed to update opportunity")
    return _row_to_opportunity(updated)


async def get_opportunity(opportunity_id: str) -> Optional[OpportunityRecord]:
    async with _connect() as conn:
        row = await _fetchone(conn, "SELECT * FROM opportunities WHERE id = ?", (opportunity_id,))
    if row is None:
        return None
    return _row_to_opportunity(row)


async def get_pipeline_by_status() -> dict[str, list[OpportunityRecord]]:
    pipeline = {status: [] for status in _STATUSES}
    async with _connect() as conn:
        rows = await _fetchall(conn, "SELECT * FROM opportunities ORDER BY updated_at DESC")
    for row in rows:
        record = _row_to_opportunity(row)
        pipeline.setdefault(record.status, []).append(record)
    return pipeline


async def get_opportunity_transitions(opportunity_id: str) -> list[OpportunityTransition]:
    async with _connect() as conn:
        rows = await _fetchall(
            conn,
            """
            SELECT * FROM opportunity_transitions
            WHERE opportunity_id = ?
            ORDER BY created_at ASC
            """,
            (opportunity_id,),
        )
    return [_row_to_transition(row) for row in rows]


async def auto_approve(
    opportunity_id: str,
    *,
    score: float,
    client_id: Optional[str] = None,
) -> ApprovalResult:
    record = await get_opportunity(opportunity_id)
    if record is None:
        raise ValueError(f"Unknown opportunity_id: {opportunity_id}")

    if score > 85:
        updated = await transition_opportunity(
            opportunity_id,
            "VALIDATED",
            reason="auto_approved_high_score",
            score=score,
            approved_automatically=True,
            approval_requested=False,
        )
        return ApprovalResult(
            opportunity_id=opportunity_id,
            score=score,
            status=updated.status,
            auto_validated=True,
            approval_requested=False,
            whatsapp_sent=False,
        )

    if 70 <= score <= 85:
        updated = await transition_opportunity(
            opportunity_id,
            "EVALUATED",
            reason="approval_requested_mid_score",
            score=score,
            approval_requested=True,
            approved_automatically=False,
        )
        to_number = str(os.getenv("RAUL_WHATSAPP_TO") or "").strip()
        from_number = str(os.getenv("YCLOUD_WHATSAPP_FROM") or "").strip()
        whatsapp_sent = False
        if to_number and from_number:
            text = (
                f"Raul, tengo una oportunidad para aprobar.\n"
                f"Negocio: {updated.business_name}\n"
                f"Vertical: {updated.vertical or updated.domain}\n"
                f"Score: {score:.2f}\n"
                f"Estado: {updated.status}\n"
                f"ID: {updated.id}"
            )
            response = await send_ycloud_whatsapp_text_message(
                from_number=from_number,
                to=to_number,
                text=text,
            )
            whatsapp_sent = response is not None
        return ApprovalResult(
            opportunity_id=opportunity_id,
            score=score,
            status=updated.status,
            auto_validated=False,
            approval_requested=True,
            whatsapp_sent=whatsapp_sent,
        )

    updated = await transition_opportunity(
        opportunity_id,
        "EVALUATED",
        reason="score_below_auto_approval_threshold",
        score=score,
        approval_requested=False,
        approved_automatically=False,
    )
    return ApprovalResult(
        opportunity_id=opportunity_id,
        score=score,
        status=updated.status,
        auto_validated=False,
        approval_requested=False,
        whatsapp_sent=False,
    )
