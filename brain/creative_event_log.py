from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("kan_core.creative_event_log")

VALID_EVENT_TYPES = frozenset({
    "impression",
    "click",
    "conversation_started",
    "reply",
    "qualified_reply",
    "meeting_scheduled",
    "show",
    "close",
})


@dataclass
class CreativeEvent:
    """Transfer object for a single funnel event flowing into the creative feedback loop."""

    event_type: str
    source: str
    idempotency_key: str
    lead_id: Optional[str] = None
    campaign_id: Optional[str] = None
    attribution_confidence: float = 0.0
    value_usd: Optional[float] = None
    metadata: Optional[Dict[str, Any]] = None
    timestamp: Optional[str] = None


# ---------------------------------------------------------------------------
# Shared connection — WAL mode, busy_timeout, check_same_thread=False
# ---------------------------------------------------------------------------

_lock = threading.Lock()
_conn: Optional[sqlite3.Connection] = None


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
        conn = sqlite3.connect(
            str(_db_path()),
            check_same_thread=False,
            timeout=10.0,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        _bootstrap_tables(conn)
        _conn = conn
    return _conn


def _bootstrap_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS creative_events (
            id TEXT PRIMARY KEY,
            campaign_id TEXT,
            event_type TEXT NOT NULL,
            source TEXT NOT NULL,
            attribution_confidence REAL DEFAULT 0.0,
            lead_id TEXT,
            value_usd REAL,
            metadata TEXT,
            created_at TIMESTAMP NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_cevt_campaign
            ON creative_events(campaign_id, event_type);
        CREATE INDEX IF NOT EXISTS idx_cevt_type
            ON creative_events(event_type, created_at);
        CREATE INDEX IF NOT EXISTS idx_cevt_lead
            ON creative_events(lead_id);

        CREATE TABLE IF NOT EXISTS creative_deployments (
            id TEXT PRIMARY KEY,
            campaign_id TEXT NOT NULL,
            pattern_id TEXT NOT NULL,
            domain TEXT NOT NULL,
            deployed_at TIMESTAMP NOT NULL,
            UNIQUE(campaign_id, pattern_id)
        );
        CREATE INDEX IF NOT EXISTS idx_cdep_campaign
            ON creative_deployments(campaign_id);
        CREATE INDEX IF NOT EXISTS idx_cdep_pattern
            ON creative_deployments(pattern_id);

        CREATE TABLE IF NOT EXISTS creative_metrics_cache (
            campaign_id TEXT PRIMARY KEY,
            impressions INTEGER DEFAULT 0,
            clicks INTEGER DEFAULT 0,
            conversations INTEGER DEFAULT 0,
            replies INTEGER DEFAULT 0,
            qualified_replies INTEGER DEFAULT 0,
            meetings_scheduled INTEGER DEFAULT 0,
            shows INTEGER DEFAULT 0,
            closes INTEGER DEFAULT 0,
            total_revenue_usd REAL DEFAULT 0.0,
            ctr REAL,
            response_rate REAL,
            booking_rate REAL,
            show_rate REAL,
            close_rate REAL,
            cost_per_qualified_reply REAL,
            revenue_per_conversation REAL,
            updated_at TIMESTAMP NOT NULL
        );

        CREATE TABLE IF NOT EXISTS creative_conversation_attribution (
            lead_id TEXT PRIMARY KEY,
            campaign_id TEXT,
            attribution_source TEXT NOT NULL,
            confidence REAL DEFAULT 0.5,
            created_at TIMESTAMP NOT NULL,
            expires_at TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_cattr_campaign
            ON creative_conversation_attribution(campaign_id);
        """
    )
    conn.commit()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Event recording
# ---------------------------------------------------------------------------


def record_event(event: CreativeEvent) -> bool:
    """Idempotent insert of a funnel event. Returns True if stored, False if
    duplicate or invalid.  Never raises."""
    try:
        key = str(event.idempotency_key or "").strip()
        if not key:
            logger.warning(
                "[CREATIVE_EVENT_LOG] record_event called with empty "
                "idempotency_key, skipping"
            )
            return False

        if event.event_type not in VALID_EVENT_TYPES:
            logger.warning(
                "[CREATIVE_EVENT_LOG] invalid event_type=%s, skipping",
                event.event_type,
            )
            return False

        conn = _get_conn()
        ts = event.timestamp or _now_iso()
        meta_json = (
            json.dumps(event.metadata, default=str) if event.metadata else None
        )
        cid = str(event.campaign_id).strip() if event.campaign_id else None
        lid = str(event.lead_id).strip() if event.lead_id else None
        val = float(event.value_usd) if event.value_usd is not None else None

        with _lock:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO creative_events
                    (id, campaign_id, event_type, source,
                     attribution_confidence, lead_id, value_usd,
                     metadata, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    key,
                    cid,
                    event.event_type,
                    event.source,
                    float(event.attribution_confidence or 0.0),
                    lid,
                    val,
                    meta_json,
                    ts,
                ),
            )
            conn.commit()
            inserted = cursor.rowcount > 0

        if not inserted:
            logger.info(
                "[CREATIVE_EVENT_LOG] duplicate skipped key=%s", key
            )
            return False

        logger.info(
            "[CREATIVE_EVENT_LOG] recorded event_type=%s campaign_id=%s "
            "lead_id=%s confidence=%.2f key=%s",
            event.event_type,
            cid,
            lid,
            event.attribution_confidence,
            key,
        )
        return True

    except Exception:
        logger.exception(
            "[CREATIVE_EVENT_LOG] record_event failed key=%s",
            getattr(event, "idempotency_key", "?"),
        )
        return False


# ---------------------------------------------------------------------------
# Metrics cache
# ---------------------------------------------------------------------------


def rebuild_metrics_cache(campaign_id: str) -> Dict[str, Any]:
    """Recompute all metrics from the event log for *campaign_id* and upsert
    into the cache table.  Returns the computed metrics dict (empty on error).
    Never raises."""
    empty: Dict[str, Any] = {}
    try:
        cid = str(campaign_id or "").strip()
        if not cid:
            return empty

        conn = _get_conn()

        with _lock:
            rows = conn.execute(
                "SELECT event_type, COUNT(*) AS cnt, "
                "COALESCE(SUM(value_usd), 0.0) AS total_value "
                "FROM creative_events WHERE campaign_id = ? "
                "GROUP BY event_type",
                (cid,),
            ).fetchall()

            counts: Dict[str, int] = {}
            revenue = 0.0
            for row in rows:
                counts[row["event_type"]] = int(row["cnt"])
                if row["event_type"] == "close":
                    revenue = float(row["total_value"])

            imp = counts.get("impression", 0)
            clk = counts.get("click", 0)
            conv = counts.get("conversation_started", 0)
            rep = counts.get("reply", 0)
            qrep = counts.get("qualified_reply", 0)
            mtg = counts.get("meeting_scheduled", 0)
            shw = counts.get("show", 0)
            cls = counts.get("close", 0)

            ctr = (clk / imp) if imp else None
            response_rate = (qrep / conv) if conv else None
            booking_rate = (mtg / conv) if conv else None
            show_rate = (shw / mtg) if mtg else None
            close_rate = (
                (cls / shw) if shw else ((cls / mtg) if mtg else None)
            )
            rev_per_conv = (revenue / conv) if conv else None

            now = _now_iso()

            conn.execute(
                """
                INSERT INTO creative_metrics_cache (
                    campaign_id, impressions, clicks, conversations,
                    replies, qualified_replies, meetings_scheduled,
                    shows, closes, total_revenue_usd,
                    ctr, response_rate, booking_rate, show_rate,
                    close_rate, cost_per_qualified_reply,
                    revenue_per_conversation, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(campaign_id) DO UPDATE SET
                    impressions          = excluded.impressions,
                    clicks               = excluded.clicks,
                    conversations        = excluded.conversations,
                    replies              = excluded.replies,
                    qualified_replies    = excluded.qualified_replies,
                    meetings_scheduled   = excluded.meetings_scheduled,
                    shows                = excluded.shows,
                    closes               = excluded.closes,
                    total_revenue_usd    = excluded.total_revenue_usd,
                    ctr                  = excluded.ctr,
                    response_rate        = excluded.response_rate,
                    booking_rate         = excluded.booking_rate,
                    show_rate            = excluded.show_rate,
                    close_rate           = excluded.close_rate,
                    cost_per_qualified_reply  = excluded.cost_per_qualified_reply,
                    revenue_per_conversation = excluded.revenue_per_conversation,
                    updated_at           = excluded.updated_at
                """,
                (
                    cid, imp, clk, conv, rep, qrep, mtg, shw, cls,
                    revenue, ctr, response_rate, booking_rate, show_rate,
                    close_rate, None, rev_per_conv, now,
                ),
            )
            conn.commit()

        metrics: Dict[str, Any] = {
            "campaign_id": cid,
            "impressions": imp,
            "clicks": clk,
            "conversations": conv,
            "replies": rep,
            "qualified_replies": qrep,
            "meetings_scheduled": mtg,
            "shows": shw,
            "closes": cls,
            "total_revenue_usd": revenue,
            "ctr": ctr,
            "response_rate": response_rate,
            "booking_rate": booking_rate,
            "show_rate": show_rate,
            "close_rate": close_rate,
            "cost_per_qualified_reply": None,
            "revenue_per_conversation": rev_per_conv,
            "updated_at": now,
        }

        logger.info(
            "[CREATIVE_EVENT_LOG] cache rebuilt campaign_id=%s "
            "imp=%d clk=%d conv=%d rep=%d qrep=%d mtg=%d shw=%d cls=%d "
            "ctr=%s resp=%s book=%s close=%s",
            cid, imp, clk, conv, rep, qrep, mtg, shw, cls,
            f"{ctr:.4f}" if ctr is not None else "null",
            f"{response_rate:.4f}" if response_rate is not None else "null",
            f"{booking_rate:.4f}" if booking_rate is not None else "null",
            f"{close_rate:.4f}" if close_rate is not None else "null",
        )
        return metrics

    except Exception:
        logger.exception(
            "[CREATIVE_EVENT_LOG] rebuild_metrics_cache failed "
            "campaign_id=%s",
            campaign_id,
        )
        return empty


def get_cached_metrics(campaign_id: str) -> Optional[Dict[str, Any]]:
    """Read pre-computed metrics from the cache.  Returns None when missing.
    Never raises."""
    try:
        cid = str(campaign_id or "").strip()
        if not cid:
            return None
        conn = _get_conn()
        row = conn.execute(
            "SELECT * FROM creative_metrics_cache WHERE campaign_id = ?",
            (cid,),
        ).fetchone()
        return dict(row) if row else None
    except Exception:
        logger.exception("[CREATIVE_EVENT_LOG] get_cached_metrics failed")
        return None


# ---------------------------------------------------------------------------
# Deployment tracking (pattern <-> campaign link)
# ---------------------------------------------------------------------------


def record_deployment(
    campaign_id: str,
    pattern_id: str,
    domain: str,
) -> bool:
    """Record that *pattern_id* was deployed in *campaign_id*.  Idempotent.
    Never raises."""
    try:
        cid = str(campaign_id or "").strip()
        pid = str(pattern_id or "").strip()
        dom = str(domain or "").strip().lower()
        if not cid or not pid:
            logger.warning(
                "[CREATIVE_EVENT_LOG] record_deployment called with "
                "missing campaign_id or pattern_id, skipping"
            )
            return False

        dep_id = hashlib.sha256(f"{cid}|{pid}".encode()).hexdigest()
        conn = _get_conn()

        with _lock:
            conn.execute(
                "INSERT OR IGNORE INTO creative_deployments "
                "(id, campaign_id, pattern_id, domain, deployed_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (dep_id, cid, pid, dom, _now_iso()),
            )
            conn.commit()

        logger.info(
            "[CREATIVE_EVENT_LOG] deployment recorded "
            "campaign_id=%s pattern_id=%s domain=%s",
            cid, pid, dom,
        )
        return True

    except Exception:
        logger.exception("[CREATIVE_EVENT_LOG] record_deployment failed")
        return False


def get_deployments_for_campaign(
    campaign_id: str,
) -> List[Dict[str, Any]]:
    """Return all pattern deployments for a campaign.  Never raises."""
    try:
        cid = str(campaign_id or "").strip()
        if not cid:
            return []
        conn = _get_conn()
        rows = conn.execute(
            "SELECT * FROM creative_deployments WHERE campaign_id = ?",
            (cid,),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        logger.exception(
            "[CREATIVE_EVENT_LOG] get_deployments_for_campaign failed"
        )
        return []


# ---------------------------------------------------------------------------
# Observability helpers
# ---------------------------------------------------------------------------


def get_unattributed_event_count() -> int:
    """Data-quality KPI: how many events lack a campaign_id.  Never raises."""
    try:
        conn = _get_conn()
        row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM creative_events "
            "WHERE campaign_id IS NULL",
        ).fetchone()
        return int(row["cnt"]) if row else 0
    except Exception:
        logger.exception(
            "[CREATIVE_EVENT_LOG] get_unattributed_event_count failed"
        )
        return 0


def get_event_count(campaign_id: str) -> int:
    """Total events recorded for a campaign.  Never raises."""
    try:
        cid = str(campaign_id or "").strip()
        if not cid:
            return 0
        conn = _get_conn()
        row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM creative_events "
            "WHERE campaign_id = ?",
            (cid,),
        ).fetchone()
        return int(row["cnt"]) if row else 0
    except Exception:
        logger.exception("[CREATIVE_EVENT_LOG] get_event_count failed")
        return 0


# ---------------------------------------------------------------------------
# Test support
# ---------------------------------------------------------------------------


def _reset_connection() -> None:
    """Close the shared connection so the next call to ``_get_conn`` creates a
    fresh one.  **For tests only** — allows ``monkeypatch.setenv`` to redirect
    the DB path between test cases."""
    global _conn
    with _lock:
        if _conn is not None:
            try:
                _conn.close()
            except Exception:
                pass
            _conn = None
