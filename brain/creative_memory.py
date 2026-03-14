from __future__ import annotations

import hashlib
import os
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class CreativePattern:
    domain: str
    offer: str
    hook: str
    angle: str
    cta: str
    pattern_used: str
    critic_score: float
    ctr: Optional[float] = None
    reply_rate: Optional[float] = None
    booking_rate: Optional[float] = None
    close_rate: Optional[float] = None
    campaign_id: Optional[str] = None
    traffic_temperature: Optional[str] = None
    created_at: Optional[str] = None
    id: Optional[str] = None


def _normalize_optional_float(value: Any) -> Optional[float]:
    if value in (None, "", "null"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _db_path() -> Path:
    configured = str(os.getenv("CREATIVE_MEMORY_DB_PATH") or "").strip()
    if configured:
        path = Path(configured)
    else:
        path = Path(__file__).resolve().parents[1] / "data" / "creative_memory.sqlite3"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS creative_patterns (
            id TEXT PRIMARY KEY,
            domain TEXT NOT NULL,
            offer TEXT NOT NULL,
            hook TEXT NOT NULL,
            angle TEXT NOT NULL,
            cta TEXT NOT NULL,
            pattern_used TEXT NOT NULL,
            critic_score REAL NOT NULL,
            ctr REAL,
            reply_rate REAL,
            booking_rate REAL,
            close_rate REAL,
            campaign_id TEXT,
            traffic_temperature TEXT,
            created_at TIMESTAMP NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_creative_patterns_domain
        ON creative_patterns(domain, created_at DESC)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_creative_patterns_campaign
        ON creative_patterns(campaign_id)
        """
    )
    conn.commit()
    return conn


def _pattern_id(pattern: CreativePattern) -> str:
    raw = "|".join(
        [
            str(pattern.domain or "").strip().lower(),
            str(pattern.offer or "").strip().lower(),
            str(pattern.hook or "").strip().lower(),
            str(pattern.angle or "").strip().lower(),
            str(pattern.cta or "").strip().lower(),
            str(pattern.pattern_used or "").strip().lower(),
            str(pattern.traffic_temperature or "").strip().lower(),
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _row_to_pattern(row: sqlite3.Row) -> CreativePattern:
    return CreativePattern(
        id=str(row["id"]),
        domain=str(row["domain"]),
        offer=str(row["offer"]),
        hook=str(row["hook"]),
        angle=str(row["angle"]),
        cta=str(row["cta"]),
        pattern_used=str(row["pattern_used"]),
        critic_score=float(row["critic_score"]),
        ctr=_normalize_optional_float(row["ctr"]),
        reply_rate=_normalize_optional_float(row["reply_rate"]),
        booking_rate=_normalize_optional_float(row["booking_rate"]),
        close_rate=_normalize_optional_float(row["close_rate"]),
        campaign_id=str(row["campaign_id"]) if row["campaign_id"] else None,
        traffic_temperature=str(row["traffic_temperature"]) if row["traffic_temperature"] else None,
        created_at=str(row["created_at"]),
    )


def save_creative_pattern(pattern: CreativePattern) -> CreativePattern:
    normalized = CreativePattern(
        id=pattern.id or _pattern_id(pattern),
        domain=str(pattern.domain or "unknown").strip().lower(),
        offer=str(pattern.offer or "Unknown").strip(),
        hook=str(pattern.hook or "").strip(),
        angle=str(pattern.angle or "").strip(),
        cta=str(pattern.cta or "").strip(),
        pattern_used=str(pattern.pattern_used or "unknown").strip().lower(),
        critic_score=float(pattern.critic_score or 0.0),
        ctr=_normalize_optional_float(pattern.ctr),
        reply_rate=_normalize_optional_float(pattern.reply_rate),
        booking_rate=_normalize_optional_float(pattern.booking_rate),
        close_rate=_normalize_optional_float(pattern.close_rate),
        campaign_id=str(pattern.campaign_id).strip() if pattern.campaign_id else None,
        traffic_temperature=str(pattern.traffic_temperature).strip().lower() if pattern.traffic_temperature else None,
        created_at=pattern.created_at or _now_iso(),
    )

    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO creative_patterns (
                id, domain, offer, hook, angle, cta, pattern_used, critic_score,
                ctr, reply_rate, booking_rate, close_rate, campaign_id,
                traffic_temperature, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                critic_score = excluded.critic_score,
                ctr = COALESCE(excluded.ctr, creative_patterns.ctr),
                reply_rate = COALESCE(excluded.reply_rate, creative_patterns.reply_rate),
                booking_rate = COALESCE(excluded.booking_rate, creative_patterns.booking_rate),
                close_rate = COALESCE(excluded.close_rate, creative_patterns.close_rate),
                campaign_id = COALESCE(excluded.campaign_id, creative_patterns.campaign_id),
                traffic_temperature = COALESCE(excluded.traffic_temperature, creative_patterns.traffic_temperature)
            """,
            (
                normalized.id,
                normalized.domain,
                normalized.offer,
                normalized.hook,
                normalized.angle,
                normalized.cta,
                normalized.pattern_used,
                normalized.critic_score,
                normalized.ctr,
                normalized.reply_rate,
                normalized.booking_rate,
                normalized.close_rate,
                normalized.campaign_id,
                normalized.traffic_temperature,
                normalized.created_at,
            ),
        )
        conn.commit()
    return normalized


def get_top_patterns(domain: str, limit: int = 10) -> List[CreativePattern]:
    safe_limit = max(1, int(limit or 10))
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM creative_patterns
            WHERE domain = ?
            ORDER BY
                CASE WHEN close_rate IS NULL THEN 1 ELSE 0 END ASC,
                close_rate DESC,
                CASE WHEN booking_rate IS NULL THEN 1 ELSE 0 END ASC,
                booking_rate DESC,
                CASE WHEN reply_rate IS NULL THEN 1 ELSE 0 END ASC,
                reply_rate DESC,
                CASE WHEN ctr IS NULL THEN 1 ELSE 0 END ASC,
                ctr DESC,
                critic_score DESC,
                created_at DESC
            LIMIT ?
            """,
            (str(domain or "unknown").strip().lower(), safe_limit),
        ).fetchall()
    return [_row_to_pattern(row) for row in rows]


def update_pattern_metrics(
    campaign_id: str,
    ctr: Optional[float] = None,
    reply_rate: Optional[float] = None,
    booking_rate: Optional[float] = None,
    close_rate: Optional[float] = None,
) -> int:
    if not str(campaign_id or "").strip():
        return 0
    normalized = {
        "ctr": _normalize_optional_float(ctr),
        "reply_rate": _normalize_optional_float(reply_rate),
        "booking_rate": _normalize_optional_float(booking_rate),
        "close_rate": _normalize_optional_float(close_rate),
    }
    with _connect() as conn:
        cursor = conn.execute(
            """
            UPDATE creative_patterns
            SET
                ctr = COALESCE(?, ctr),
                reply_rate = COALESCE(?, reply_rate),
                booking_rate = COALESCE(?, booking_rate),
                close_rate = COALESCE(?, close_rate)
            WHERE campaign_id = ?
            """,
            (
                normalized["ctr"],
                normalized["reply_rate"],
                normalized["booking_rate"],
                normalized["close_rate"],
                str(campaign_id).strip(),
            ),
        )
        conn.commit()
        return int(cursor.rowcount or 0)


def get_creative_memory(*, domain: str) -> List[Dict[str, Any]]:
    return [asdict(pattern) for pattern in get_top_patterns(domain=domain, limit=10)]


def record_creative_outcome(
    *,
    domain: str,
    creative_plan: Dict[str, Any],
    critic_review: Dict[str, Any],
    offer: Optional[str] = None,
) -> Optional[CreativePattern]:
    winner = dict(critic_review.get("winner") or {})
    if not winner:
        return None
    ideas = list(creative_plan.get("ideas") or creative_plan.get("angles") or [])
    winning_idea = next(
        (
            item
            for item in ideas
            if str(item.get("name") or "") == str(winner.get("name") or "")
        ),
        None,
    )
    if not winning_idea:
        return None
    pattern = CreativePattern(
        domain=domain,
        offer=str(offer or creative_plan.get("offer") or "Unknown"),
        hook=str(winning_idea.get("hook") or ""),
        angle=str(winning_idea.get("angle") or ""),
        cta=str(winning_idea.get("cta") or ""),
        pattern_used=str(winning_idea.get("pattern_used") or "unknown"),
        traffic_temperature=str(winning_idea.get("traffic_temperature") or creative_plan.get("traffic_temperature") or ""),
        critic_score=float(winner.get("score") or 0.0),
        campaign_id=str(creative_plan.get("campaign_id") or "") or None,
    )
    return save_creative_pattern(pattern)
