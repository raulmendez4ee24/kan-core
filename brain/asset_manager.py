from __future__ import annotations

import asyncio
import hashlib
import logging
import os
from pathlib import Path
from tempfile import gettempdir
from textwrap import fill
from time import time
from typing import TYPE_CHECKING, Any, Awaitable, Callable

import httpx
from PIL import Image, ImageDraw, ImageFont

if TYPE_CHECKING:
    from brain.brand_director import ContentPost

logger = logging.getLogger("kan_core.asset_manager")

_BACKGROUND = "#1a1a2e"
_ACCENT = "#e94560"
_TEXT = "#f5f7ff"
_MUTED = "#b8bfd6"
_DEFAULT_GEMINI_IMAGE_MODEL = "gemini-3-pro-image-preview"


RequestFn = Callable[[str, str, dict[str, Any], dict[str, Any]], Awaitable[dict[str, Any] | None]]


class AssetManager:
    def __init__(
        self,
        *,
        requester: RequestFn | None = None,
    ) -> None:
        self.requester = requester

    def _cloud_name(self) -> str:
        return str(os.getenv("CLOUDINARY_CLOUD_NAME") or "").strip()

    def _api_key(self) -> str:
        return str(os.getenv("CLOUDINARY_API_KEY") or "").strip()

    def _api_secret(self) -> str:
        return str(os.getenv("CLOUDINARY_API_SECRET") or "").strip()

    def _google_api_key(self) -> str:
        return str(os.getenv("GOOGLE_API_KEY") or "").strip()

    def _gemini_image_model(self) -> str:
        return str(os.getenv("GEMINI_IMAGE_MODEL") or _DEFAULT_GEMINI_IMAGE_MODEL).strip()

    def _signature(self, *, public_id: str, timestamp: int) -> str:
        api_secret = self._api_secret()
        if not api_secret:
            raise RuntimeError("CLOUDINARY_API_SECRET is required")
        payload = f"public_id={public_id}&timestamp={timestamp}{api_secret}"
        return hashlib.sha1(payload.encode()).hexdigest()

    async def _request(
        self,
        method: str,
        url: str,
        *,
        data: dict[str, Any],
        files: dict[str, Any],
    ) -> dict[str, Any] | None:
        if self.requester is not None:
            return await self.requester(method, url, data, files)

        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.request(method.upper(), url, data=data, files=files)
            response.raise_for_status()
            if not response.content:
                return {}
            return response.json()

    async def upload_image(self, image_path: str) -> str:
        cloud_name = self._cloud_name()
        api_key = self._api_key()
        api_secret = self._api_secret()
        if not cloud_name or not api_key or not api_secret:
            raise RuntimeError(
                "CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY and CLOUDINARY_API_SECRET are required"
            )

        path = Path(image_path)
        if not path.exists():
            raise FileNotFoundError(f"Image not found: {path}")

        timestamp = int(time())
        public_id = path.stem
        signature = self._signature(public_id=public_id, timestamp=timestamp)
        url = f"https://api.cloudinary.com/v1_1/{cloud_name}/image/upload"
        data = {
            "api_key": api_key,
            "timestamp": str(timestamp),
            "public_id": public_id,
            "signature": signature,
        }
        with path.open("rb") as fh:
            payload = await self._request(
                "POST",
                url,
                data=data,
                files={"file": (path.name, fh, "image/jpeg")},
            )
        secure_url = str((payload or {}).get("secure_url") or "").strip()
        if not secure_url:
            raise RuntimeError("Cloudinary upload did not return secure_url")
        return secure_url

    def _build_gemini_prompt(self, post: ContentPost) -> str:
        vertical = str(post.pillar or "business").strip()
        return (
            "Create a professional square social media image, 1080x1080. "
            f"Topic: {post.topic}. "
            f"Hook: {post.hook}. "
            f"Vertical: {vertical}. "
            "Use KAN Logic brand colors with #1a1a2e as primary background and #e94560 as accent. "
            "Visual direction: modern, tech-forward, premium Mexican business aesthetic, clean composition, "
            "high contrast typography area, subtle depth, no watermark, no UI chrome, no extra logos except a simple brand placeholder area. "
            "The image should feel commercial, social-ready, and polished for Instagram."
        )

    def _render_with_pillow(self, post: ContentPost, output: Path) -> None:
        image = Image.new("RGB", (1080, 1080), color=_BACKGROUND)
        draw = ImageDraw.Draw(image)

        try:
            title_font = ImageFont.truetype("DejaVuSans-Bold.ttf", 72)
            body_font = ImageFont.truetype("DejaVuSans.ttf", 40)
            brand_font = ImageFont.truetype("DejaVuSans-Bold.ttf", 34)
        except Exception:
            title_font = ImageFont.load_default()
            body_font = ImageFont.load_default()
            brand_font = ImageFont.load_default()

        draw.rounded_rectangle((80, 80, 1000, 1000), radius=36, outline=_ACCENT, width=8)
        draw.rectangle((120, 120, 220, 220), fill=_ACCENT)
        draw.text((250, 145), "KAN Logic", fill=_TEXT, font=brand_font)

        wrapped_hook = fill(str(post.hook or "").strip(), width=18)
        wrapped_topic = fill(str(post.topic or "").strip(), width=26)
        wrapped_cta = fill(str(post.cta or "").strip(), width=28)

        draw.text((120, 300), wrapped_hook, fill=_TEXT, font=title_font, spacing=10)
        draw.text((120, 630), wrapped_topic, fill=_MUTED, font=body_font, spacing=8)
        draw.line((120, 790, 960, 790), fill=_ACCENT, width=5)
        draw.text((120, 830), wrapped_cta or "Escríbeme para activarlo.", fill=_ACCENT, font=body_font, spacing=8)

        output.parent.mkdir(parents=True, exist_ok=True)
        image.save(output, format="JPEG", quality=92, optimize=True)

    def _generate_with_google_genai_sync(self, post: ContentPost, output: Path) -> Path:
        api_key = self._google_api_key()
        if not api_key:
            raise RuntimeError("GOOGLE_API_KEY is required for Gemini image generation")

        from google import genai
        from google.genai import types

        client = genai.Client(api_key=api_key)
        response = client.models.generate_images(
            model=self._gemini_image_model(),
            prompt=self._build_gemini_prompt(post),
            config=types.GenerateImagesConfig(
                number_of_images=1,
                aspect_ratio="1:1",
                output_mime_type="image/jpeg",
                image_size="1K",
            ),
        )
        generated_images = list(getattr(response, "generated_images", None) or [])
        if not generated_images:
            raise RuntimeError("Gemini image generation returned no images")
        image = generated_images[0].image
        image_bytes = getattr(image, "image_bytes", None) if image is not None else None
        if not image_bytes:
            raise RuntimeError("Gemini image generation returned no image bytes")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(image_bytes)
        return output

    async def _generate_with_gemini(self, post: ContentPost, output: Path) -> Path:
        return await asyncio.to_thread(self._generate_with_google_genai_sync, post, output)

    async def generate_post_image(self, post: ContentPost) -> str:
        output = Path(gettempdir()) / f"{post.post_id}.jpg"
        try:
            await self._generate_with_gemini(post, output)
            logger.info("Generated post image with Gemini at %s", output)
        except Exception:
            logger.exception("Gemini image generation failed for %s; falling back to Pillow", post.post_id)
            self._render_with_pillow(post, output)
            logger.info("Generated post image with Pillow fallback at %s", output)
        return await self.upload_image(str(output))
