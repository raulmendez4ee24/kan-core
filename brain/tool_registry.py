from __future__ import annotations

import os
from typing import Any, Dict, List


def tool_available(tool_name: str) -> bool:
    token = str(tool_name or "").strip().lower()
    if token == "n8n":
        return bool(str(os.getenv("N8N_WEBHOOK_URL") or "").strip())
    if token == "http":
        return True
    if token in {"db_internal", "internal_service"}:
        return True
    if token in {"desktop", "browser"}:
        return os.getenv("ENABLE_DESKTOP_AGENT", "false").lower() in {"1", "true", "yes"}
    return False


def build_tool_candidates(step: Dict[str, Any]) -> List[str]:
    recommended = str(step.get("recommended_tool") or "").strip().lower()
    fallbacks = [str(item).strip().lower() for item in (step.get("fallback_tools") or []) if str(item).strip()]
    ordered: List[str] = []
    for token in [recommended, *fallbacks]:
        if token and token not in ordered and tool_available(token):
            ordered.append(token)
    if ordered:
        return ordered
    return [token for token in ["db_internal", "internal_service", "http", "n8n"] if tool_available(token)]
