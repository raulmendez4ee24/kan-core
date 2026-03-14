import base64
import io
import logging
import os
from typing import Any, Dict

from PIL import Image

from vision.ui_detector import parse_vision_response

logger = logging.getLogger("kan_core.vision.screen_analyzer")

VISION_PROMPT_TEMPLATE = (
    "You are an AI that analyzes screenshots of computer interfaces.\n"
    "Find the element requested and return pixel coordinates (x,y).\n"
    "Return only JSON.\n"
    "If more than one candidate is relevant, return an array sorted by confidence.\n"
    "Use this schema: {\"element\":\"name\",\"x\":123,\"y\":456,\"confidence\":0.9}."
)


class VisionAnalyzerError(RuntimeError):
    pass


def _get_openai_client():
    try:
        from openai import OpenAI  # type: ignore
    except Exception as exc:
        raise VisionAnalyzerError("openai SDK is required for vision analysis") from exc

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise VisionAnalyzerError("OPENAI_API_KEY is not set")

    base_url = os.getenv("OPENAI_API_BASE")
    if base_url:
        return OpenAI(api_key=api_key, base_url=base_url)
    return OpenAI(api_key=api_key)


def _extract_response_text(response: Any) -> str:
    text = getattr(response, "output_text", None)
    if isinstance(text, str) and text.strip():
        return text.strip()

    output = getattr(response, "output", None)
    if not isinstance(output, list):
        raise VisionAnalyzerError("Vision model returned empty output")

    chunks: list[str] = []
    for item in output:
        content = getattr(item, "content", None)
        if content is None and isinstance(item, dict):
            content = item.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            block_text = getattr(block, "text", None)
            if block_text is None and isinstance(block, dict):
                block_text = block.get("text")
            if isinstance(block_text, str) and block_text.strip():
                chunks.append(block_text.strip())
    if chunks:
        return "\n".join(chunks).strip()
    raise VisionAnalyzerError("Vision model did not return text")


def _get_image_size(image_bytes: bytes) -> tuple[int, int]:
    try:
        image = Image.open(io.BytesIO(image_bytes))
    except Exception as exc:
        raise VisionAnalyzerError("Invalid screenshot bytes") from exc
    return int(image.width), int(image.height)


def analyze_screen(
    image_bytes: bytes,
    instruction: str,
    *,
    image_mime_type: str = "image/png",
) -> Dict[str, Any]:
    if not isinstance(image_bytes, (bytes, bytearray)) or not image_bytes:
        raise VisionAnalyzerError("image_bytes is required")
    if not isinstance(instruction, str) or not instruction.strip():
        raise VisionAnalyzerError("instruction is required")

    model_name = os.getenv("OPENAI_VISION_MODEL", "gpt-4.1-mini")
    max_tokens = int(os.getenv("OPENAI_VISION_MAX_OUTPUT_TOKENS", "500"))

    encoded_image = base64.b64encode(bytes(image_bytes)).decode()
    user_prompt = f"{VISION_PROMPT_TEMPLATE}\n\nRequested element: {instruction.strip()}"

    client = _get_openai_client()
    try:
        response = client.responses.create(
            model=model_name,
            input=[
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": user_prompt},
                        {
                            "type": "input_image",
                            "image_url": f"data:{image_mime_type};base64,{encoded_image}",
                        },
                    ],
                }
            ],
            max_output_tokens=max_tokens,
        )
    except Exception as exc:
        logger.exception("Vision model request failed")
        raise VisionAnalyzerError(f"Vision model request failed: {exc!s}") from exc

    raw_text = _extract_response_text(response)
    width, height = _get_image_size(bytes(image_bytes))
    detections = parse_vision_response(
        raw_text,
        image_width=width,
        image_height=height,
    )
    if not detections:
        raise VisionAnalyzerError("No UI elements detected")

    primary = detections[0]
    return {
        "element": primary.element,
        "x": primary.x,
        "y": primary.y,
        "confidence": primary.confidence,
        "detections": [item.model_dump() for item in detections],
        "raw_response": raw_text,
        "image_width": width,
        "image_height": height,
    }
