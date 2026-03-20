from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from brain.brand_director import ContentPost
from brain.content_publisher import ContentPublisher, ScheduledPostRecord
from brain.revenue_brain import RevenueBrain
from brain.revenue_scheduler import RevenueBrainScheduler
from main import app


def _post(*, post_format: str, media_url: str) -> ContentPost:
    return ContentPost(
        post_id=f"post-{post_format}",
        day_index=0,
        platform="instagram",
        pillar="oferta",
        format=post_format,
        topic="Demo",
        hook="Hook",
        full_script="Script",
        visual_direction="Visual",
        caption=f"Caption for {post_format}",
        hashtags=["#demo"],
        best_posting_time="09:00",
        cta="CTA",
        media_url=media_url,
    )


def test_publish_instagram_handles_image_reel_and_story(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CONTENT_PUBLISHER_DB_PATH", str(tmp_path / "publisher.sqlite3"))
    monkeypatch.setenv("INSTAGRAM_ACCOUNT_ID", "17890000000000000")
    monkeypatch.setenv("META_ACCESS_TOKEN", "meta-token")

    calls: list[tuple[str, str, dict[str, object]]] = []

    async def _request(method: str, path: str, payload: dict[str, object]):
        calls.append((method, path, payload))
        if path.endswith("/media"):
            return {"id": f"creation-{len(calls)}"}
        if method == "GET":
            return {"status_code": "FINISHED"}
        return {"id": f"media-{len(calls)}"}

    publisher = ContentPublisher(
        db_path=tmp_path / "publisher.sqlite3",
        requester=_request,
        poll_interval_seconds=0.01,
        poll_timeout_seconds=0.05,
    )

    async def _run() -> list[str]:
        return [
            await publisher.publish_instagram(_post(post_format="static", media_url="https://cdn.example.com/image.jpg")),
            await publisher.publish_instagram(_post(post_format="reel", media_url="https://cdn.example.com/video.mp4")),
            await publisher.publish_instagram(_post(post_format="story", media_url="https://cdn.example.com/story.jpg")),
        ]

    media_ids = asyncio.run(_run())

    assert media_ids == ["media-3", "media-6", "media-9"]
    image_creation = calls[0]
    image_status = calls[1]
    image_publish = calls[2]
    reel_creation = calls[3]
    story_creation = calls[6]

    assert image_creation[1] == "17890000000000000/media"
    assert image_creation[2]["image_url"] == "https://cdn.example.com/image.jpg"
    assert image_creation[2]["caption"] == "Caption for static"
    assert image_status[0] == "GET"
    assert image_status[1] == "creation-1"
    assert image_publish[1] == "17890000000000000/media_publish"
    assert reel_creation[2]["media_type"] == "REELS"
    assert reel_creation[2]["video_url"] == "https://cdn.example.com/video.mp4"
    assert story_creation[2]["media_type"] == "STORIES"
    assert story_creation[2]["image_url"] == "https://cdn.example.com/story.jpg"


def test_schedule_post_and_check_scheduled_posts_updates_status(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CONTENT_PUBLISHER_DB_PATH", str(tmp_path / "publisher.sqlite3"))
    monkeypatch.setenv("INSTAGRAM_ACCOUNT_ID", "17890000000000000")
    monkeypatch.setenv("META_ACCESS_TOKEN", "meta-token")

    async def _request(method: str, path: str, payload: dict[str, object]):
        if path.endswith("/media"):
            return {"id": "creation-1"}
        if method == "GET":
            return {"status_code": "FINISHED"}
        return {"id": "media-1"}

    publisher = ContentPublisher(
        db_path=tmp_path / "publisher.sqlite3",
        requester=_request,
        poll_interval_seconds=0.01,
        poll_timeout_seconds=0.05,
    )

    async def _run() -> None:
        scheduled = await publisher.schedule_post(
            _post(post_format="static", media_url="https://cdn.example.com/image.jpg"),
            datetime.now(timezone.utc) - timedelta(minutes=5),
        )
        assert scheduled.status == "scheduled"

        processed = await publisher.check_scheduled_posts(now=datetime.now(timezone.utc))
        assert len(processed) == 1
        assert processed[0].status == "published"
        assert processed[0].media_id == "media-1"

        rows = await publisher.list_scheduled_posts(limit=10)
        assert len(rows) == 1
        assert rows[0].status == "published"
        assert rows[0].media_id == "media-1"

    asyncio.run(_run())


def test_check_scheduled_posts_marks_failed_when_publish_errors(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CONTENT_PUBLISHER_DB_PATH", str(tmp_path / "publisher.sqlite3"))
    monkeypatch.setenv("INSTAGRAM_ACCOUNT_ID", "17890000000000000")
    monkeypatch.setenv("META_ACCESS_TOKEN", "meta-token")

    async def _request(method: str, path: str, payload: dict[str, object]):
        raise RuntimeError("graph down")

    publisher = ContentPublisher(
        db_path=tmp_path / "publisher.sqlite3",
        requester=_request,
        poll_interval_seconds=0.01,
        poll_timeout_seconds=0.05,
    )

    async def _run() -> None:
        await publisher.schedule_post(
            _post(post_format="story", media_url="https://cdn.example.com/story.jpg"),
            datetime.now(timezone.utc) - timedelta(minutes=1),
        )
        processed = await publisher.check_scheduled_posts(now=datetime.now(timezone.utc))
        assert len(processed) == 1
        assert processed[0].status == "failed"
        assert "graph down" in str(processed[0].error)

    asyncio.run(_run())


def test_scheduler_runs_hourly_content_publish_queue(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CONTENT_PUBLISHER_DB_PATH", str(tmp_path / "publisher.sqlite3"))
    scheduler = RevenueBrainScheduler(RevenueBrain())

    async def _fake_check():
        return [
            ScheduledPostRecord(
                schedule_id="scheduled-1",
                post=_post(post_format="static", media_url="https://cdn.example.com/image.jpg"),
                publish_at=datetime.now(timezone.utc).isoformat(),
                status="published",
                media_id="media-1",
                error=None,
                created_at=datetime.now(timezone.utc).isoformat(),
                updated_at=datetime.now(timezone.utc).isoformat(),
            )
        ]

    monkeypatch.setattr(scheduler.content_publisher, "check_scheduled_posts", _fake_check)

    result = asyncio.run(scheduler.run_content_publish_queue())

    assert len(result) == 1
    assert result[0]["status"] == "published"
    assert scheduler.last_published_content is not None


def test_content_scheduled_endpoint_lists_scheduled_and_published(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CONTENT_PUBLISHER_DB_PATH", str(tmp_path / "publisher.sqlite3"))
    monkeypatch.setenv("CLIENT_TOKENS_JSON", '{"test-client":"test-token"}')
    monkeypatch.setenv("KAN_CLIENT_ID", "test-client")
    monkeypatch.setenv("INSTAGRAM_ACCOUNT_ID", "17890000000000000")
    monkeypatch.setenv("META_ACCESS_TOKEN", "meta-token")

    async def _request(method: str, path: str, payload: dict[str, object]):
        if path.endswith("/media"):
            return {"id": "creation-1"}
        if method == "GET":
            return {"status_code": "FINISHED"}
        return {"id": "media-1"}

    publisher = ContentPublisher(
        db_path=tmp_path / "publisher.sqlite3",
        requester=_request,
        poll_interval_seconds=0.01,
        poll_timeout_seconds=0.05,
    )

    async def _seed() -> None:
        await publisher.schedule_post(
            _post(post_format="static", media_url="https://cdn.example.com/image.jpg"),
            datetime.now(timezone.utc) - timedelta(minutes=5),
        )
        await publisher.schedule_post(
            _post(post_format="story", media_url="https://cdn.example.com/story.jpg"),
            datetime.now(timezone.utc) + timedelta(hours=2),
        )
        await publisher.check_scheduled_posts(now=datetime.now(timezone.utc))

    asyncio.run(_seed())

    with TestClient(app) as client:
        response = client.get(
            "/content/scheduled",
            headers={"X-Client-Token": "test-token"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 2
    statuses = {item["status"] for item in payload["items"]}
    assert statuses == {"scheduled", "published"}


def test_publish_instagram_raises_when_container_reports_error(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CONTENT_PUBLISHER_DB_PATH", str(tmp_path / "publisher.sqlite3"))
    monkeypatch.setenv("INSTAGRAM_ACCOUNT_ID", "17890000000000000")
    monkeypatch.setenv("META_ACCESS_TOKEN", "meta-token")

    async def _request(method: str, path: str, payload: dict[str, object]):
        if path.endswith("/media"):
            return {"id": "creation-error"}
        if method == "GET":
            return {"status_code": "ERROR"}
        return {"id": "media-1"}

    publisher = ContentPublisher(
        db_path=tmp_path / "publisher.sqlite3",
        requester=_request,
        poll_interval_seconds=0.01,
        poll_timeout_seconds=0.05,
    )

    async def _run() -> None:
        await publisher.publish_instagram(
            _post(post_format="static", media_url="https://cdn.example.com/image.jpg")
        )

    try:
        asyncio.run(_run())
    except RuntimeError as exc:
        assert "returned ERROR" in str(exc)
    else:
        raise AssertionError("publish_instagram should raise when status_code is ERROR")


def test_publish_instagram_raises_on_container_timeout(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CONTENT_PUBLISHER_DB_PATH", str(tmp_path / "publisher.sqlite3"))
    monkeypatch.setenv("INSTAGRAM_ACCOUNT_ID", "17890000000000000")
    monkeypatch.setenv("META_ACCESS_TOKEN", "meta-token")

    sleep_calls: list[float] = []

    async def _request(method: str, path: str, payload: dict[str, object]):
        if path.endswith("/media"):
            return {"id": "creation-timeout"}
        if method == "GET":
            return {"status_code": "IN_PROGRESS"}
        return {"id": "media-1"}

    async def _sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    publisher = ContentPublisher(
        db_path=tmp_path / "publisher.sqlite3",
        requester=_request,
        poll_interval_seconds=0.01,
        poll_timeout_seconds=0.03,
        sleep_fn=_sleep,
    )

    async def _run() -> None:
        await publisher.publish_instagram(
            _post(post_format="static", media_url="https://cdn.example.com/image.jpg")
        )

    try:
        asyncio.run(_run())
    except RuntimeError as exc:
        assert "was not ready within" in str(exc)
        assert sleep_calls
    else:
        raise AssertionError("publish_instagram should raise on timeout")


def test_publish_to_tiktok_stub() -> None:
    publisher = ContentPublisher()

    async def _run() -> None:
        await publisher.publish_to_tiktok(
            _post(post_format="reel", media_url="https://cdn.example.com/video.mp4")
        )

    try:
        asyncio.run(_run())
    except NotImplementedError as exc:
        assert "TikTok publishing is not enabled yet" in str(exc)
    else:
        raise AssertionError("publish_to_tiktok should raise NotImplementedError")
