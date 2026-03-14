import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import GlobalLearning


def _enabled() -> bool:
    return os.getenv("ENABLE_STRATEGY_LEARNING", "true").lower() in {"1", "true", "yes"}


def _normalize_text(value: Any, default: str) -> str:
    token = str(value or "").strip().lower()
    if not token:
        return default
    return token[:120]


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _compact_value(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        compact: Dict[str, Any] = {}
        for key in sorted(value.keys()):
            compact_key = str(key)[:64]
            compact[compact_key] = _compact_value(value[key])
        return compact
    if isinstance(value, list):
        return [_compact_value(item) for item in value[:10]]
    return str(value)[:300]


def _ui_signature(ui_context: Dict[str, Any]) -> str:
    payload = json.dumps(_compact_value(ui_context), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def build_ui_context(
    *,
    action: str,
    args: Dict[str, Any],
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    context: Dict[str, Any] = {
        "action": _normalize_text(action, "unknown_action"),
        "is_browser": str(action).startswith("browser_"),
    }
    src = dict(args or {})

    for key in ("selector", "text", "role", "key", "button"):
        value = src.get(key)
        if isinstance(value, str) and value.strip():
            context[key] = value.strip()[:160]

    url = src.get("url") or src.get("start_url")
    if isinstance(url, str) and url.strip():
        parsed = urlparse(url)
        if parsed.hostname:
            context["domain"] = parsed.hostname.lower()[:160]

    x = src.get("x")
    y = src.get("y")
    if isinstance(x, int):
        context["x_bucket"] = max(0, int(x / 80))
    if isinstance(y, int):
        context["y_bucket"] = max(0, int(y / 80))

    meta = dict(metadata or {})
    risk_level = meta.get("risk_level")
    if isinstance(risk_level, str) and risk_level.strip():
        context["risk_level"] = risk_level.strip().lower()[:32]
    industry = meta.get("industry")
    if isinstance(industry, str) and industry.strip():
        context["industry_hint"] = industry.strip().lower()[:120]
    signature_payload = dict(context)
    context["ui_signature"] = _ui_signature(signature_payload)
    return context


def _impact_score(*, success: bool, duration_ms: Optional[int]) -> float:
    duration = max(0, min(_safe_int(duration_ms, 0), 120000))
    speed_score = max(0.0, 1.0 - (duration / 30000.0))
    base = 0.8 if success else 0.0
    return max(0.0, min((base + (0.2 * speed_score)) * 100.0, 100.0))


async def _upsert_pattern(
    session: AsyncSession,
    *,
    pattern_key: str,
    industry: str,
    function_name: str,
    strategy: str,
    scope: str,
    organization_id: str,
    ui_context: Dict[str, Any],
    ui_signature: str,
    success: bool,
    duration_ms: Optional[int],
    reason: Optional[str],
    error_type: Optional[str],
    extra: Optional[Dict[str, Any]],
) -> None:
    stmt = select(GlobalLearning).where(GlobalLearning.pattern_key == pattern_key)
    row = (await session.execute(stmt)).scalars().first()
    now = datetime.now(timezone.utc)
    success_value = 1.0 if success else 0.0
    impact = _impact_score(success=success, duration_ms=duration_ms)

    recent_event = {
        "at": now.isoformat(),
        "success": success,
        "duration_ms": _safe_int(duration_ms, 0) if duration_ms is not None else None,
        "reason": (str(reason)[:300] if reason else None),
        "error_type": (str(error_type)[:120] if error_type else None),
    }

    if row:
        prev_samples = int(row.sample_size or 0)
        new_samples = prev_samples + 1
        prev_success = float(row.success_rate or 0.0)
        prev_impact = float(row.avg_impact_score or 0.0)

        row.sample_size = new_samples
        row.success_rate = ((prev_success * prev_samples) + success_value) / new_samples
        row.avg_impact_score = ((prev_impact * prev_samples) + impact) / new_samples

        meta = dict(row.learning_metadata or {})
        recent = list(meta.get("recent_outcomes") or [])
        recent.append(recent_event)
        meta.update(
            {
                "scope": scope,
                "organization_id": organization_id if scope.startswith("org:") else None,
                "action": function_name,
                "strategy": strategy,
                "ui_context": _compact_value(ui_context),
                "ui_signature": ui_signature,
                "last_result": recent_event,
                "recent_outcomes": recent[-30:],
                "updated_at": now.isoformat(),
                "extra": _compact_value(extra or {}),
            }
        )
        row.learning_metadata = meta
        return

    row = GlobalLearning(
        pattern_key=pattern_key,
        industry=industry,
        function_name=function_name,
        provider_id=strategy,
        sample_size=1,
        success_rate=success_value,
        avg_impact_score=impact,
        learning_metadata={
            "scope": scope,
            "organization_id": organization_id if scope.startswith("org:") else None,
            "action": function_name,
            "strategy": strategy,
            "ui_context": _compact_value(ui_context),
            "ui_signature": ui_signature,
            "last_result": recent_event,
            "recent_outcomes": [recent_event],
            "created_at": now.isoformat(),
            "extra": _compact_value(extra or {}),
        },
    )
    session.add(row)


async def record_strategy_outcome(
    session: AsyncSession,
    *,
    organization_id: str,
    industry: Optional[str],
    action: str,
    strategy: str,
    ui_context: Optional[Dict[str, Any]],
    success: bool,
    duration_ms: Optional[int],
    reason: Optional[str] = None,
    error_type: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    if not _enabled():
        return

    industry_token = _normalize_text(industry, "general")
    action_token = _normalize_text(action, "unknown_action")
    strategy_token = _normalize_text(strategy, "unknown_strategy")
    function_name = f"desktop_recovery:{action_token}"
    compact_context = _compact_value(ui_context or {})
    context = compact_context if isinstance(compact_context, dict) else {"raw": compact_context}
    context_for_signature = dict(context)
    context_for_signature.pop("ui_signature", None)
    signature = str(context.get("ui_signature") or _ui_signature(context_for_signature))

    org_scope = f"org:{organization_id}"
    global_scope = "global"
    org_pattern_key = f"desktop_strategy:{org_scope}:{signature}:{strategy_token}"
    global_pattern_key = f"desktop_strategy:{global_scope}:{signature}:{strategy_token}"

    await _upsert_pattern(
        session,
        pattern_key=org_pattern_key,
        industry=industry_token,
        function_name=function_name,
        strategy=strategy_token,
        scope=org_scope,
        organization_id=organization_id,
        ui_context=context,
        ui_signature=signature,
        success=success,
        duration_ms=duration_ms,
        reason=reason,
        error_type=error_type,
        extra=extra,
    )
    await _upsert_pattern(
        session,
        pattern_key=global_pattern_key,
        industry=industry_token,
        function_name=function_name,
        strategy=strategy_token,
        scope=global_scope,
        organization_id=organization_id,
        ui_context=context,
        ui_signature=signature,
        success=success,
        duration_ms=duration_ms,
        reason=reason,
        error_type=error_type,
        extra=extra,
    )
    await session.commit()
