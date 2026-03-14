from __future__ import annotations

import hashlib
import logging
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from brain.creative_attribution import register_attribution, resolve_campaign
from brain.creative_event_log import (
    CreativeEvent,
    get_unattributed_event_count,
    record_deployment,
    record_event,
    rebuild_metrics_cache,
)
from brain.creative_memory import update_pattern_metrics

logger = logging.getLogger("kan_core.creative_event_tracker")

_lock = threading.Lock()
_conn: Optional[sqlite3.Connection] = None


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


def _get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is not None:
        return _conn
    with _lock:
        if _conn is not None:
            return _conn
        conn = sqlite3.connect(str(_db_path()), check_same_thread=False, timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS creative_campaign_links (
                campaign_id TEXT PRIMARY KEY,
                domain TEXT,
                offer TEXT,
                hook TEXT,
                angle TEXT,
                cta TEXT,
                pattern_used TEXT,
                traffic_temperature TEXT,
                created_at TIMESTAMP NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_creative_campaign_links_domain
                ON creative_campaign_links(domain, created_at DESC);

            CREATE TABLE IF NOT EXISTS creative_outcomes (
                campaign_id TEXT PRIMARY KEY,
                impressions INTEGER DEFAULT 0,
                clicks INTEGER DEFAULT 0,
                conversations INTEGER DEFAULT 0,
                replies INTEGER DEFAULT 0,
                meeting_requests INTEGER DEFAULT 0,
                bookings INTEGER DEFAULT 0,
                shows INTEGER DEFAULT 0,
                closes INTEGER DEFAULT 0,
                updated_at TIMESTAMP NOT NULL
            );
            """
        )
        conn.commit()
        _conn = conn
        return conn


def _upsert_outcomes(
    campaign_id: str,
    *,
    impressions: int = 0,
    clicks: int = 0,
    conversations: int = 0,
    replies: int = 0,
    meeting_requests: int = 0,
    bookings: int = 0,
    shows: int = 0,
    closes: int = 0,
) -> None:
    conn = _get_conn()
    with _lock:
        conn.execute(
            """
            INSERT INTO creative_outcomes (
                campaign_id, impressions, clicks, conversations, replies,
                meeting_requests, bookings, shows, closes, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(campaign_id) DO UPDATE SET
                impressions = creative_outcomes.impressions + excluded.impressions,
                clicks = creative_outcomes.clicks + excluded.clicks,
                conversations = creative_outcomes.conversations + excluded.conversations,
                replies = creative_outcomes.replies + excluded.replies,
                meeting_requests = creative_outcomes.meeting_requests + excluded.meeting_requests,
                bookings = creative_outcomes.bookings + excluded.bookings,
                shows = creative_outcomes.shows + excluded.shows,
                closes = creative_outcomes.closes + excluded.closes,
                updated_at = excluded.updated_at
            """,
            (
                campaign_id,
                max(0, int(impressions or 0)),
                max(0, int(clicks or 0)),
                max(0, int(conversations or 0)),
                max(0, int(replies or 0)),
                max(0, int(meeting_requests or 0)),
                max(0, int(bookings or 0)),
                max(0, int(shows or 0)),
                max(0, int(closes or 0)),
                _now_iso(),
            ),
        )
        conn.commit()


def resolve_campaign_source(
    campaign_id: Optional[str] = None,
    utm_campaign: Optional[str] = None,
    source_campaign: Optional[str] = None,
    ad_name: Optional[str] = None,
) -> Optional[str]:
    for raw in (campaign_id, utm_campaign, source_campaign, ad_name):
        token = str(raw or "").strip()
        if token:
            return token
    return None


def register_creative_pattern_usage(
    campaign_id: Optional[str],
    domain: str,
    offer: str,
    hook: str,
    angle: str,
    cta: str,
    pattern_used: str,
    traffic_temperature: Optional[str],
) -> bool:
    cid = str(campaign_id or "").strip()
    if not cid:
        return False
    conn = _get_conn()
    with _lock:
        conn.execute(
            """
            INSERT INTO creative_campaign_links (
                campaign_id, domain, offer, hook, angle, cta,
                pattern_used, traffic_temperature, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(campaign_id) DO UPDATE SET
                domain = excluded.domain,
                offer = excluded.offer,
                hook = excluded.hook,
                angle = excluded.angle,
                cta = excluded.cta,
                pattern_used = excluded.pattern_used,
                traffic_temperature = excluded.traffic_temperature
            """,
            (
                cid,
                str(domain or "").strip().lower(),
                str(offer or "").strip(),
                str(hook or "").strip(),
                str(angle or "").strip(),
                str(cta or "").strip(),
                str(pattern_used or "").strip().lower(),
                str(traffic_temperature or "").strip().lower() or None,
                _now_iso(),
            ),
        )
        conn.commit()
    recalculate_and_update_pattern_metrics(cid)
    return True


def record_campaign_metrics(
    campaign_id: Optional[str],
    *,
    impressions: int = 0,
    clicks: int = 0,
) -> bool:
    cid = str(campaign_id or "").strip()
    if not cid:
        return False
    _upsert_outcomes(cid, impressions=impressions, clicks=clicks)
    recalculate_and_update_pattern_metrics(cid)
    return True


def record_whatsapp_outcome(
    campaign_id: Optional[str],
    *,
    conversation_started: bool = False,
    replied: bool = False,
    meeting_requested: bool = False,
    booked: bool = False,
) -> bool:
    cid = str(campaign_id or "").strip()
    if not cid:
        return False
    _upsert_outcomes(
        cid,
        conversations=1 if conversation_started else 0,
        replies=1 if replied else 0,
        meeting_requests=1 if meeting_requested else 0,
        bookings=1 if booked else 0,
    )
    recalculate_and_update_pattern_metrics(cid)
    return True


def record_sales_outcome(
    campaign_id: Optional[str],
    *,
    showed: bool = False,
    closed: bool = False,
) -> bool:
    cid = str(campaign_id or "").strip()
    if not cid:
        return False
    _upsert_outcomes(
        cid,
        shows=1 if showed else 0,
        closes=1 if closed else 0,
    )
    recalculate_and_update_pattern_metrics(cid)
    return True


def recalculate_and_update_pattern_metrics(campaign_id: Optional[str]) -> Dict[str, Any]:
    cid = str(campaign_id or "").strip()
    if not cid:
        return {}
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM creative_outcomes WHERE campaign_id = ?",
        (cid,),
    ).fetchone()
    if row is None:
        return {}
    impressions = int(row["impressions"] or 0)
    clicks = int(row["clicks"] or 0)
    conversations = int(row["conversations"] or 0)
    replies = int(row["replies"] or 0)
    bookings = int(row["bookings"] or 0)
    closes = int(row["closes"] or 0)

    ctr = (clicks / impressions) if impressions else None
    reply_rate = (replies / conversations) if conversations else None
    booking_rate = (bookings / conversations) if conversations else None
    close_rate = (closes / bookings) if bookings else None

    update_pattern_metrics(
        cid,
        ctr=ctr,
        reply_rate=reply_rate,
        booking_rate=booking_rate,
        close_rate=close_rate,
    )
    return {
        "campaign_id": cid,
        "impressions": impressions,
        "clicks": clicks,
        "conversations": conversations,
        "replies": replies,
        "meeting_requests": int(row["meeting_requests"] or 0),
        "bookings": bookings,
        "shows": int(row["shows"] or 0),
        "closes": closes,
        "ctr": ctr,
        "reply_rate": reply_rate,
        "booking_rate": booking_rate,
        "close_rate": close_rate,
    }


def _propagate_legacy_metrics(campaign_id: Optional[str]) -> None:
    cid = str(campaign_id or "").strip()
    if not cid:
        return
    try:
        legacy = rebuild_metrics_cache(cid)
    except Exception:
        logger.exception("[CREATIVE_TRACKER] legacy metrics rebuild failed campaign_id=%s", cid)
        return
    if not legacy:
        return
    record_campaign_metrics(
        cid,
        impressions=int(legacy.get("impressions") or 0),
        clicks=int(legacy.get("clicks") or 0),
    )


def _stable_message_key(lead_id: str, channel: str, *, message_id: str = "", text: str = "") -> str:
    mid = str(message_id or "").strip()
    if mid:
        return f"{channel}:{lead_id}:{mid}"
    content = str(text or "").strip()
    if content:
        digest = hashlib.sha256(f"{channel}:{lead_id}:{content}".encode()).hexdigest()[:16]
        return f"{channel}:{lead_id}:sha:{digest}"
    return f"{channel}:{lead_id}:empty:{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}"


def _resolve_and_attribute(
    lead_id: str,
    channel: str,
    *,
    raw_text: str = "",
    campaign_hint: Optional[str] = None,
) -> tuple[Optional[str], float]:
    hints: Dict[str, Any] = {}
    if campaign_hint:
        hints["campaign_id"] = campaign_hint
    if raw_text:
        hints["raw_text"] = raw_text
    result = resolve_campaign(lead_id, hints=hints)
    if not result:
        logger.info(
            "[CREATIVE_TRACKER] unattributed_event lead=%s channel=%s total=%d",
            lead_id,
            channel,
            get_unattributed_event_count(),
        )
        return (None, 0.0)
    cid, conf = result
    if conf >= 0.7:
        source = "ref_tag" if conf >= 1.0 else "hint"
        register_attribution(lead_id, cid, source, conf)
    return (cid, conf)


def on_campaign_deployed(campaign_id: str, pattern_id: str, domain: str) -> None:
    try:
        cid = str(campaign_id or "").strip()
        pid = str(pattern_id or "").strip()
        if not cid or not pid:
            return
        record_deployment(cid, pid, domain)
        try:
            conn = _get_conn()
            with _lock:
                conn.execute(
                    "UPDATE creative_patterns SET campaign_id = ? WHERE id = ?",
                    (cid, pid),
                )
                conn.commit()
        except Exception:
            logger.warning("[CREATIVE_TRACKER] could not link pattern_id=%s to campaign_id=%s", pid, cid, exc_info=True)
        _propagate_legacy_metrics(cid)
    except Exception:
        logger.exception("[CREATIVE_TRACKER] on_campaign_deployed failed")


def on_ad_metrics(campaign_id: str, impressions: int, clicks: int) -> None:
    try:
        cid = str(campaign_id or "").strip()
        if not cid:
            return
        record_campaign_metrics(cid, impressions=impressions, clicks=clicks)
    except Exception:
        logger.exception("[CREATIVE_TRACKER] on_ad_metrics failed")


def on_conversation_started(
    lead_id: str,
    channel: str,
    raw_text: str,
    campaign_hint: Optional[str] = None,
) -> None:
    try:
        lid = str(lead_id or "").strip()
        ch = str(channel or "").strip()
        if not lid or not ch:
            return
        cid, conf = _resolve_and_attribute(lid, ch, raw_text=raw_text, campaign_hint=campaign_hint)
        key = _stable_message_key(lid, ch, text=raw_text)
        inserted = record_event(
            CreativeEvent(
                event_type="conversation_started",
                source=ch,
                idempotency_key=key,
                lead_id=lid,
                campaign_id=cid,
                attribution_confidence=conf,
                metadata={"raw_text": str(raw_text or "")[:500]},
            )
        )
        if inserted and cid:
            record_whatsapp_outcome(cid, conversation_started=True)
            _propagate_legacy_metrics(cid)
    except Exception:
        logger.exception("[CREATIVE_TRACKER] on_conversation_started failed")


def on_reply(
    lead_id: str,
    channel: str,
    *,
    is_qualified: bool = False,
    message_id: str = "",
    text: str = "",
) -> None:
    try:
        lid = str(lead_id or "").strip()
        ch = str(channel or "").strip()
        if not lid or not ch:
            return
        resolved = resolve_campaign(lid)
        cid = resolved[0] if resolved else None
        conf = float(resolved[1]) if resolved else 0.0
        event_type = "qualified_reply" if is_qualified else "reply"
        key = _stable_message_key(lid, ch, message_id=message_id, text=text)
        inserted = record_event(
            CreativeEvent(
                event_type=event_type,
                source=ch,
                idempotency_key=key,
                lead_id=lid,
                campaign_id=cid,
                attribution_confidence=conf,
                metadata={"message_id": message_id, "text": str(text or "")[:500]},
            )
        )
        if inserted and cid and is_qualified:
            record_whatsapp_outcome(cid, replied=True)
            _propagate_legacy_metrics(cid)
    except Exception:
        logger.exception("[CREATIVE_TRACKER] on_reply failed")


def on_meeting_scheduled(lead_id: str, scheduled_at: str) -> None:
    try:
        lid = str(lead_id or "").strip()
        if not lid:
            return
        resolved = resolve_campaign(lid)
        cid = resolved[0] if resolved else None
        conf = float(resolved[1]) if resolved else 0.0
        inserted = record_event(
            CreativeEvent(
                event_type="meeting_scheduled",
                source="meeting_request",
                idempotency_key=f"meeting:{lid}:{str(scheduled_at or '').strip()}",
                lead_id=lid,
                campaign_id=cid,
                attribution_confidence=conf,
                metadata={"scheduled_at": scheduled_at},
            )
        )
        if inserted and cid:
            record_whatsapp_outcome(cid, booked=True)
            _propagate_legacy_metrics(cid)
    except Exception:
        logger.exception("[CREATIVE_TRACKER] on_meeting_scheduled failed")


def on_show(lead_id: str) -> None:
    try:
        lid = str(lead_id or "").strip()
        if not lid:
            return
        resolved = resolve_campaign(lid)
        cid = resolved[0] if resolved else None
        conf = float(resolved[1]) if resolved else 0.0
        inserted = record_event(
            CreativeEvent(
                event_type="show",
                source="meeting_request",
                idempotency_key=f"show:{lid}:{datetime.now(timezone.utc).strftime('%Y-%m-%d')}",
                lead_id=lid,
                campaign_id=cid,
                attribution_confidence=conf,
            )
        )
        if inserted and cid:
            record_sales_outcome(cid, showed=True)
            _propagate_legacy_metrics(cid)
    except Exception:
        logger.exception("[CREATIVE_TRACKER] on_show failed")


def on_close(lead_id: str, value_usd: float = 0.0) -> None:
    try:
        lid = str(lead_id or "").strip()
        if not lid:
            return
        resolved = resolve_campaign(lid)
        cid = resolved[0] if resolved else None
        conf = float(resolved[1]) if resolved else 0.0
        inserted = record_event(
            CreativeEvent(
                event_type="close",
                source="sales",
                idempotency_key=f"close:{lid}:{datetime.now(timezone.utc).strftime('%Y-%m-%d')}",
                lead_id=lid,
                campaign_id=cid,
                attribution_confidence=conf,
                value_usd=float(value_usd or 0.0),
            )
        )
        if inserted and cid:
            record_sales_outcome(cid, closed=True)
            _propagate_legacy_metrics(cid)
    except Exception:
        logger.exception("[CREATIVE_TRACKER] on_close failed")


def _reset_connection() -> None:
    global _conn
    with _lock:
        if _conn is not None:
            try:
                _conn.close()
            except Exception:
                pass
            _conn = None
