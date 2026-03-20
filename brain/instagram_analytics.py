"""Instagram Analytics Engine — Graph API v22.0 insights + diagnostics.

Fetches account info, post metrics, post-level insights, and account reach.
Caches results in SQLite to respect rate limits.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import aiosqlite
import httpx

from schemas.instagram_analytics import (
    AccountInfo,
    AccountReach,
    InstagramIssue,
    InstagramReport,
    PostInsights,
    PostMetrics,
    PostWithInsights,
)

logger = logging.getLogger("kan_core.instagram_analytics")

_GRAPH_BASE = "https://graph.facebook.com/v22.0"
_CACHE_TTL_SECONDS = 6 * 3600  # 6 hours


# ---------------------------------------------------------------------------
# SQLite cache
# ---------------------------------------------------------------------------


def _db_path() -> Path:
    configured = str(os.getenv("INSTAGRAM_ANALYTICS_DB_PATH") or "").strip()
    if configured:
        path = Path(configured)
    else:
        path = Path(__file__).resolve().parents[1] / "data" / "instagram_analytics.sqlite3"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


@asynccontextmanager
async def _connect():
    async with aiosqlite.connect(str(_db_path())) as conn:
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA journal_mode = WAL")
        await _ensure_schema(conn)
        yield conn


async def _ensure_schema(conn: aiosqlite.Connection) -> None:
    await conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS ig_post_insights (
            media_id TEXT PRIMARY KEY,
            reach INTEGER DEFAULT 0,
            saved INTEGER DEFAULT 0,
            shares INTEGER DEFAULT 0,
            total_interactions INTEGER DEFAULT 0,
            fetched_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS ig_daily_snapshots (
            snapshot_date TEXT PRIMARY KEY,
            followers INTEGER DEFAULT 0,
            total_reach INTEGER DEFAULT 0,
            posts_published INTEGER DEFAULT 0,
            avg_engagement_rate REAL DEFAULT 0.0,
            issues_json TEXT,
            created_at TEXT NOT NULL
        );
        """
    )
    await conn.commit()


# ---------------------------------------------------------------------------
# Core class
# ---------------------------------------------------------------------------


class InstagramAnalytics:
    """Fetches and analyzes Instagram metrics via Graph API v22.0."""

    def __init__(
        self,
        *,
        ig_account_id: str | None = None,
        access_token: str | None = None,
    ) -> None:
        self._ig_id = (ig_account_id or os.getenv("INSTAGRAM_ACCOUNT_ID", "")).strip()
        self._token = (access_token or os.getenv("META_ACCESS_TOKEN", "")).strip()

    def _configured(self) -> bool:
        return bool(self._ig_id and self._token)

    # ------------------------------------------------------------------
    # Graph API helpers
    # ------------------------------------------------------------------

    async def _get(self, path: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        url = f"{_GRAPH_BASE}/{path}"
        all_params = {"access_token": self._token, **(params or {})}
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(url, params=all_params)
            if resp.status_code != 200:
                err = resp.json().get("error", {}).get("message", resp.text[:200])
                logger.warning("IG API %s → %s: %s", path, resp.status_code, err)
                return {}
            return resp.json()

    # ------------------------------------------------------------------
    # Account info
    # ------------------------------------------------------------------

    async def fetch_account_info(self) -> AccountInfo:
        if not self._configured():
            return AccountInfo(ig_id="")
        data = await self._get(
            self._ig_id,
            {"fields": "id,name,username,biography,followers_count,follows_count,media_count"},
        )
        return AccountInfo(
            ig_id=str(data.get("id", self._ig_id)),
            username=str(data.get("username", "")),
            name=str(data.get("name", "")),
            biography=str(data.get("biography", "")),
            followers_count=int(data.get("followers_count", 0)),
            follows_count=int(data.get("follows_count", 0)),
            media_count=int(data.get("media_count", 0)),
        )

    # ------------------------------------------------------------------
    # Recent posts
    # ------------------------------------------------------------------

    async def fetch_recent_posts(self, limit: int = 25) -> list[PostMetrics]:
        if not self._configured():
            return []
        data = await self._get(
            f"{self._ig_id}/media",
            {
                "fields": "id,caption,media_type,timestamp,like_count,comments_count",
                "limit": str(min(limit, 50)),
            },
        )
        return [
            PostMetrics(
                media_id=str(p["id"]),
                caption=p.get("caption"),
                media_type=str(p.get("media_type", "IMAGE")),
                timestamp=p.get("timestamp"),
                like_count=int(p.get("like_count", 0)),
                comments_count=int(p.get("comments_count", 0)),
            )
            for p in data.get("data", [])
        ]

    # ------------------------------------------------------------------
    # Post-level insights (with cache)
    # ------------------------------------------------------------------

    async def fetch_post_insights(self, media_id: str) -> PostInsights:
        # Check cache first
        async with _connect() as conn:
            cursor = await conn.execute(
                "SELECT reach, saved, shares, total_interactions, fetched_at FROM ig_post_insights WHERE media_id = ?",
                (media_id,),
            )
            row = await cursor.fetchone()
            if row:
                fetched = str(row["fetched_at"])
                try:
                    age = (datetime.now(timezone.utc) - datetime.fromisoformat(fetched)).total_seconds()
                except Exception:
                    age = _CACHE_TTL_SECONDS + 1
                if age < _CACHE_TTL_SECONDS:
                    reach = int(row["reach"] or 0)
                    saved = int(row["saved"] or 0)
                    shares = int(row["shares"] or 0)
                    interactions = int(row["total_interactions"] or 0)
                    return PostInsights(
                        media_id=media_id,
                        reach=reach,
                        saved=saved,
                        shares=shares,
                        total_interactions=interactions,
                        engagement_rate=(interactions / reach * 100) if reach else 0.0,
                    )

        # Fetch from API — v22.0 metrics
        data = await self._get(
            f"{media_id}/insights",
            {"metric": "reach,saved,shares,total_interactions"},
        )
        insights: dict[str, int] = {}
        for item in data.get("data", []):
            name = item.get("name", "")
            vals = item.get("values", [])
            if vals:
                insights[name] = int(vals[0].get("value", 0))

        reach = insights.get("reach", 0)
        saved = insights.get("saved", 0)
        shares = insights.get("shares", 0)
        interactions = insights.get("total_interactions", 0)
        er = (interactions / reach * 100) if reach else 0.0

        # Store in cache
        async with _connect() as conn:
            await conn.execute(
                """
                INSERT OR REPLACE INTO ig_post_insights (media_id, reach, saved, shares, total_interactions, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (media_id, reach, saved, shares, interactions, datetime.now(timezone.utc).isoformat()),
            )
            await conn.commit()

        return PostInsights(
            media_id=media_id,
            reach=reach,
            saved=saved,
            shares=shares,
            total_interactions=interactions,
            engagement_rate=round(er, 2),
        )

    # ------------------------------------------------------------------
    # Account reach (28 days)
    # ------------------------------------------------------------------

    async def fetch_account_reach(self, days: int = 28) -> AccountReach:
        if not self._configured():
            return AccountReach()
        now = datetime.now(timezone.utc)
        since = int((now - timedelta(days=days)).timestamp())
        until = int(now.timestamp())
        data = await self._get(
            f"{self._ig_id}/insights",
            {"metric": "reach", "period": "day", "since": str(since), "until": str(until)},
        )
        all_data = data.get("data", [])
        if not all_data:
            return AccountReach()
        values = [v["value"] for v in all_data[0].get("values", []) if v.get("value")]
        if not values:
            return AccountReach()
        total = sum(values)
        return AccountReach(
            total_reach=total,
            avg_reach_per_day=round(total / len(values), 1),
            best_day_reach=max(values),
            days_measured=len(values),
        )

    # ------------------------------------------------------------------
    # Issue detection
    # ------------------------------------------------------------------

    def detect_issues(
        self,
        account: AccountInfo,
        posts: list[PostWithInsights],
    ) -> list[InstagramIssue]:
        issues: list[InstagramIssue] = []

        # Spam: more than 3 posts in a single day
        days: dict[str, int] = {}
        for pw in posts:
            day = (pw.post.timestamp or "")[:10]
            if day:
                days[day] = days.get(day, 0) + 1
        for day, count in days.items():
            if count > 3:
                issues.append(InstagramIssue(
                    code="spam_posting",
                    title="Spam de publicaciones",
                    severity="HIGH",
                    detail=f"{count} posts el {day}. Max recomendado: 2/día.",
                ))

        # Duplicates: same caption prefix (first 50 chars)
        seen_captions: dict[str, int] = {}
        for pw in posts:
            cap = (pw.post.caption or "")[:50].strip()
            if cap:
                seen_captions[cap] = seen_captions.get(cap, 0) + 1
        dupes = sum(1 for v in seen_captions.values() if v > 1)
        if dupes > 0:
            issues.append(InstagramIssue(
                code="duplicate_content",
                title="Contenido duplicado",
                severity="HIGH",
                detail=f"{dupes} captions repetidos. Instagram reduce alcance de duplicados.",
            ))

        # No Reels
        has_reels = any(pw.post.media_type in ("VIDEO", "REEL") for pw in posts)
        if not has_reels and len(posts) >= 5:
            issues.append(InstagramIssue(
                code="no_reels",
                title="Sin Reels",
                severity="MEDIUM",
                detail="0 Reels publicados. Instagram empuja Reels 3-5x más que fotos.",
            ))

        # Low reach
        reaches = [pw.insights.reach for pw in posts if pw.insights.reach > 0]
        if reaches:
            avg_reach = sum(reaches) / len(reaches)
            if avg_reach < 50:
                issues.append(InstagramIssue(
                    code="low_reach",
                    title="Alcance bajo",
                    severity="MEDIUM",
                    detail=f"Promedio {avg_reach:.0f} reach/post. Los posts no llegan a nadie.",
                ))

        # No saves
        total_saves = sum(pw.insights.saved for pw in posts[:10])
        if total_saves == 0 and len(posts) >= 5:
            issues.append(InstagramIssue(
                code="no_saves",
                title="0 saves",
                severity="MEDIUM",
                detail="Ningún post guardado. Publica tips/datos que la gente quiera guardar.",
            ))

        # No shares
        total_shares = sum(pw.insights.shares for pw in posts[:10])
        if total_shares == 0 and len(posts) >= 5:
            issues.append(InstagramIssue(
                code="no_shares",
                title="0 shares",
                severity="MEDIUM",
                detail="Ningún post compartido. Contenido controversial/útil genera shares.",
            ))

        # Zero comments
        total_comments = sum(pw.post.comments_count for pw in posts[:10])
        if total_comments == 0 and len(posts) >= 5:
            issues.append(InstagramIssue(
                code="zero_comments",
                title="0 comentarios",
                severity="MEDIUM",
                detail="Sin comentarios. Agrega CTAs y preguntas en los captions.",
            ))

        # Low followers
        if account.followers_count < 500:
            issues.append(InstagramIssue(
                code="low_followers",
                title="Pocos followers",
                severity="LOW",
                detail=f"Solo {account.followers_count} followers. El algoritmo necesita ~500+ para tracción.",
            ))

        return issues

    # ------------------------------------------------------------------
    # Full analysis
    # ------------------------------------------------------------------

    async def full_analysis(self) -> InstagramReport:
        if not self._configured():
            return InstagramReport(
                account=AccountInfo(ig_id=""),
                reach=AccountReach(),
            )

        account = await self.fetch_account_info()
        raw_posts = await self.fetch_recent_posts(limit=25)
        reach = await self.fetch_account_reach(days=28)

        # Fetch insights for each post
        posts_with_insights: list[PostWithInsights] = []
        for post in raw_posts:
            insights = await self.fetch_post_insights(post.media_id)
            posts_with_insights.append(PostWithInsights(post=post, insights=insights))

        issues = self.detect_issues(account, posts_with_insights)

        # Aggregate metrics
        total_reach = sum(pw.insights.reach for pw in posts_with_insights)
        total_likes = sum(pw.post.like_count for pw in posts_with_insights)
        total_comments = sum(pw.post.comments_count for pw in posts_with_insights)
        total_saves = sum(pw.insights.saved for pw in posts_with_insights)
        total_shares = sum(pw.insights.shares for pw in posts_with_insights)
        total_eng = total_likes + total_comments + total_saves + total_shares
        count = len(posts_with_insights)

        aggregate = {
            "posts_analyzed": count,
            "total_reach": total_reach,
            "total_likes": total_likes,
            "total_comments": total_comments,
            "total_saves": total_saves,
            "total_shares": total_shares,
            "total_engagement": total_eng,
            "avg_reach_per_post": round(total_reach / count, 1) if count else 0,
            "avg_engagement_rate": round(total_eng / total_reach * 100, 1) if total_reach else 0,
        }

        return InstagramReport(
            account=account,
            reach=reach,
            posts=posts_with_insights,
            issues=issues,
            aggregate=aggregate,
        )

    # ------------------------------------------------------------------
    # Save daily snapshot
    # ------------------------------------------------------------------

    async def save_daily_snapshot(self, report: InstagramReport) -> None:
        """Persist a daily snapshot for historical tracking."""
        import json

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        issues_json = json.dumps(
            [i.model_dump(mode="json") for i in report.issues],
            ensure_ascii=False,
        )
        async with _connect() as conn:
            await conn.execute(
                """
                INSERT OR REPLACE INTO ig_daily_snapshots
                    (snapshot_date, followers, total_reach, posts_published, avg_engagement_rate, issues_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    today,
                    report.account.followers_count,
                    report.reach.total_reach,
                    report.account.media_count,
                    report.aggregate.get("avg_engagement_rate", 0.0),
                    issues_json,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            await conn.commit()
