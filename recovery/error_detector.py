import base64
import json
import logging
import os
import re
from typing import Any

from recovery.schemas import ErrorAnalysisResult

logger = logging.getLogger("kan_core.recovery.error_detector")

ERROR_DETECTION_PROMPT = (
    "You are analyzing a computer screenshot after an automated action.\n"
    "Determine if the intended goal was achieved.\n"
    "If not, explain why and suggest a correction strategy.\n"
    "Return JSON only.\n"
    "Allowed error_type: none, element_not_found, incorrect_coordinates, wrong_screen, "
    "visible_error_message, no_visual_change, unknown.\n"
    "Allowed suggested_fix: adjust_coordinates, scroll_and_retry, search_again, wait_and_retry, "
    "fallback_click_center, retry_with_alternate_selector.\n"
    "Schema: {\"success\":false,\"error_type\":\"element_not_found\",\"reason\":\"...\","
    "\"suggested_fix\":\"search_again\",\"confidence\":0.8}."
)


def _get_openai_client():
    try:
        from openai import OpenAI  # type: ignore
    except Exception as exc:
        raise RuntimeError("openai SDK is required for recovery error detection") from exc

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")
    base_url = os.getenv("OPENAI_API_BASE")
    if base_url:
        return OpenAI(api_key=api_key, base_url=base_url)
    return OpenAI(api_key=api_key)


def _extract_text(response: Any) -> str:
    output_text = getattr(response, "output_text", None)
    if isinstance(output_text, str) and output_text.strip():
        return output_text.strip()

    output = getattr(response, "output", None)
    if not isinstance(output, list):
        return ""
    chunks: list[str] = []
    for item in output:
        content = getattr(item, "content", None)
        if content is None and isinstance(item, dict):
            content = item.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            text = getattr(block, "text", None)
            if text is None and isinstance(block, dict):
                text = block.get("text")
            if isinstance(text, str) and text.strip():
                chunks.append(text.strip())
    return "\n".join(chunks).strip()


def _parse_json(raw: str) -> dict:
    candidate = raw.strip()
    if candidate.startswith("```"):
        lines = candidate.splitlines()
        if len(lines) >= 3 and lines[-1].strip() == "```":
            candidate = "\n".join(lines[1:-1]).strip()
    if candidate.lower().startswith("json"):
        candidate = candidate[4:].strip()

    try:
        parsed = json.loads(candidate)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{.*\}", candidate, flags=re.DOTALL)
    if not match:
        raise ValueError("Could not parse JSON from error detector response")
    parsed = json.loads(match.group(0))
    if not isinstance(parsed, dict):
        raise ValueError("Error detector JSON must be object")
    return parsed


def _fallback_result(reason: str) -> ErrorAnalysisResult:
    return ErrorAnalysisResult(
        success=False,
        error_type="unknown",
        reason=reason,
        suggested_fix="search_again",
        confidence=0.0,
        raw={},
    )


def detect_error(
    before_image_bytes: bytes,
    after_image_bytes: bytes,
    instruction: str,
    expected_outcome: str,
) -> ErrorAnalysisResult:
    if not isinstance(after_image_bytes, (bytes, bytearray)) or not after_image_bytes:
        return _fallback_result("Missing after screenshot")
    if not isinstance(instruction, str) or not instruction.strip():
        return _fallback_result("Missing instruction for recovery analysis")

    model_name = os.getenv("OPENAI_VISION_MODEL", "gpt-4.1-mini")
    max_tokens = int(os.getenv("OPENAI_VISION_MAX_OUTPUT_TOKENS", "450"))

    before_b64 = base64.b64encode(bytes(before_image_bytes or b"")).decode()
    after_b64 = base64.b64encode(bytes(after_image_bytes)).decode()

    prompt = (
        f"{ERROR_DETECTION_PROMPT}\n\n"
        f"Instruction: {instruction.strip()}\n"
        f"Expected outcome: {expected_outcome.strip() if expected_outcome else 'N/A'}"
    )

    try:
        client = _get_openai_client()
        response = client.responses.create(
            model=model_name,
            input=[
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": prompt},
                        {
                            "type": "input_image",
                            "image_url": f"data:image/png;base64,{before_b64}",
                        },
                        {
                            "type": "input_image",
                            "image_url": f"data:image/png;base64,{after_b64}",
                        },
                    ],
                }
            ],
            max_output_tokens=max_tokens,
        )
    except Exception as exc:
        logger.warning("Vision error detection request failed: %s", exc)
        return _fallback_result(f"vision_error_detection_failed: {exc!s}")

    raw_text = _extract_text(response)
    if not raw_text:
        return _fallback_result("Vision error detector returned empty response")

    try:
        parsed = _parse_json(raw_text)
        parsed["raw"] = {"model_response": raw_text}
        return ErrorAnalysisResult.model_validate(parsed)
    except Exception as exc:
        logger.warning("Could not parse error detector response: %s", exc)
        return _fallback_result(f"invalid_error_detector_json: {exc!s}")
