from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
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
_DEFAULT_GEMINI_IMAGE_MODEL = "models/gemini-3-pro-image-preview"
_SORA_BOLD_URL = "https://github.com/google/fonts/raw/main/ofl/sora/Sora%5Bwght%5D.ttf"
_JETBRAINS_MONO_URL = "https://github.com/JetBrains/JetBrainsMono/raw/master/fonts/ttf/JetBrainsMono-Regular.ttf"
_SORA_FONT_NAME = "Sora-Bold.ttf"
_JETBRAINS_MONO_NAME = "JetBrains-Mono-Regular.ttf"
_FONT_DOWNLOAD_ATTEMPTED: set[str] = set()
_EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001F5FF"
    "\U0001F600-\U0001F64F"
    "\U0001F680-\U0001F6FF"
    "\U0001F700-\U0001F77F"
    "\U0001F780-\U0001F7FF"
    "\U0001F800-\U0001F8FF"
    "\U0001F900-\U0001F9FF"
    "\U0001FA00-\U0001FA6F"
    "\U0001FA70-\U0001FAFF"
    "\u2600-\u26FF"
    "\u2700-\u27BF"
    "]+",
    flags=re.UNICODE,
)

BRAND: dict[str, Any] = {
    "name": "KAN Logic",
    "positioning": "automation and growth systems for Mexican businesses",
    "palette": {
        "background": "#1a1a2e",
        "accent": "#e94560",
        "text": "#f5f7ff",
        "muted": "#b8bfd6",
    },
    "voice": [
        "direct",
        "premium",
        "commercial",
        "tech-forward",
        "clean",
        "Mexican business context",
    ],
    "visual_rules": [
        "no watermark",
        "no UI chrome",
        "no fake screenshots",
        "clean composition",
        "leave clean space for text overlay",
        "no text in the image",
    ],
}

STYLE_PRESETS: dict[str, dict[str, Any]] = {
    "premium": {
        "mood": "premium, modern, commercial confidence",
        "scene": "clean tech-forward business environment with aspirational polish",
        "composition": "hero-led framing with strong negative space for overlay",
        "palette_hint": "KAN Logic navy and coral with elevated neutral highlights",
        "objects": ["laptop", "smartphone", "subtle interface glow"],
    },
    "default": {
        "mood": "modern operational excellence",
        "scene": "clean premium business setting with subtle depth",
        "composition": "strong focal subject with clean negative space on the left",
        "palette_hint": "deep navy base with hot coral accent and soft neutral highlights",
        "objects": ["laptop", "smartphone", "abstract automation flows"],
    },
    "clinics": {
        "mood": "trustworthy clinical efficiency",
        "scene": "modern medical consultation environment",
        "composition": "clean sterile framing with negative space for overlay",
        "palette_hint": "blue-white medical palette with restrained KAN coral accents",
        "objects": ["reception desk", "consult room", "tablet"],
    },
    "dental": {
        "mood": "clean precision and confidence",
        "scene": "premium dental clinic environment",
        "composition": "bright professional frame with calm negative space",
        "palette_hint": "fresh green-white dental palette with subtle coral accents",
        "objects": ["dental chair", "reception", "smiling client silhouette"],
    },
    "restaurants": {
        "mood": "busy profitable hospitality",
        "scene": "stylized restaurant service environment",
        "composition": "dynamic service moment with negative space for hook",
        "palette_hint": "warm orange-gold hospitality palette balanced with KAN coral accents",
        "objects": ["table service", "phone orders", "kitchen pass"],
    },
    "spas": {
        "mood": "calm premium wellness",
        "scene": "elegant spa and self-care setting",
        "composition": "soft luxurious frame with calm negative space",
        "palette_hint": "soft purple and cream wellness palette with subtle coral accents",
        "objects": ["towels", "ambient light", "treatment room"],
    },
    "barbershops": {
        "mood": "sharp local authority",
        "scene": "premium barbershop with urban polish",
        "composition": "confident subject framing with clean space for overlay",
        "palette_hint": "charcoal, steel, and warm amber balanced with KAN coral accent",
        "objects": ["barber chair", "mirror lights", "appointment notebook"],
    },
    "real_estate": {
        "mood": "high-value advisory",
        "scene": "premium real estate presentation setting",
        "composition": "architectural framing with sales-focused negative space",
        "palette_hint": "navy, sand, and gold with restrained coral accent",
        "objects": ["modern home", "tablet presentation", "city view"],
    },
}

READY_PROMPTS: dict[str, str] = {
    "lead_capture": "Show a business losing leads due to slow follow-up, then imply an automated recovery system.",
    "offer_launch": "Present a premium service launch visual that feels urgent, polished, and conversion-oriented.",
    "testimonial": "Create a visual that suggests client proof, confidence, and visible business improvement.",
    "objection_breaker": "Depict the contrast between doing everything manually vs automating the critical bottleneck.",
    "case_study": "Show before-and-after operational clarity with a premium business transformation tone.",
    "educational_tip": "Illustrate one practical growth lesson in a clean, high-credibility social format.",
    "booking_push": "Visually nudge the viewer toward scheduling a call, demo, or consultation today.",
    "retargeting": "Create a reminder-style visual for someone who already showed interest but has not acted.",
    "seasonal_promo": "Blend seasonal commercial energy with a concrete automation or website offer.",
    "brand_authority": "Create a founder-brand visual that feels experienced, strategic, and commercially sharp.",
    "workflow_upgrade": "Show operational chaos transformed into a clean automated workflow.",
    "whatsapp_conversion": "Depict WhatsApp as a serious sales channel with immediate response and booked meetings.",
}


RequestFn = Callable[[str, str, dict[str, Any], dict[str, Any]], Awaitable[dict[str, Any] | None]]


def build_image_prompt(
    *,
    topic: str,
    hook_text: str,
    style_preset: str = "premium",
    format: str = "square",
    vertical: str | None = None,
    content_type: str = "general",
    include_logo: bool = True,
) -> str:
    preset_key = str(style_preset or "premium").strip().lower().replace(" ", "_")
    preset = STYLE_PRESETS.get(preset_key) or STYLE_PRESETS["premium"]
    vertical_key = str(vertical or "").strip().lower().replace(" ", "_")
    vertical_preset = STYLE_PRESETS.get(vertical_key) or {}
    content_hint = READY_PROMPTS.get(str(content_type or "general").strip(), "")
    objects = ", ".join(preset.get("objects", []))
    visual_rules = ", ".join(BRAND["visual_rules"])
    voice = ", ".join(BRAND["voice"])
    return (
        f"Create a professional 1080x1080 social media image for {BRAND['name']}. "
        f"Brand positioning: {BRAND['positioning']}. "
        f"Topic: {topic}. "
        f"Core hook theme: {hook_text}. "
        f"Style preset: {preset_key}. "
        f"Format: {format}. "
        f"Vertical: {vertical_key or 'general business'}. "
        f"Content type: {content_type}. "
        f"Mood: {preset['mood']}. "
        f"Scene: {vertical_preset.get('scene', preset['scene'])}. "
        f"Composition: {vertical_preset.get('composition', preset['composition'])}. "
        f"Palette direction: {vertical_preset.get('palette_hint', preset['palette_hint'])}. "
        f"Suggested objects or context: {objects}. "
        f"Content objective: {content_hint or 'Create a compelling general business content visual.'} "
        f"Include logo in final composition: {'yes' if include_logo else 'no'}. "
        f"Brand colors must influence the image, especially {BRAND['palette']['background']} and {BRAND['palette']['accent']}. "
        f"Tone: {voice}. "
        "Make it polished, commercial, premium, and relevant for a modern Mexican business audience. "
        "ABSOLUTELY NO TEXT, WORDS, LETTERS, OR TYPOGRAPHY IN THE IMAGE. "
        "Pure background composition only. Any text in the image will ruin the design. "
        "No emojis, no icons, no symbols, no UI elements, no text overlays, no watermarks. "
        "Leave clean space for text overlay, no text in the image. "
        f"Follow these visual rules: {visual_rules}."
    )


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
        return str(os.getenv("GEMINI_MODEL") or _DEFAULT_GEMINI_IMAGE_MODEL).strip()

    def get_ready_prompt(self, key: str) -> str:
        ready = READY_PROMPTS.get(str(key).strip())
        if not ready:
            raise KeyError(f"Unknown prompt_key: {key}")
        return ready

    def _strip_emojis(self, value: str) -> str:
        cleaned = _EMOJI_RE.sub("", str(value or ""))
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return cleaned

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

    def _resolve_vertical(self, vertical: str | None = None, fallback: str | None = None) -> str:
        raw = str(vertical or fallback or "default").strip().lower().replace(" ", "_")
        aliases = {
            "clinic": "clinics",
            "medical": "clinics",
            "dentist": "dental",
            "dental": "dental",
            "restaurant": "restaurants",
            "spa": "spas",
            "barber": "barbershops",
        }
        normalized = aliases.get(raw, raw)
        return normalized if normalized in STYLE_PRESETS else "default"

    def _build_gemini_prompt(
        self,
        *,
        topic: str,
        hook_text: str,
        style_preset: str = "premium",
        format: str = "square",
        vertical: str | None = None,
        content_type: str = "general",
        include_logo: bool = True,
    ) -> str:
        return build_image_prompt(
            topic=str(topic or "").strip(),
            hook_text=str(hook_text or "").strip(),
            style_preset=style_preset,
            format=format,
            vertical=self._resolve_vertical(vertical),
            content_type=content_type,
            include_logo=include_logo,
        )

    def _font_cache_path(self, filename: str) -> Path:
        return Path(gettempdir()) / filename

    def _ensure_downloaded_font(self, *, url: str, filename: str) -> Path | None:
        font_path = self._font_cache_path(filename)
        if font_path.exists():
            return font_path
        if filename in _FONT_DOWNLOAD_ATTEMPTED:
            return None
        _FONT_DOWNLOAD_ATTEMPTED.add(filename)
        try:
            response = httpx.get(url, timeout=10.0, follow_redirects=True)
            response.raise_for_status()
            font_path.write_bytes(response.content)
            return font_path
        except Exception:
            logger.exception("Could not download font %s; using fallback font", filename)
            return None

    def _ensure_sora_bold_font(self) -> Path | None:
        return self._ensure_downloaded_font(url=_SORA_BOLD_URL, filename=_SORA_FONT_NAME)

    def _ensure_jetbrains_mono_font(self) -> Path | None:
        return self._ensure_downloaded_font(url=_JETBRAINS_MONO_URL, filename=_JETBRAINS_MONO_NAME)

    def _load_font(self, size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        font_path = self._ensure_sora_bold_font() if bold else None
        if font_path is not None:
            try:
                return ImageFont.truetype(str(font_path), size)
            except Exception:
                logger.exception("Could not load downloaded Sora Bold font")
        fallback_candidates = [
            "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf",
            "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
            "/System/Library/Fonts/Supplemental/Helvetica.ttc",
        ]
        for candidate in fallback_candidates:
            try:
                return ImageFont.truetype(candidate, size)
            except Exception:
                continue
        return ImageFont.load_default()

    def _load_mono_font(self, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        font_path = self._ensure_jetbrains_mono_font()
        if font_path is not None:
            try:
                return ImageFont.truetype(str(font_path), size)
            except Exception:
                logger.exception("Could not load downloaded JetBrains Mono font")
        fallback_candidates = [
            "DejaVuSansMono.ttf",
            "/System/Library/Fonts/Supplemental/Courier New.ttf",
            "/System/Library/Fonts/Supplemental/Menlo.ttc",
        ]
        for candidate in fallback_candidates:
            try:
                return ImageFont.truetype(candidate, size)
            except Exception:
                continue
        return ImageFont.load_default()

    def _render_with_pillow(self, output: Path) -> None:
        image = Image.new("RGB", (1080, 1080), color=_BACKGROUND)
        draw = ImageDraw.Draw(image)

        draw.rounded_rectangle((80, 80, 1000, 1000), radius=36, outline=_ACCENT, width=8)
        draw.rectangle((120, 120, 220, 220), fill=_ACCENT)

        output.parent.mkdir(parents=True, exist_ok=True)
        image.save(output, format="JPEG", quality=92, optimize=True)

    def _apply_text_gradient(self, image: Image.Image, *, top: int, bottom: int) -> Image.Image:
        base = image.convert("RGBA")
        overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
        width = base.size[0]
        height = max(1, bottom - top)
        gradient = Image.new("L", (1, height))
        for y in range(height):
            midpoint = height * 0.45
            if y <= midpoint:
                alpha = int(128 * (y / max(1, midpoint)))
            else:
                alpha = int(128 * ((height - y) / max(1, height - midpoint)))
            gradient.putpixel((0, y), max(0, min(128, alpha)))
        alpha_mask = gradient.resize((width, height))
        overlay.paste((0, 0, 0, 0), (0, 0, width, base.size[1]))
        overlay.paste((0, 0, 0, 0), (0, 0, width, top))
        overlay.paste((0, 0, 0, 0), (0, bottom, width, base.size[1]))
        band = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        band.putalpha(alpha_mask)
        overlay.alpha_composite(band, (0, top))
        left_cover = Image.new("RGBA", (int(width * 0.60), int(base.size[1] * 0.50)), (0, 0, 0, 191))
        overlay.alpha_composite(left_cover, (0, 0))
        return Image.alpha_composite(base, overlay)

    def _overlay_text_and_wordmark(
        self,
        *,
        hook_text: str,
        topic: str,
        include_logo: bool,
        image_path: Path,
    ) -> None:
        cleaned_hook = self._strip_emojis(hook_text)
        cleaned_topic = self._strip_emojis(topic)
        image = Image.open(image_path).convert("RGB")
        image = self._apply_text_gradient(image, top=84, bottom=520).convert("RGB")
        draw = ImageDraw.Draw(image)
        width, height = image.size
        title_font = self._load_font(78, bold=True)
        topic_font = self._load_mono_font(28)
        brand_font = self._load_font(28, bold=True)

        wrapped_hook = fill(cleaned_hook, width=18)
        wrapped_topic = fill(cleaned_topic, width=28)
        draw.text((110, 130), wrapped_hook, fill=_TEXT, font=title_font, spacing=10)
        draw.text((110, 395), wrapped_topic, fill=_MUTED, font=topic_font, spacing=8)

        if include_logo:
            wordmark = "KAN Logic"
            bbox = draw.textbbox((0, 0), wordmark, font=brand_font)
            wordmark_width = bbox[2] - bbox[0]
            wordmark_height = bbox[3] - bbox[1]
            mark_x = width - wordmark_width - 72
            mark_y = height - wordmark_height - 54
            draw.rounded_rectangle(
                (mark_x - 24, mark_y - 18, mark_x + wordmark_width + 24, mark_y + wordmark_height + 18),
                radius=18,
                fill=(15, 18, 34),
            )
            draw.text((mark_x, mark_y), wordmark, fill=_ACCENT, font=brand_font)
        image.save(image_path, format="JPEG", quality=92, optimize=True)

    def _generate_with_google_genai_sync(
        self,
        *,
        topic: str,
        hook_text: str,
        style_preset: str,
        format: str,
        vertical: str | None,
        content_type: str,
        include_logo: bool,
        output: Path,
    ) -> Path:
        api_key = self._google_api_key()
        if not api_key:
            raise RuntimeError("GOOGLE_API_KEY is required for Gemini image generation")

        from google import genai
        from google.genai import types

        client = genai.Client(api_key=api_key)
        model = self._gemini_image_model()
        image_bytes: bytes | None = None
        if "imagen" in model.lower():
            response = client.models.generate_images(
                model=model,
                prompt=self._build_gemini_prompt(
                    topic=topic,
                    hook_text=hook_text,
                    style_preset=style_preset,
                    format=format,
                    vertical=vertical,
                    content_type=content_type,
                    include_logo=include_logo,
                ),
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
        else:
            response = client.models.generate_content(
                model=model,
                contents=self._build_gemini_prompt(
                    topic=topic,
                    hook_text=hook_text,
                    style_preset=style_preset,
                    format=format,
                    vertical=vertical,
                    content_type=content_type,
                    include_logo=include_logo,
                ),
                config=types.GenerateContentConfig(response_modalities=["IMAGE"]),
            )
            candidates = list(getattr(response, "candidates", None) or [])
            for candidate in candidates:
                content = getattr(candidate, "content", None)
                for part in list(getattr(content, "parts", None) or []):
                    inline_data = getattr(part, "inline_data", None)
                    if inline_data is not None and getattr(inline_data, "data", None):
                        image_bytes = inline_data.data
                        break
                if image_bytes:
                    break
        if not image_bytes:
            raise RuntimeError("Gemini image generation returned no image bytes")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(image_bytes)
        return output

    async def _generate_with_gemini(
        self,
        *,
        topic: str,
        hook_text: str,
        style_preset: str,
        format: str,
        vertical: str | None,
        content_type: str,
        include_logo: bool,
        output: Path,
    ) -> Path:
        return await asyncio.to_thread(
            self._generate_with_google_genai_sync,
            topic=topic,
            hook_text=hook_text,
            style_preset=style_preset,
            format=format,
            vertical=vertical,
            content_type=content_type,
            include_logo=include_logo,
            output=output,
        )

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
        output_id = str(asset_id or hashlib.sha1(f"{topic}|{hook_text}|{style_preset}|{format}|{vertical}|{content_type}|{include_logo}".encode()).hexdigest()[:16]).strip()
        output = Path(gettempdir()) / f"{output_id}.jpg"
        try:
            await self._generate_with_gemini(
                topic=topic,
                hook_text=hook_text,
                style_preset=style_preset,
                format=format,
                vertical=vertical,
                content_type=content_type,
                include_logo=include_logo,
                output=output,
            )
            logger.info("Generated post image with Gemini at %s", output)
        except Exception:
            logger.exception("Gemini image generation failed for %s; falling back to Pillow", output_id)
            self._render_with_pillow(output)
            logger.info("Generated post image with Pillow fallback at %s", output)
        try:
            self._overlay_text_and_wordmark(
                hook_text=hook_text,
                topic=topic,
                include_logo=include_logo,
                image_path=output,
            )
        except Exception:
            logger.exception("Image overlay failed for %s; rebuilding with Pillow background", output_id)
            self._render_with_pillow(output)
            self._overlay_text_and_wordmark(
                hook_text=hook_text,
                topic=topic,
                include_logo=include_logo,
                image_path=output,
            )
        return await self.upload_image(str(output))
