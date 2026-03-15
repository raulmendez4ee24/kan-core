#!/usr/bin/env python3
from __future__ import annotations

import asyncio
from pathlib import Path
import sys

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from brain.asset_manager import AssetManager
from brain.brand_director import ContentPost
from brain.content_publisher import ContentPublisher
from config.env_loader import load_environment

TEST_CAPTION = "Test de publicación automática — KAN Logic 🤖"
TEST_HASHTAGS = ["#kanlogic", "#automatizacion", "#ia"]


def _build_post() -> ContentPost:
    caption = f"{TEST_CAPTION}\n\n{' '.join(TEST_HASHTAGS)}"
    return ContentPost(
        post_id="instagram-test-cloudinary",
        day_index=0,
        platform="instagram",
        pillar="test",
        format="static",
        topic="Test de publicación automática",
        hook=TEST_CAPTION,
        full_script=TEST_CAPTION,
        visual_direction="Imagen de prueba generada y subida a Cloudinary para validar publicación automática.",
        caption=caption,
        hashtags=TEST_HASHTAGS,
        best_posting_time="00:00",
        cta="N/A",
    )


async def _run() -> int:
    load_environment(context="test_instagram_publish")

    post = _build_post()
    asset_manager = AssetManager()
    publisher = ContentPublisher()

    try:
        public_image_url = await asset_manager.generate_post_image(post)
        print(f"public_image_url={public_image_url}")
        post = post.model_copy(update={"media_url": public_image_url})
        media_id = await publisher.publish_instagram(post)
    except httpx.HTTPStatusError as exc:
        error_body = exc.response.text if exc.response is not None else str(exc)
        print(f"Error publicando en Instagram: {error_body}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Error publicando en Instagram: {exc}", file=sys.stderr)
        return 1

    print(f"media_id={media_id}")
    return 0


def main() -> int:
    return asyncio.run(_run())


if __name__ == "__main__":
    raise SystemExit(main())
