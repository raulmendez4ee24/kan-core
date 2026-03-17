#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from brain.agents.quality_control_agent import ImagePipeline
from brain.asset_manager import AssetManager
from brain.brand_director import BRAND_TZ, BrandDirector
from brain.content_publisher import ContentPublisher


def _load_env() -> None:
    for name in (".env.secrets", ".env"):
        env_path = ROOT / name
        if not env_path.exists():
            continue
        for raw_line in env_path.read_text().splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


class _PipelineAssetManagerAdapter:
    def __init__(self, asset_manager: AssetManager) -> None:
        self.asset_manager = asset_manager
        self.pipeline = ImagePipeline(asset_manager)
        self.last_pipeline_result: dict[str, Any] | None = None

    async def generate_post_image(
        self,
        *,
        topic: str,
        hook_text: str,
        style_preset: str = "premium",
        format: str = "square",
        vertical: str | None = None,
        content_type: str = "general",
        include_logo: bool = True,
        asset_id: str | None = None,
    ) -> str:
        result = await self.pipeline.generate_and_publish(
            topic=topic,
            hook_text=hook_text,
            style_preset=style_preset,
            vertical=vertical,
            content_type=content_type,
            platform="instagram",
        )
        self.last_pipeline_result = {
            **result,
            "style_preset": style_preset,
            "format": format,
            "include_logo": include_logo,
            "asset_id": asset_id,
        }
        published_url = str(result.get("published_url") or "").strip()
        if not published_url:
            raise RuntimeError(f"ImagePipeline did not return a published_url: {result}")
        return published_url


async def _main() -> None:
    _load_env()

    instagram_handle = str(os.getenv("BRAND_DIRECTOR_INSTAGRAM_HANDLE") or "kanialogic").strip()
    website_url = str(os.getenv("BRAND_DIRECTOR_WEBSITE_URL") or "").strip()
    hook_override = str(os.getenv("BRAND_DIRECTOR_TEST_HOOK") or "").strip()
    topic_override = str(os.getenv("BRAND_DIRECTOR_TEST_TOPIC") or "").strip()

    asset_manager = AssetManager()
    content_publisher = ContentPublisher()
    pipeline_adapter = _PipelineAssetManagerAdapter(asset_manager)
    director = BrandDirector(
        asset_manager=pipeline_adapter,
        content_publisher=content_publisher,
    )

    reference_date = None
    probe_date = datetime.now(BRAND_TZ).date()
    for _ in range(7):
        plan = await director.generate_weekly_content_plan(
            instagram_handle=instagram_handle,
            website_url=website_url,
            reference_date=probe_date,
        )
        week_start = probe_date - timedelta(days=probe_date.weekday())
        candidate = plan[(probe_date - week_start).days % len(plan)]
        if candidate.platform == "instagram":
            reference_date = probe_date
            break
        probe_date += timedelta(days=1)
    if reference_date is None:
        raise RuntimeError("Could not find an Instagram post in the next 7 days")

    week_start = reference_date - timedelta(days=reference_date.weekday())
    plan = await director._load_weekly_plan(week_start=week_start)
    candidate = plan[(reference_date - week_start).days % len(plan)]
    if hook_override or topic_override:
        candidate = candidate.model_copy(
            update={
                "hook": hook_override or candidate.hook,
                "topic": topic_override or candidate.topic,
                "full_script": candidate.full_script.replace(candidate.hook, hook_override or candidate.hook, 1),
                "caption": candidate.caption.replace(candidate.hook, hook_override or candidate.hook, 1),
            }
        )

    original_load_weekly_plan = director._load_weekly_plan

    async def _patched_load_weekly_plan(*, week_start: Any) -> list[Any]:
        plan_items = await original_load_weekly_plan(week_start=week_start)
        if not (hook_override or topic_override):
            return plan_items
        target_index = (reference_date - week_start).days % len(plan_items)
        plan_items[target_index] = candidate
        return plan_items

    director._load_weekly_plan = _patched_load_weekly_plan  # type: ignore[method-assign]

    try:
        post = await director.generate_daily_post(
            reference_date=reference_date,
            instagram_handle=instagram_handle,
            website_url=website_url,
        )
    except Exception as exc:
        pipeline_result = pipeline_adapter.last_pipeline_result or {}
        print(f"post topic: {candidate.topic}")
        print(f"hook: {candidate.hook}")
        print(f"style_preset: {pipeline_result.get('style_preset', 'premium')}")
        print(f"vertical: {candidate.vertical or 'general'}")
        print("cloudinary_url: none")
        print("scheduled_time: none")
        print(f"qc status: {pipeline_result.get('status', 'failed')}")
        print(f"qc score: {pipeline_result.get('score', 'unknown')}")
        print(f"error: {exc}")
        return

    scheduled = await content_publisher.list_scheduled_posts(limit=25)
    matched = next((item for item in reversed(scheduled) if item.post.post_id == post.post_id), None)
    if matched is None:
        raise RuntimeError(f"No scheduled post record found for post_id={post.post_id}")

    local_publish_at = datetime.fromisoformat(matched.publish_at).astimezone(BRAND_TZ)
    pipeline_result = pipeline_adapter.last_pipeline_result or {}

    print(f"post topic: {post.topic}")
    print(f"hook: {post.hook}")
    print(f"style_preset: {pipeline_result.get('style_preset', 'premium')}")
    print(f"vertical: {post.vertical or 'general'}")
    print(f"cloudinary_url: {post.media_url}")
    print(f"scheduled_time: {local_publish_at.isoformat()}")
    print(f"qc status: {pipeline_result.get('status', 'unknown')}")
    print(f"qc score: {pipeline_result.get('score', 'unknown')}")


if __name__ == "__main__":
    asyncio.run(_main())
