"""
GoalTracker — closes the feedback loop that was missing in the original system.

When the dispatcher fires an action, GoalTracker registers the intent.
A periodic background task then checks whether evidence of success appeared
in BusinessEvent or IntegrationRunLog. If success is confirmed, it feeds
back into ContinuousImprovement. If it times out, it triggers AdaptivePlanner
to generate a new plan automatically.
"""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database import AsyncSessionLocal
from models import BusinessEvent, IntegrationRunLog, RecommendationAction
from observability import capture_exception

logger = logging.getLogger("kan_core.goal_tracker")

# In-memory registry for the current process. Each entry tracks a dispatched goal.
_pending_goals: Dict[str, "_GoalEntry"] = {}
_tracker_task: Optional[asyncio.Task] = None
_tracker_running: bool = False


@dataclass
class _GoalEntry:
    goal_id: str
    org_id: str
    client_id: str
    goal_text: str
    action_type: str          # e.g. "integration", "desktop", "n8n"
    action_name: str          # e.g. "sync_orders"
    dispatched_at: datetime
    deadline: datetime
    achieved: bool = False
    checked_at: Optional[datetime] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def register_goal(
    *,
    client_id: str,
    goal_text: str,
    action_type: str,
    action_name: str,
    timeout_minutes: int = 30,
    metadata: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Register a dispatched action for goal tracking.
    Returns a unique goal_id that can be used for follow-up.
    """
    goal_id = str(uuid4())
    now = datetime.now(timezone.utc)
    _pending_goals[goal_id] = _GoalEntry(
        goal_id=goal_id,
        org_id=client_id,
        client_id=client_id,
        goal_text=goal_text,
        action_type=action_type,
        action_name=action_name,
        dispatched_at=now,
        deadline=now + timedelta(minutes=timeout_minutes),
        metadata=dict(metadata or {}),
    )
    logger.debug("GoalTracker: registered goal %s — %s", goal_id, goal_text[:80])
    return goal_id


def mark_achieved(goal_id: str) -> None:
    """Externally mark a goal as achieved (e.g. from a callback)."""
    entry = _pending_goals.get(goal_id)
    if entry:
        entry.achieved = True
        logger.info("GoalTracker: goal %s marked achieved externally", goal_id)


def get_pending_goals() -> List[Dict[str, Any]]:
    """Return a snapshot of all pending (unresolved) goals."""
    now = datetime.now(timezone.utc)
    return [
        {
            "goal_id": e.goal_id,
            "goal_text": e.goal_text,
            "action_name": e.action_name,
            "dispatched_at": e.dispatched_at.isoformat(),
            "deadline": e.deadline.isoformat(),
            "timed_out": now > e.deadline,
            "achieved": e.achieved,
        }
        for e in _pending_goals.values()
        if not e.achieved
    ]


def start_tracker() -> None:
    """Start the background evaluation loop."""
    global _tracker_running, _tracker_task
    if not _tracker_running:
        _tracker_running = True
        _tracker_task = asyncio.create_task(_tracker_loop())
        logger.info("GoalTracker background loop started")


def stop_tracker() -> None:
    global _tracker_running, _tracker_task
    _tracker_running = False
    if _tracker_task and not _tracker_task.done():
        _tracker_task.cancel()


# ---------------------------------------------------------------------------
# Background evaluation loop
# ---------------------------------------------------------------------------

async def _tracker_loop() -> None:
    interval = int(os.getenv("GOAL_TRACKER_INTERVAL_MINUTES", "10")) * 60
    await asyncio.sleep(60)  # let app fully boot
    while _tracker_running:
        try:
            await _evaluate_all_goals()
        except asyncio.CancelledError:
            break
        except Exception as exc:
            capture_exception(exc, {"source": "goal_tracker"})
            logger.exception("GoalTracker evaluation loop failed")
        await asyncio.sleep(interval)


async def _evaluate_all_goals() -> None:
    now = datetime.now(timezone.utc)
    resolved: List[str] = []

    for goal_id, entry in list(_pending_goals.items()):
        if entry.achieved:
            resolved.append(goal_id)
            continue

        try:
            achieved = await _check_goal_achieved(entry)
        except Exception as exc:
            capture_exception(exc, {"source": "goal_tracker.check", "goal_id": goal_id})
            continue

        entry.checked_at = now

        if achieved:
            entry.achieved = True
            resolved.append(goal_id)
            logger.info("GoalTracker: ✓ goal achieved — %s", entry.action_name)
            await _on_goal_achieved(entry)
        elif now > entry.deadline:
            # Goal timed out — trigger adaptive replanning.
            resolved.append(goal_id)
            logger.warning(
                "GoalTracker: ✗ goal timed out after %s min — %s",
                (now - entry.dispatched_at).seconds // 60,
                entry.action_name,
            )
            await _on_goal_timed_out(entry)

    for goal_id in resolved:
        _pending_goals.pop(goal_id, None)


async def _check_goal_achieved(entry: _GoalEntry) -> bool:
    """
    Look for success signals in BusinessEvent and IntegrationRunLog
    that correspond to this goal's action and time window.
    """
    async with AsyncSessionLocal() as session:
        # Signal 1: successful integration run after dispatch time.
        run_stmt = select(func.count(IntegrationRunLog.id)).where(
            IntegrationRunLog.client_id == UUID(entry.client_id),
            IntegrationRunLog.function_name.ilike(f"%{entry.action_name.replace('_', '%')}%"),
            IntegrationRunLog.status.in_(["success", "ok", "completed"]),
            IntegrationRunLog.created_at >= entry.dispatched_at,
        )
        success_count = (await session.execute(run_stmt)).scalar() or 0
        if success_count > 0:
            return True

        # Signal 2: business event matching the goal (e.g. sale_closed after sync_orders).
        _GOAL_EVENT_MAP = {
            "sync_orders": "sale_closed",
            "sync": "message_out",
            "create_campaign": "message_out",
        }
        event_type = _GOAL_EVENT_MAP.get(entry.action_name)
        if event_type:
            evt_stmt = select(func.count(BusinessEvent.id)).where(
                BusinessEvent.organization_id == UUID(entry.org_id),
                BusinessEvent.event_type == event_type,
                BusinessEvent.created_at >= entry.dispatched_at,
            )
            evt_count = (await session.execute(evt_stmt)).scalar() or 0
            if evt_count > 0:
                return True

    return False


async def _on_goal_achieved(entry: _GoalEntry) -> None:
    """Feed positive signal back to ContinuousImprovement via a success RecommendationAction."""
    try:
        async with AsyncSessionLocal() as session:
            from models import RecommendationAction as RA
            action_row = RA(
                recommendation_id=None,  # no specific recommendation — general feedback
                organization_id=UUID(entry.org_id),
                action_type=entry.action_type,
                status="success",
                result={
                    "goal_id": entry.goal_id,
                    "goal_text": entry.goal_text,
                    "action_name": entry.action_name,
                    "elapsed_seconds": int(
                        (datetime.now(timezone.utc) - entry.dispatched_at).total_seconds()
                    ),
                    "source": "goal_tracker",
                },
                risk_score=0.1,
            )
            session.add(action_row)
            await session.commit()
    except Exception as exc:
        capture_exception(exc, {"source": "goal_tracker.on_achieved"})


async def _on_goal_timed_out(entry: _GoalEntry) -> None:
    """Trigger adaptive replanning when a goal times out."""
    try:
        from brain.adaptive_planner import AdaptivePlanner
        await AdaptivePlanner.replan_from_timeout(
            client_id=entry.client_id,
            goal_text=entry.goal_text,
            failed_action=entry.action_name,
            metadata=entry.metadata,
        )
    except Exception as exc:
        capture_exception(exc, {"source": "goal_tracker.on_timeout", "goal_id": entry.goal_id})
        logger.warning("GoalTracker: adaptive replan failed for goal %s: %s", entry.goal_id, exc)
