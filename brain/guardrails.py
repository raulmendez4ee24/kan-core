import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from brain.audit import write_audit_log
from brain.policy_engine import EffectivePolicy
from models import AlertIncident, DesktopCommandResult, IntegrationRunLog

logger = logging.getLogger("kan_core.guardrails")

_INTEGRATION_SUCCESS_STATUSES = {"integrated", "saved_without_workflow", "already_covered"}
_INTEGRATION_FAILED_STATUSES = {"failed", "manual_action_required"}

_CB_STATE: Dict[str, Dict[str, Any]] = {}


@dataclass(frozen=True)
class CircuitBreakerResult:
    open: bool
    reason: str
    error_rate: float
    failed: int
    total: int
    opened_until: Optional[float]


def _now_ts() -> float:
    return time.time()


def _clamp01(value: float) -> float:
    return max(0.0, min(float(value), 1.0))


async def _compute_recent_error_rate(
    session: AsyncSession,
    *,
    organization_id: UUID,
    window_minutes: int,
) -> Tuple[float, int, int, Dict[str, Any]]:
    now = datetime.now(timezone.utc)
    since = now - timedelta(minutes=max(1, int(window_minutes)))

    # API/Integrations failures.
    run_stmt = select(IntegrationRunLog.status).where(
        IntegrationRunLog.client_id == organization_id,
        IntegrationRunLog.created_at >= since,
    )
    run_rows = list((await session.execute(run_stmt)).scalars().all())
    api_total = len(run_rows)
    api_failed = sum(
        1 for status in run_rows if str(status) in _INTEGRATION_FAILED_STATUSES
    )

    # Desktop/browser command failures.
    cmd_stmt = select(DesktopCommandResult.status).where(
        DesktopCommandResult.organization_id == organization_id,
        DesktopCommandResult.received_at >= since,
    )
    cmd_rows = list((await session.execute(cmd_stmt)).scalars().all())
    desktop_total = len(cmd_rows)
    desktop_failed = sum(1 for status in cmd_rows if str(status).lower() != "success")

    total = api_total + desktop_total
    failed = api_failed + desktop_failed
    error_rate = _clamp01((failed / total) if total else 0.0)
    evidence = {
        "window_minutes": int(window_minutes),
        "api_total": api_total,
        "api_failed": api_failed,
        "desktop_total": desktop_total,
        "desktop_failed": desktop_failed,
    }
    return error_rate, failed, total, evidence


async def _has_open_critical_incident(session: AsyncSession, *, organization_id: UUID) -> bool:
    stmt = (
        select(AlertIncident.id)
        .where(
            AlertIncident.organization_id == organization_id,
            AlertIncident.status == "open",
            AlertIncident.severity.in_({"critical"}),
        )
        .limit(1)
    )
    return bool((await session.execute(stmt)).scalars().first())


def _state_key(organization_id: UUID) -> str:
    return str(organization_id)


async def evaluate_circuit_breaker(
    session: AsyncSession,
    *,
    organization_id: UUID,
    policy: EffectivePolicy,
    source: str,
) -> CircuitBreakerResult:
    cfg = policy.circuit_breaker_config()
    enabled = bool(cfg.get("enabled"))
    if not enabled:
        return CircuitBreakerResult(
            open=False,
            reason="disabled",
            error_rate=0.0,
            failed=0,
            total=0,
            opened_until=None,
        )

    threshold = float(cfg.get("error_rate_threshold") or 0.45)
    window_minutes = int(cfg.get("window_minutes") or 15)
    open_minutes = int(cfg.get("open_minutes") or 10)
    min_samples = max(3, int(os.getenv("CIRCUIT_BREAKER_MIN_SAMPLES", "10") or 10))

    error_rate, failed, total, evidence = await _compute_recent_error_rate(
        session,
        organization_id=organization_id,
        window_minutes=window_minutes,
    )

    has_critical_incident = False
    try:
        has_critical_incident = await _has_open_critical_incident(session, organization_id=organization_id)
    except Exception:
        logger.exception("incident_check_failed org=%s", organization_id)

    key = _state_key(organization_id)
    state = dict(_CB_STATE.get(key) or {})
    opened_until = float(state.get("opened_until") or 0.0)
    previously_open = bool(state.get("open", False)) and opened_until > _now_ts()

    should_open = False
    reason = ""
    if has_critical_incident:
        should_open = True
        reason = "open_critical_incident"
    elif total >= min_samples and error_rate >= max(0.0, min(threshold, 1.0)):
        should_open = True
        reason = "error_rate_threshold_exceeded"

    if previously_open:
        if should_open:
            # Extend open window.
            new_opened_until = _now_ts() + max(1, open_minutes) * 60.0
            _CB_STATE[key] = {"open": True, "opened_until": new_opened_until}
            return CircuitBreakerResult(
                open=True,
                reason=reason or "still_open",
                error_rate=error_rate,
                failed=failed,
                total=total,
                opened_until=new_opened_until,
            )
        # Keep open until window expires.
        return CircuitBreakerResult(
            open=True,
            reason="cooldown_active",
            error_rate=error_rate,
            failed=failed,
            total=total,
            opened_until=opened_until,
        )

    if should_open:
        new_opened_until = _now_ts() + max(1, open_minutes) * 60.0
        _CB_STATE[key] = {"open": True, "opened_until": new_opened_until}
        try:
            await write_audit_log(
                session,
                organization_id=organization_id,
                actor_user_id=None,
                action="guardrails.circuit_breaker.open",
                resource="organization",
                resource_id=str(organization_id),
                status="open",
                detail=reason,
                metadata={
                    "source": source,
                    "threshold": threshold,
                    "error_rate": round(error_rate, 6),
                    "failed": failed,
                    "total": total,
                    "evidence": evidence,
                    "opened_until": new_opened_until,
                },
            )
        except Exception:
            logger.exception("audit_cb_open_failed org=%s", organization_id)
        return CircuitBreakerResult(
            open=True,
            reason=reason,
            error_rate=error_rate,
            failed=failed,
            total=total,
            opened_until=new_opened_until,
        )

    # Closed.
    if state.get("open"):
        _CB_STATE[key] = {"open": False, "opened_until": 0.0}
        try:
            await write_audit_log(
                session,
                organization_id=organization_id,
                actor_user_id=None,
                action="guardrails.circuit_breaker.close",
                resource="organization",
                resource_id=str(organization_id),
                status="closed",
                metadata={
                    "source": source,
                    "threshold": threshold,
                    "error_rate": round(error_rate, 6),
                    "failed": failed,
                    "total": total,
                    "evidence": evidence,
                },
            )
        except Exception:
            logger.exception("audit_cb_close_failed org=%s", organization_id)

    _CB_STATE[key] = {"open": False, "opened_until": 0.0}
    return CircuitBreakerResult(
        open=False,
        reason="ok",
        error_rate=error_rate,
        failed=failed,
        total=total,
        opened_until=None,
    )
