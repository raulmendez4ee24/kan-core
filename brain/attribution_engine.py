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


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _db_path() -> Path:
    configured = str(os.getenv("ATTRIBUTION_ENGINE_DB_PATH") or "").strip()
    if configured:
        path = Path(configured)
    else:
        path = Path(__file__).resolve().parents[1] / "data" / "attribution_engine.sqlite3"
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
        CREATE TABLE IF NOT EXISTS lead_sources (
            lead_id TEXT PRIMARY KEY,
            source_key TEXT,
            channel TEXT NOT NULL,
            campaign_id TEXT,
            utm_campaign TEXT,
            source_campaign TEXT,
            ad_name TEXT,
            spend REAL DEFAULT 0,
            metadata_json TEXT,
            created_at TIMESTAMP NOT NULL,
            updated_at TIMESTAMP NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_lead_sources_source_key
            ON lead_sources(source_key);
        CREATE INDEX IF NOT EXISTS idx_lead_sources_channel
            ON lead_sources(channel);

        CREATE TABLE IF NOT EXISTS conversations (
            id TEXT PRIMARY KEY,
            lead_id TEXT NOT NULL,
            source_key TEXT,
            channel TEXT NOT NULL,
            meaningful_reply INTEGER DEFAULT 0,
            metadata_json TEXT,
            created_at TIMESTAMP NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_conversations_source_key
            ON conversations(source_key);
        CREATE INDEX IF NOT EXISTS idx_conversations_lead
            ON conversations(lead_id);

        CREATE TABLE IF NOT EXISTS bookings (
            id TEXT PRIMARY KEY,
            lead_id TEXT NOT NULL,
            source_key TEXT,
            scheduled_at TIMESTAMP NOT NULL,
            confirmed INTEGER DEFAULT 1,
            metadata_json TEXT,
            created_at TIMESTAMP NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_bookings_source_key
            ON bookings(source_key);
        CREATE INDEX IF NOT EXISTS idx_bookings_lead
            ON bookings(lead_id);

        CREATE TABLE IF NOT EXISTS closes (
            id TEXT PRIMARY KEY,
            lead_id TEXT NOT NULL,
            source_key TEXT,
            revenue REAL DEFAULT 0,
            currency TEXT DEFAULT 'USD',
            status TEXT DEFAULT 'won',
            metadata_json TEXT,
            created_at TIMESTAMP NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_closes_source_key
            ON closes(source_key);
        CREATE INDEX IF NOT EXISTS idx_closes_lead
            ON closes(lead_id);

        CREATE TABLE IF NOT EXISTS briefings (
            id TEXT PRIMARY KEY,
            type TEXT NOT NULL,
            created_at TIMESTAMP NOT NULL,
            content_json TEXT NOT NULL,
            actions_executed INTEGER DEFAULT 0,
            actions_recommended INTEGER DEFAULT 0
        );

        CREATE INDEX IF NOT EXISTS idx_briefings_type_created_at
            ON briefings(type, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_briefings_created_at
            ON briefings(created_at DESC);
        """
    )
    await conn.commit()


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


def _normalize_key(value: Optional[str]) -> Optional[str]:
    cleaned = str(value or "").strip()
    return cleaned or None


def _resolve_source_key(
    *,
    campaign_id: Optional[str] = None,
    utm_campaign: Optional[str] = None,
    source_campaign: Optional[str] = None,
    ad_name: Optional[str] = None,
) -> Optional[str]:
    return (
        _normalize_key(campaign_id)
        or _normalize_key(utm_campaign)
        or _normalize_key(source_campaign)
        or _normalize_key(ad_name)
    )


def _stable_id(prefix: str, *parts: Any) -> str:
    digest = hashlib.sha256(
        "|".join(str(part or "").strip() for part in parts).encode("utf-8")
    ).hexdigest()[:24]
    return f"{prefix}_{digest}"


def _json_blob(payload: Optional[dict[str, Any]]) -> Optional[str]:
    if not payload:
        return None
    return json.dumps(payload, ensure_ascii=True, sort_keys=True)


class LeadSourceRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    lead_id: str
    source_key: Optional[str] = None
    channel: str
    campaign_id: Optional[str] = None
    utm_campaign: Optional[str] = None
    source_campaign: Optional[str] = None
    ad_name: Optional[str] = None
    spend: float = 0.0
    metadata: Optional[dict[str, Any]] = None
    created_at: str
    updated_at: str


class ConversationRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    lead_id: str
    source_key: Optional[str] = None
    channel: str
    meaningful_reply: bool = False
    metadata: Optional[dict[str, Any]] = None
    created_at: str


class BookingRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    lead_id: str
    source_key: Optional[str] = None
    scheduled_at: str
    confirmed: bool = True
    metadata: Optional[dict[str, Any]] = None
    created_at: str


class CloseRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    lead_id: str
    source_key: Optional[str] = None
    revenue: float = 0.0
    currency: str = "USD"
    status: str = "won"
    metadata: Optional[dict[str, Any]] = None
    created_at: str


class FunnelMetrics(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    source_key: str
    leads: int = 0
    conversations: int = 0
    replies: int = 0
    bookings: int = 0
    closes: int = 0
    revenue: float = 0.0
    spend: float = 0.0
    conversation_rate: Optional[float] = None
    reply_rate: Optional[float] = None
    booking_rate: Optional[float] = None
    close_rate: Optional[float] = None
    roas: Optional[float] = None
    roi: Optional[float] = None


class RoiSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    key: str
    leads: int = 0
    conversations: int = 0
    replies: int = 0
    bookings: int = 0
    closes: int = 0
    revenue: float = 0.0
    spend: float = 0.0
    booking_rate: Optional[float] = None
    close_rate: Optional[float] = None
    roas: Optional[float] = None
    roi: Optional[float] = None


class BriefingRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    type: str
    created_at: str
    content: dict[str, Any] = Field(default_factory=dict)
    actions_executed: int = 0
    actions_recommended: int = 0


def _row_json(row: aiosqlite.Row, field: str) -> Optional[dict[str, Any]]:
    raw = row[field]
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


async def _find_source_key_for_lead(conn: aiosqlite.Connection, lead_id: str) -> Optional[str]:
    row = await _fetchone(
        conn,
        "SELECT source_key FROM lead_sources WHERE lead_id = ?",
        (lead_id,),
    )
    if not row:
        return None
    return _normalize_key(row["source_key"])


def _metrics_from_counts(
    *,
    source_key: str,
    leads: int,
    conversations: int,
    replies: int,
    bookings: int,
    closes: int,
    revenue: float,
    spend: float,
) -> FunnelMetrics:
    conversation_rate = (conversations / leads) if leads else None
    reply_rate = (replies / conversations) if conversations else None
    booking_rate = (bookings / conversations) if conversations else None
    close_rate = (closes / bookings) if bookings else None
    roas = (revenue / spend) if spend else None
    roi = ((revenue - spend) / spend) if spend else None
    return FunnelMetrics(
        source_key=source_key,
        leads=leads,
        conversations=conversations,
        replies=replies,
        bookings=bookings,
        closes=closes,
        revenue=float(revenue or 0.0),
        spend=float(spend or 0.0),
        conversation_rate=conversation_rate,
        reply_rate=reply_rate,
        booking_rate=booking_rate,
        close_rate=close_rate,
        roas=roas,
        roi=roi,
    )


async def track_lead_source(
    *,
    lead_id: str,
    channel: str,
    campaign_id: Optional[str] = None,
    utm_campaign: Optional[str] = None,
    source_campaign: Optional[str] = None,
    ad_name: Optional[str] = None,
    spend: float = 0.0,
    metadata: Optional[dict[str, Any]] = None,
) -> LeadSourceRecord:
    lead = _normalize_key(lead_id)
    if not lead:
        raise ValueError("lead_id is required")
    now = _now_iso()
    source_key = _resolve_source_key(
        campaign_id=campaign_id,
        utm_campaign=utm_campaign,
        source_campaign=source_campaign,
        ad_name=ad_name,
    )
    record = LeadSourceRecord(
        lead_id=lead,
        source_key=source_key,
        channel=str(channel or "unknown").strip() or "unknown",
        campaign_id=_normalize_key(campaign_id),
        utm_campaign=_normalize_key(utm_campaign),
        source_campaign=_normalize_key(source_campaign),
        ad_name=_normalize_key(ad_name),
        spend=max(0.0, float(spend or 0.0)),
        metadata=metadata or None,
        created_at=now,
        updated_at=now,
    )
    async with _connect() as conn:
        existing = await _fetchone(
            conn,
            "SELECT created_at FROM lead_sources WHERE lead_id = ?",
            (lead,),
        )
        created_at = str(existing["created_at"]) if existing else now
        await conn.execute(
            """
            INSERT INTO lead_sources (
                lead_id, source_key, channel, campaign_id, utm_campaign,
                source_campaign, ad_name, spend, metadata_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(lead_id) DO UPDATE SET
                source_key = COALESCE(excluded.source_key, lead_sources.source_key),
                channel = excluded.channel,
                campaign_id = COALESCE(excluded.campaign_id, lead_sources.campaign_id),
                utm_campaign = COALESCE(excluded.utm_campaign, lead_sources.utm_campaign),
                source_campaign = COALESCE(excluded.source_campaign, lead_sources.source_campaign),
                ad_name = COALESCE(excluded.ad_name, lead_sources.ad_name),
                spend = CASE
                    WHEN excluded.spend > 0 THEN excluded.spend
                    ELSE lead_sources.spend
                END,
                metadata_json = COALESCE(excluded.metadata_json, lead_sources.metadata_json),
                updated_at = excluded.updated_at
            """,
            (
                record.lead_id,
                record.source_key,
                record.channel,
                record.campaign_id,
                record.utm_campaign,
                record.source_campaign,
                record.ad_name,
                record.spend,
                _json_blob(record.metadata),
                created_at,
                now,
            ),
        )
        await conn.commit()
    return record.model_copy(update={"created_at": created_at})


async def track_conversation(
    *,
    lead_id: str,
    channel: str,
    conversation_id: Optional[str] = None,
    campaign_id: Optional[str] = None,
    utm_campaign: Optional[str] = None,
    source_campaign: Optional[str] = None,
    ad_name: Optional[str] = None,
    meaningful_reply: bool = False,
    metadata: Optional[dict[str, Any]] = None,
) -> ConversationRecord:
    lead = _normalize_key(lead_id)
    if not lead:
        raise ValueError("lead_id is required")
    now = _now_iso()
    cid = _normalize_key(conversation_id) or _stable_id(
        "conv",
        lead,
        channel,
        campaign_id,
        utm_campaign,
        source_campaign,
        ad_name,
        now,
    )
    async with _connect() as conn:
        source_key = _resolve_source_key(
            campaign_id=campaign_id,
            utm_campaign=utm_campaign,
            source_campaign=source_campaign,
            ad_name=ad_name,
        )
        if not source_key:
            source_key = await _find_source_key_for_lead(conn, lead)
        await conn.execute(
            """
            INSERT OR IGNORE INTO conversations (
                id, lead_id, source_key, channel, meaningful_reply, metadata_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                cid,
                lead,
                source_key,
                str(channel or "unknown").strip() or "unknown",
                1 if meaningful_reply else 0,
                _json_blob(metadata),
                now,
            ),
        )
        if meaningful_reply:
            await conn.execute(
                """
                UPDATE conversations
                SET meaningful_reply = 1,
                    source_key = COALESCE(?, source_key),
                    metadata_json = COALESCE(?, metadata_json)
                WHERE id = ?
                """,
                (source_key, _json_blob(metadata), cid),
            )
        await conn.commit()
        row = await _fetchone(
            conn,
            "SELECT * FROM conversations WHERE id = ?",
            (cid,),
        )
    return ConversationRecord(
        id=str(row["id"]),
        lead_id=str(row["lead_id"]),
        source_key=_normalize_key(row["source_key"]),
        channel=str(row["channel"]),
        meaningful_reply=bool(row["meaningful_reply"]),
        metadata=_row_json(row, "metadata_json"),
        created_at=str(row["created_at"]),
    )


async def track_booking(
    *,
    lead_id: str,
    scheduled_at: datetime | str,
    booking_id: Optional[str] = None,
    campaign_id: Optional[str] = None,
    utm_campaign: Optional[str] = None,
    source_campaign: Optional[str] = None,
    ad_name: Optional[str] = None,
    confirmed: bool = True,
    metadata: Optional[dict[str, Any]] = None,
) -> BookingRecord:
    lead = _normalize_key(lead_id)
    if not lead:
        raise ValueError("lead_id is required")
    scheduled_str = (
        scheduled_at.isoformat()
        if isinstance(scheduled_at, datetime)
        else str(scheduled_at or "").strip()
    )
    if not scheduled_str:
        raise ValueError("scheduled_at is required")
    now = _now_iso()
    bid = _normalize_key(booking_id) or _stable_id("book", lead, scheduled_str)
    async with _connect() as conn:
        source_key = _resolve_source_key(
            campaign_id=campaign_id,
            utm_campaign=utm_campaign,
            source_campaign=source_campaign,
            ad_name=ad_name,
        )
        if not source_key:
            source_key = await _find_source_key_for_lead(conn, lead)
        await conn.execute(
            """
            INSERT OR IGNORE INTO bookings (
                id, lead_id, source_key, scheduled_at, confirmed, metadata_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                bid,
                lead,
                source_key,
                scheduled_str,
                1 if confirmed else 0,
                _json_blob(metadata),
                now,
            ),
        )
        if confirmed:
            await conn.execute(
                """
                UPDATE bookings
                SET confirmed = 1,
                    source_key = COALESCE(?, source_key),
                    metadata_json = COALESCE(?, metadata_json)
                WHERE id = ?
                """,
                (source_key, _json_blob(metadata), bid),
            )
        await conn.commit()
        row = await _fetchone(
            conn,
            "SELECT * FROM bookings WHERE id = ?",
            (bid,),
        )
    return BookingRecord(
        id=str(row["id"]),
        lead_id=str(row["lead_id"]),
        source_key=_normalize_key(row["source_key"]),
        scheduled_at=str(row["scheduled_at"]),
        confirmed=bool(row["confirmed"]),
        metadata=_row_json(row, "metadata_json"),
        created_at=str(row["created_at"]),
    )


async def track_close(
    *,
    lead_id: str,
    revenue: float,
    close_id: Optional[str] = None,
    campaign_id: Optional[str] = None,
    utm_campaign: Optional[str] = None,
    source_campaign: Optional[str] = None,
    ad_name: Optional[str] = None,
    currency: str = "USD",
    status: str = "won",
    metadata: Optional[dict[str, Any]] = None,
) -> CloseRecord:
    lead = _normalize_key(lead_id)
    if not lead:
        raise ValueError("lead_id is required")
    now = _now_iso()
    cid = _normalize_key(close_id) or _stable_id("close", lead, revenue, currency, status, now)
    async with _connect() as conn:
        source_key = _resolve_source_key(
            campaign_id=campaign_id,
            utm_campaign=utm_campaign,
            source_campaign=source_campaign,
            ad_name=ad_name,
        )
        if not source_key:
            source_key = await _find_source_key_for_lead(conn, lead)
        await conn.execute(
            """
            INSERT OR IGNORE INTO closes (
                id, lead_id, source_key, revenue, currency, status, metadata_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                cid,
                lead,
                source_key,
                max(0.0, float(revenue or 0.0)),
                str(currency or "USD").strip() or "USD",
                str(status or "won").strip() or "won",
                _json_blob(metadata),
                now,
            ),
        )
        await conn.commit()
        row = await _fetchone(
            conn,
            "SELECT * FROM closes WHERE id = ?",
            (cid,),
        )
    return CloseRecord(
        id=str(row["id"]),
        lead_id=str(row["lead_id"]),
        source_key=_normalize_key(row["source_key"]),
        revenue=float(row["revenue"] or 0.0),
        currency=str(row["currency"] or "USD"),
        status=str(row["status"] or "won"),
        metadata=_row_json(row, "metadata_json"),
        created_at=str(row["created_at"]),
    )


async def get_full_funnel(campaign_id: str) -> FunnelMetrics:
    source_key = _normalize_key(campaign_id)
    if not source_key:
        return _metrics_from_counts(
            source_key="",
            leads=0,
            conversations=0,
            replies=0,
            bookings=0,
            closes=0,
            revenue=0.0,
            spend=0.0,
        )
    async with _connect() as conn:
        lead_row = await _fetchone(
            conn,
            """
            SELECT COUNT(*) AS leads, COALESCE(SUM(spend), 0) AS spend
            FROM lead_sources
            WHERE source_key = ?
            """,
            (source_key,),
        )
        conv_row = await _fetchone(
            conn,
            """
            SELECT
                COUNT(*) AS conversations,
                COALESCE(SUM(CASE WHEN meaningful_reply = 1 THEN 1 ELSE 0 END), 0) AS replies
            FROM conversations
            WHERE source_key = ?
            """,
            (source_key,),
        )
        book_row = await _fetchone(
            conn,
            """
            SELECT COUNT(*) AS bookings
            FROM bookings
            WHERE source_key = ? AND confirmed = 1
            """,
            (source_key,),
        )
        close_row = await _fetchone(
            conn,
            """
            SELECT COUNT(*) AS closes, COALESCE(SUM(revenue), 0) AS revenue
            FROM closes
            WHERE source_key = ? AND status = 'won'
            """,
            (source_key,),
        )
    return _metrics_from_counts(
        source_key=source_key,
        leads=int(lead_row["leads"] or 0),
        conversations=int(conv_row["conversations"] or 0),
        replies=int(conv_row["replies"] or 0),
        bookings=int(book_row["bookings"] or 0),
        closes=int(close_row["closes"] or 0),
        revenue=float(close_row["revenue"] or 0.0),
        spend=float(lead_row["spend"] or 0.0),
    )


async def get_campaign_roi(campaign_id: str) -> RoiSummary:
    funnel = await get_full_funnel(campaign_id)
    return RoiSummary(
        key=funnel.source_key,
        leads=funnel.leads,
        conversations=funnel.conversations,
        replies=funnel.replies,
        bookings=funnel.bookings,
        closes=funnel.closes,
        revenue=funnel.revenue,
        spend=funnel.spend,
        booking_rate=funnel.booking_rate,
        close_rate=funnel.close_rate,
        roas=funnel.roas,
        roi=funnel.roi,
    )


async def get_channel_roi(channel: str) -> RoiSummary:
    safe_channel = str(channel or "").strip() or "unknown"
    async with _connect() as conn:
        source_rows = await _fetchall(
            conn,
            """
            SELECT DISTINCT source_key
            FROM lead_sources
            WHERE channel = ? AND source_key IS NOT NULL AND source_key != ''
            """,
            (safe_channel,),
        )
    if not source_rows:
        return RoiSummary(key=safe_channel)

    totals = {
        "leads": 0,
        "conversations": 0,
        "replies": 0,
        "bookings": 0,
        "closes": 0,
        "revenue": 0.0,
        "spend": 0.0,
    }
    for row in source_rows:
        funnel = await get_full_funnel(str(row["source_key"]))
        totals["leads"] += funnel.leads
        totals["conversations"] += funnel.conversations
        totals["replies"] += funnel.replies
        totals["bookings"] += funnel.bookings
        totals["closes"] += funnel.closes
        totals["revenue"] += funnel.revenue
        totals["spend"] += funnel.spend

    aggregate = _metrics_from_counts(
        source_key=safe_channel,
        leads=totals["leads"],
        conversations=totals["conversations"],
        replies=totals["replies"],
        bookings=totals["bookings"],
        closes=totals["closes"],
        revenue=totals["revenue"],
        spend=totals["spend"],
    )
    return RoiSummary(
        key=safe_channel,
        leads=aggregate.leads,
        conversations=aggregate.conversations,
        replies=aggregate.replies,
        bookings=aggregate.bookings,
        closes=aggregate.closes,
        revenue=aggregate.revenue,
        spend=aggregate.spend,
        booking_rate=aggregate.booking_rate,
        close_rate=aggregate.close_rate,
        roas=aggregate.roas,
        roi=aggregate.roi,
    )


async def save_briefing(
    *,
    briefing_type: str,
    content: dict[str, Any],
    actions_executed: int = 0,
    actions_recommended: int = 0,
    briefing_id: Optional[str] = None,
    created_at: Optional[str] = None,
) -> BriefingRecord:
    normalized_type = str(briefing_type or "").strip().lower()
    if normalized_type not in {"daily", "evening"}:
        raise ValueError("briefing_type must be 'daily' or 'evening'")
    payload = dict(content or {})
    created = str(created_at or payload.get("generated_at") or _now_iso())
    record_id = _normalize_key(briefing_id) or _stable_id(
        "briefing",
        normalized_type,
        created,
        actions_executed,
        actions_recommended,
    )
    record = BriefingRecord(
        id=record_id,
        type=normalized_type,
        created_at=created,
        content=payload,
        actions_executed=max(0, int(actions_executed or 0)),
        actions_recommended=max(0, int(actions_recommended or 0)),
    )
    async with _connect() as conn:
        await conn.execute(
            """
            INSERT INTO briefings (
                id, type, created_at, content_json, actions_executed, actions_recommended
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                type = excluded.type,
                created_at = excluded.created_at,
                content_json = excluded.content_json,
                actions_executed = excluded.actions_executed,
                actions_recommended = excluded.actions_recommended
            """,
            (
                record.id,
                record.type,
                record.created_at,
                json.dumps(record.content, ensure_ascii=True, sort_keys=True),
                record.actions_executed,
                record.actions_recommended,
            ),
        )
        await conn.commit()
    return record


async def get_recent_briefings(
    *,
    limit: int = 7,
    briefing_type: Optional[str] = None,
) -> list[BriefingRecord]:
    safe_limit = max(1, min(int(limit or 7), 100))
    query = """
        SELECT * FROM briefings
        {where_clause}
        ORDER BY datetime(created_at) DESC
        LIMIT ?
    """
    params: tuple[Any, ...]
    where_clause = ""
    if briefing_type:
        where_clause = "WHERE type = ?"
        params = (str(briefing_type).strip().lower(), safe_limit)
    else:
        params = (safe_limit,)
    async with _connect() as conn:
        rows = await _fetchall(conn, query.format(where_clause=where_clause), params)
    return [
        BriefingRecord(
            id=str(row["id"]),
            type=str(row["type"]),
            created_at=str(row["created_at"]),
            content=_row_json(row, "content_json") or {},
            actions_executed=int(row["actions_executed"] or 0),
            actions_recommended=int(row["actions_recommended"] or 0),
        )
        for row in rows
    ]


async def get_latest_briefing(briefing_type: str = "daily") -> Optional[BriefingRecord]:
    records = await get_recent_briefings(limit=1, briefing_type=briefing_type)
    return records[0] if records else None


async def list_upcoming_bookings(within_hours: float = 4.0) -> list[BookingRecord]:
    """Return confirmed bookings scheduled within the next *within_hours* hours."""
    from datetime import timedelta

    now = datetime.now(timezone.utc)
    horizon = now + timedelta(hours=max(0.0, within_hours))
    now_str = now.isoformat()
    horizon_str = horizon.isoformat()
    async with _connect() as conn:
        rows = await _fetchall(
            conn,
            """
            SELECT * FROM bookings
            WHERE confirmed = 1
              AND datetime(scheduled_at) > datetime(?)
              AND datetime(scheduled_at) <= datetime(?)
            ORDER BY datetime(scheduled_at) ASC
            """,
            (now_str, horizon_str),
        )
    return [
        BookingRecord(
            id=str(row["id"]),
            lead_id=str(row["lead_id"]),
            source_key=_normalize_key(row["source_key"]),
            scheduled_at=str(row["scheduled_at"]),
            confirmed=bool(row["confirmed"]),
            metadata=_row_json(row, "metadata_json"),
            created_at=str(row["created_at"]),
        )
        for row in rows
    ]
