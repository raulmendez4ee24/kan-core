from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

from brain.creative_event_log import _get_conn, _lock

logger = logging.getLogger("kan_core.creative_attribution")

_REF_PATTERN = re.compile(r"\bref:([\w.-]+)", re.IGNORECASE)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now_utc().isoformat()


# ---------------------------------------------------------------------------
# Message parsing
# ---------------------------------------------------------------------------


def extract_campaign_from_message(text: str) -> Optional[str]:
    """Parse a ``ref:cmp-xyz`` token embedded in a WhatsApp prefilled message.
    Returns the campaign identifier (e.g. ``cmp-xyz``) or ``None``.
    Never raises."""
    try:
        cleaned = str(text or "").strip()
        if not cleaned:
            return None
        match = _REF_PATTERN.search(cleaned)
        if match:
            candidate = match.group(1).strip()
            return candidate if candidate else None
        return None
    except Exception:
        logger.exception(
            "[CREATIVE_ATTRIBUTION] extract_campaign_from_message failed"
        )
        return None


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def resolve_campaign(
    lead_id: str,
    *,
    hints: Optional[Dict[str, Any]] = None,
) -> Optional[Tuple[str, float]]:
    """Determine which campaign *lead_id* should be attributed to.

    Resolution order (first non-empty match wins):

    1. ``hints["campaign_id"]``   → confidence **1.0**
    2. ``hints["campaign_ref"]``  → confidence **1.0**
    3. ``hints["raw_text"]``      → ``extract_campaign_from_message`` → **1.0**
    4. ``hints["utm_campaign"]``  → confidence **0.9**
    5. ``hints["source_campaign"]`` → confidence **0.7**
    6. ``hints["ad_name"]``       → confidence **0.5**
    7. DB lookup on *lead_id*     → stored confidence (skipped if expired)

    Returns ``(campaign_id, confidence)`` or ``None``.  Never raises.
    """
    try:
        lid = str(lead_id or "").strip()
        if not lid:
            return None

        safe_hints = dict(hints or {})

        direct = str(safe_hints.get("campaign_id") or "").strip()
        if direct:
            return (direct, 1.0)

        ref = str(safe_hints.get("campaign_ref") or "").strip()
        if ref:
            return (ref, 1.0)

        raw_text = str(safe_hints.get("raw_text") or "").strip()
        if raw_text:
            extracted = extract_campaign_from_message(raw_text)
            if extracted:
                return (extracted, 1.0)

        utm = str(safe_hints.get("utm_campaign") or "").strip()
        if utm:
            return (utm, 0.9)

        src_cmp = str(safe_hints.get("source_campaign") or "").strip()
        if src_cmp:
            return (src_cmp, 0.7)

        ad = str(safe_hints.get("ad_name") or "").strip()
        if ad:
            return (ad, 0.5)

        conn = _get_conn()
        now = _now_iso()
        row = conn.execute(
            "SELECT campaign_id, confidence "
            "FROM creative_conversation_attribution "
            "WHERE lead_id = ? AND (expires_at IS NULL OR expires_at > ?)",
            (lid, now),
        ).fetchone()

        if row and row["campaign_id"]:
            stored_conf = row["confidence"]
            return (
                str(row["campaign_id"]),
                float(stored_conf) if stored_conf is not None else 0.5,
            )

        return None

    except Exception:
        logger.exception(
            "[CREATIVE_ATTRIBUTION] resolve_campaign failed lead_id=%s",
            lead_id,
        )
        return None


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register_attribution(
    lead_id: str,
    campaign_id: str,
    source: str,
    confidence: float = 0.5,
    expires_days: int = 7,
) -> bool:
    """Persist (or overwrite) the attribution link from *lead_id* →
    *campaign_id*.  Newer calls always replace older attributions.
    Never raises."""
    try:
        lid = str(lead_id or "").strip()
        cid = str(campaign_id or "").strip()
        src = str(source or "unknown").strip()
        if not lid or not cid:
            logger.warning(
                "[CREATIVE_ATTRIBUTION] register_attribution missing "
                "lead_id or campaign_id, skipping"
            )
            return False

        conf = max(0.0, min(1.0, float(confidence or 0.5)))
        now = _now_utc()
        now_str = now.isoformat()
        days = max(1, int(expires_days or 7))
        expires_str = (now + timedelta(days=days)).isoformat()

        conn = _get_conn()
        with _lock:
            conn.execute(
                """
                INSERT INTO creative_conversation_attribution
                    (lead_id, campaign_id, attribution_source,
                     confidence, created_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(lead_id) DO UPDATE SET
                    campaign_id        = excluded.campaign_id,
                    attribution_source = excluded.attribution_source,
                    confidence         = excluded.confidence,
                    created_at         = excluded.created_at,
                    expires_at         = excluded.expires_at
                """,
                (lid, cid, src, conf, now_str, expires_str),
            )
            conn.commit()

        logger.info(
            "[CREATIVE_ATTRIBUTION] registered lead_id=%s → "
            "campaign_id=%s source=%s confidence=%.2f expires=%s",
            lid,
            cid,
            src,
            conf,
            expires_str,
        )
        return True

    except Exception:
        logger.exception(
            "[CREATIVE_ATTRIBUTION] register_attribution failed "
            "lead_id=%s",
            lead_id,
        )
        return False
