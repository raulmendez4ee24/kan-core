import json
import re
from typing import Any, List

from vision.schemas import ScreenElementDetection


class UIDetectorError(ValueError):
    pass


def _strip_code_fences(text: str) -> str:
    candidate = text.strip()
    if not candidate.startswith("```"):
        return candidate

    lines = candidate.splitlines()
    if len(lines) < 3:
        return candidate
    if lines[0].startswith("```") and lines[-1].strip() == "```":
        return "\n".join(lines[1:-1]).strip()
    return candidate


def _extract_json_candidate(text: str) -> Any:
    cleaned = _strip_code_fences(text)
    if cleaned.lower().startswith("json"):
        cleaned = cleaned[4:].strip()

    for direct in (cleaned, cleaned.replace("\n", " ").strip()):
        try:
            return json.loads(direct)
        except json.JSONDecodeError:
            pass

    for pattern in (r"\{.*\}", r"\[.*\]"):
        match = re.search(pattern, cleaned, flags=re.DOTALL)
        if not match:
            continue
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            continue

    raise UIDetectorError("Vision model response did not contain valid JSON")


def _to_int(value: Any, *, field: str) -> int:
    if isinstance(value, bool):
        raise UIDetectorError(f"{field} must be numeric")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(round(value))
    if isinstance(value, str):
        token = value.strip()
        if not token:
            raise UIDetectorError(f"{field} is empty")
        try:
            if "." in token:
                return int(round(float(token)))
            return int(token)
        except ValueError as exc:
            raise UIDetectorError(f"{field} must be numeric") from exc
    raise UIDetectorError(f"{field} must be numeric")


def _to_confidence(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        parsed = float(value)
    elif isinstance(value, str):
        token = value.strip()
        if not token:
            return 0.0
        try:
            parsed = float(token)
        except ValueError:
            return 0.0
    else:
        return 0.0

    if parsed < 0.0:
        return 0.0
    if parsed > 1.0:
        return 1.0
    return parsed


def _normalize_items(payload: Any) -> List[dict]:
    if isinstance(payload, dict):
        if isinstance(payload.get("detections"), list):
            items = payload["detections"]
        else:
            items = [payload]
    elif isinstance(payload, list):
        items = payload
    else:
        raise UIDetectorError("Vision JSON must be object or array")

    normalized: List[dict] = []
    for item in items:
        if isinstance(item, dict):
            normalized.append(item)
    if not normalized:
        raise UIDetectorError("Vision JSON does not contain detection objects")
    return normalized


def parse_vision_response(
    raw_response: str,
    *,
    image_width: int,
    image_height: int,
) -> List[ScreenElementDetection]:
    payload = _extract_json_candidate(raw_response)
    items = _normalize_items(payload)

    detections: List[ScreenElementDetection] = []
    for item in items:
        element = str(
            item.get("element")
            or item.get("label")
            or item.get("name")
            or "target_element"
        ).strip()
        if not element:
            element = "target_element"

        x = _to_int(item.get("x"), field="x")
        y = _to_int(item.get("y"), field="y")
        if x < 0 or y < 0:
            raise UIDetectorError("Coordinates cannot be negative")
        if x >= image_width or y >= image_height:
            raise UIDetectorError(
                f"Coordinates out of bounds: ({x},{y}) for {image_width}x{image_height}"
            )

        confidence = _to_confidence(item.get("confidence", item.get("score")))
        detections.append(
            ScreenElementDetection(
                element=element,
                x=x,
                y=y,
                confidence=confidence,
            )
        )

    detections.sort(key=lambda item: item.confidence, reverse=True)
    return detections
