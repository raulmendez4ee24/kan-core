from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

_WRITE_LOCK = threading.Lock()


def _state_path() -> Path:
    raw = str(os.getenv("JARVIS_UI_STATE_FILE") or ".run/jarvis_ui_state.json").strip()
    path = Path(raw)
    if not path.is_absolute():
        root = Path(__file__).resolve().parents[1]
        path = root / path
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _normalize_level(level: Optional[float]) -> Optional[float]:
    if level is None:
        return None
    try:
        value = float(level)
    except (TypeError, ValueError):
        return None
    return max(0.0, min(1.0, value))


def publish_state(
    mode: str,
    *,
    source: str,
    text: str = "",
    level: float | None = None,
) -> None:
    payload = {
        "mode": str(mode or "standby").strip() or "standby",
        "source": str(source or "unknown").strip() or "unknown",
        "text": str(text or "").strip(),
        "level": _normalize_level(level),
        "updated_at": float(time.time()),
    }
    path = _state_path()
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    blob = json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
    with _WRITE_LOCK:
        tmp_path.write_text(blob, encoding="utf-8")
        os.replace(tmp_path, path)


def read_state() -> Dict[str, Any]:
    path = _state_path()
    if not path.exists():
        return {
            "mode": "standby",
            "source": "default",
            "text": "",
            "level": None,
            "updated_at": 0.0,
        }
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {
            "mode": "standby",
            "source": "corrupt",
            "text": "",
            "level": None,
            "updated_at": 0.0,
        }
    if not isinstance(data, dict):
        return {
            "mode": "standby",
            "source": "invalid",
            "text": "",
            "level": None,
            "updated_at": 0.0,
        }
    return {
        "mode": str(data.get("mode") or "standby"),
        "source": str(data.get("source") or "unknown"),
        "text": str(data.get("text") or ""),
        "level": _normalize_level(data.get("level")),
        "updated_at": float(data.get("updated_at") or 0.0),
    }

