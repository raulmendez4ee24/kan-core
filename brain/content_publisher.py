from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

import aiosqlite
import httpx
from pydantic import BaseModel, ConfigDict

from brain.brand_director import ContentPost

logger = logging.getLogger("kan_core.content_publisher")
GRAPH_BASE_URL = "https://graph.facebook.com"
GRAPH_VERSION = "v22.0"


def _publisher_db_path() -> Path:
    configured = str(os.getenv("CONTENT_PUBLISHER_DB_PATH") or "").strip()
    if configured:
        path = Path(configured)
    else:
        path = Path(__file__).resolve().parents[1] / "data" / "content_publisher.sqlite3"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _schedule_id(post: ContentPost, publish_at: datetime) -> str:
    seed = f"{post.post_id}|{publish_at.astimezone(timezone.utc).isoformat()}"
    return hashlib.sha256(seed.encode()).hexdigest()[:24]


class ScheduledPostRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    schedule_id: str
    post: ContentPost
    publish_at: str
    status: str
    media_id: str | None = None
    error: str | None = None
    created_at: str
    updated_at: str


RequestFn = Callable[[str, str, dict[str, Any]], Awaitable[dict[str, Any] | None]]


class ContentPublisher:
    def __init__(
        self,
        *,
        db_path: str | Path | None = None,
        requester: RequestFn | None = None,
        poll_interval_seconds: float = 5.0,
        poll_timeout_seconds: float = 30.0,
        sleep_fn: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.db_path = Path(db_path) if db_path else _publisher_db_path()
        self.requester = requester
        self.poll_interval_seconds = poll_interval_seconds
        self.poll_timeout_seconds = poll_timeout_seconds
        self.sleep_fn = sleep_fn

    async def initialize(self) -> None:
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS scheduled_posts (
                    schedule_id TEXT PRIMARY KEY,
                    post_json TEXT NOT NULL,
                    publish_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    media_id TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            await conn.commit()

    async def _request(self, method: str, path: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        if self.requester is not None:
            return await self.requester(method, path, payload)

        url = f"{GRAPH_BASE_URL.rstrip('/')}/{GRAPH_VERSION}/{path.lstrip('/')}"
        async with httpx.AsyncClient(timeout=30.0) as client:
            if method.upper() == "GET":
                response = await client.request(method.upper(), url, params=payload)
            else:
                response = await client.request(method.upper(), url, data=payload)
            response.raise_for_status()
            if not response.content:
                return {}
            return response.json()

    def _account_id(self) -> str:
        return str(os.getenv("INSTAGRAM_ACCOUNT_ID") or "").strip()

    def _access_token(self) -> str:
        return str(os.getenv("META_ACCESS_TOKEN") or "").strip()

    def _infer_media_payload(self, post: ContentPost) -> dict[str, Any]:
        if not post.media_url:
            raise ValueError("ContentPost.media_url is required to publish")

        payload: dict[str, Any] = {
            "caption": post.caption,
        }
        content_format = post.format.lower().strip()
        media_url = str(post.media_url).strip()
        if content_format == "reel":
            payload["media_type"] = "REELS"
            payload["video_url"] = media_url
            if post.thumbnail_url:
                payload["thumb_offset"] = "0"
        elif content_format == "story":
            payload["media_type"] = "STORIES"
            if media_url.lower().endswith((".mp4", ".mov", ".m4v")):
                payload["video_url"] = media_url
            else:
                payload["image_url"] = media_url
        else:
            payload["image_url"] = media_url
        return payload

    async def _wait_until_media_ready(self, creation_id: str, access_token: str) -> None:
        interval = max(self.poll_interval_seconds, 0.001)
        attempts = max(1, int(self.poll_timeout_seconds / interval))
        for attempt in range(attempts):
            status_payload = {
                "fields": "status_code",
                "access_token": access_token,
            }
            status_response = await self._request("GET", creation_id, status_payload) or {}
            status_code = str(status_response.get("status_code") or "").strip().upper()
            if status_code == "FINISHED":
                return
            if status_code == "ERROR":
                raise RuntimeError(f"Instagram media container {creation_id} returned ERROR")
            if attempt < attempts - 1:
                await self.sleep_fn(interval)
        raise RuntimeError(
            f"Instagram media container {creation_id} was not ready within {self.poll_timeout_seconds:.0f}s"
        )

    async def publish_instagram(self, post: ContentPost) -> str:
        account_id = self._account_id()
        access_token = self._access_token()
        if not account_id or not access_token:
            raise RuntimeError("INSTAGRAM_ACCOUNT_ID and META_ACCESS_TOKEN are required")

        creation_payload = self._infer_media_payload(post)
        creation_payload["access_token"] = access_token
        created = await self._request("POST", f"{account_id}/media", creation_payload) or {}
        creation_id = str(created.get("id") or "").strip()
        if not creation_id:
            raise RuntimeError("Instagram media creation did not return an id")

        await self._wait_until_media_ready(creation_id, access_token)

        publish_payload = {"creation_id": creation_id, "access_token": access_token}
        published = await self._request("POST", f"{account_id}/media_publish", publish_payload) or {}
        media_id = str(published.get("id") or creation_id).strip()
        if not media_id:
            raise RuntimeError("Instagram publish did not return a media id")
        return media_id

    async def publish_to_tiktok(self, post: ContentPost) -> str:
        # TODO: TikTok API publishing requires business verification and app review.
        raise NotImplementedError("TikTok publishing is not enabled yet")

    # ------------------------------------------------------------------
    # Cadence & duplicate guards
    # ------------------------------------------------------------------

    def _max_posts_per_day(self) -> int:
        try:
            return max(1, int(os.getenv("MAX_INSTAGRAM_POSTS_PER_DAY", "2")))
        except (TypeError, ValueError):
            return 2

    async def _posts_on_date(self, target_date: str) -> int:
        """Count scheduled + published posts for a given date (YYYY-MM-DD)."""
        async with aiosqlite.connect(self.db_path) as conn:
            cursor = await conn.execute(
                """
                SELECT COUNT(*) FROM scheduled_posts
                WHERE status IN ('scheduled', 'published')
                  AND publish_at LIKE ?
                """,
                (f"{target_date}%",),
            )
            row = await cursor.fetchone()
            await cursor.close()
        return int(row[0]) if row else 0

    async def _caption_exists_on_date(self, post_id: str, caption: str, target_date: str) -> bool:
        """Check if a different post with the same caption already exists for the date."""
        prefix = (caption or "")[:60].strip()
        if not prefix:
            return False
        async with aiosqlite.connect(self.db_path) as conn:
            cursor = await conn.execute(
                """
                SELECT COUNT(*) FROM scheduled_posts
                WHERE status IN ('scheduled', 'published')
                  AND publish_at LIKE ?
                  AND SUBSTR(json_extract(post_json, '$.caption'), 1, 60) = ?
                  AND json_extract(post_json, '$.post_id') != ?
                """,
                (f"{target_date}%", prefix, post_id),
            )
            row = await cursor.fetchone()
            await cursor.close()
        return bool(row and int(row[0]) > 0)

    async def schedule_post(self, post: ContentPost, publish_at: datetime) -> ScheduledPostRecord:
        await self.initialize()
        timestamp = publish_at.astimezone(timezone.utc)
        target_date = timestamp.strftime("%Y-%m-%d")

        # Cadence guard: max posts per day
        day_count = await self._posts_on_date(target_date)
        if day_count >= self._max_posts_per_day():
            logger.warning(
                "content_publisher: cadence limit reached (%d/%d) for %s, skipping %s",
                day_count, self._max_posts_per_day(), target_date, post.post_id,
            )
            return ScheduledPostRecord(
                schedule_id=_schedule_id(post, timestamp),
                post=post,
                publish_at=timestamp.isoformat(),
                status="skipped_cadence",
                created_at=_utc_now().isoformat(),
                updated_at=_utc_now().isoformat(),
            )

        # Duplicate guard: same caption prefix from a different post
        if await self._caption_exists_on_date(post.post_id, post.caption, target_date):
            logger.warning(
                "content_publisher: duplicate caption detected for %s on %s, skipping",
                post.post_id, target_date,
            )
            return ScheduledPostRecord(
                schedule_id=_schedule_id(post, timestamp),
                post=post,
                publish_at=timestamp.isoformat(),
                status="skipped_duplicate",
                created_at=_utc_now().isoformat(),
                updated_at=_utc_now().isoformat(),
            )

        record = ScheduledPostRecord(
            schedule_id=_schedule_id(post, timestamp),
            post=post,
            publish_at=timestamp.isoformat(),
            status="scheduled",
            created_at=_utc_now().isoformat(),
            updated_at=_utc_now().isoformat(),
        )
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute(
                """
                INSERT INTO scheduled_posts (
                    schedule_id, post_json, publish_at, status, media_id, error, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(schedule_id) DO UPDATE SET
                    post_json = excluded.post_json,
                    publish_at = excluded.publish_at,
                    status = excluded.status,
                    media_id = excluded.media_id,
                    error = excluded.error,
                    updated_at = excluded.updated_at
                """,
                (
                    record.schedule_id,
                    json.dumps(post.model_dump(mode="json"), ensure_ascii=False),
                    record.publish_at,
                    record.status,
                    record.media_id,
                    record.error,
                    record.created_at,
                    record.updated_at,
                ),
            )
            await conn.commit()
        return record

    async def list_scheduled_posts(self, *, limit: int = 50) -> list[ScheduledPostRecord]:
        await self.initialize()
        async with aiosqlite.connect(self.db_path) as conn:
            cursor = await conn.execute(
                """
                SELECT schedule_id, post_json, publish_at, status, media_id, error, created_at, updated_at
                FROM scheduled_posts
                ORDER BY publish_at ASC
                LIMIT ?
                """,
                (limit,),
            )
            rows = await cursor.fetchall()
            await cursor.close()
        records: list[ScheduledPostRecord] = []
        for row in rows:
            records.append(
                ScheduledPostRecord(
                    schedule_id=str(row[0]),
                    post=ContentPost.model_validate(json.loads(str(row[1]))),
                    publish_at=str(row[2]),
                    status=str(row[3]),
                    media_id=str(row[4]) if row[4] is not None else None,
                    error=str(row[5]) if row[5] is not None else None,
                    created_at=str(row[6]),
                    updated_at=str(row[7]),
                )
            )
        return records

    async def check_scheduled_posts(self, *, now: datetime | None = None) -> list[ScheduledPostRecord]:
        await self.initialize()
        cutoff = (now or _utc_now()).astimezone(timezone.utc).isoformat()
        async with aiosqlite.connect(self.db_path) as conn:
            cursor = await conn.execute(
                """
                SELECT schedule_id, post_json, publish_at, status, media_id, error, created_at, updated_at
                FROM scheduled_posts
                WHERE status = 'scheduled' AND publish_at <= ?
                ORDER BY publish_at ASC
                """,
                (cutoff,),
            )
            rows = await cursor.fetchall()
            await cursor.close()

            published: list[ScheduledPostRecord] = []
            published_today = 0
            today_str = _utc_now().strftime("%Y-%m-%d")
            max_per_day = self._max_posts_per_day()

            # Count only already-published posts today (not scheduled ones in the queue)
            count_cursor = await conn.execute(
                "SELECT COUNT(*) FROM scheduled_posts WHERE status = 'published' AND publish_at LIKE ?",
                (f"{today_str}%",),
            )
            count_row = await count_cursor.fetchone()
            already_published_today = int(count_row[0]) if count_row else 0
            await count_cursor.close()

            for row in rows:
                schedule_id = str(row[0])
                post = ContentPost.model_validate(json.loads(str(row[1])))
                created_at = str(row[6])

                # Cadence guard at publish time
                if already_published_today + published_today >= max_per_day:
                    from datetime import timedelta
                    tomorrow = (_utc_now() + timedelta(days=1)).replace(
                        hour=14, minute=0, second=0, microsecond=0,
                    )
                    await conn.execute(
                        "UPDATE scheduled_posts SET publish_at = ?, updated_at = ? WHERE schedule_id = ?",
                        (tomorrow.isoformat(), _utc_now().isoformat(), schedule_id),
                    )
                    logger.info("content_publisher: postponed %s to %s (cadence limit)", schedule_id, tomorrow.isoformat())
                    continue

                current_status = "published"
                media_id: str | None = None
                error: str | None = None
                try:
                    media_id = await self.publish_instagram(post)
                    published_today += 1
                except Exception as exc:
                    current_status = "failed"
                    error = str(exc)
                    logger.exception("Scheduled Instagram publish failed for %s", schedule_id)
                updated_at = _utc_now().isoformat()
                await conn.execute(
                    """
                    UPDATE scheduled_posts
                    SET status = ?, media_id = ?, error = ?, updated_at = ?
                    WHERE schedule_id = ?
                    """,
                    (current_status, media_id, error, updated_at, schedule_id),
                )
                published.append(
                    ScheduledPostRecord(
                        schedule_id=schedule_id,
                        post=post,
                        publish_at=str(row[2]),
                        status=current_status,
                        media_id=media_id,
                        error=error,
                        created_at=created_at,
                        updated_at=updated_at,
                    )
                )
            await conn.commit()
        return published
