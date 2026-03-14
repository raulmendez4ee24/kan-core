"""
API routes for the ProactiveMonitor and GoalTracker.

GET  /proactive/status          — Monitor status + last cycle result
POST /proactive/trigger         — Manually trigger a monitoring cycle
GET  /proactive/goals           — List pending goals being tracked
DELETE /proactive/goals/{id}    — Mark a goal as resolved
"""
from fastapi import APIRouter, Depends, HTTPException

from brain.proactive_monitor import ProactiveMonitor
from security import require_client_token

router = APIRouter(prefix="/proactive", tags=["proactive"])


@router.get("/status")
async def proactive_status(_: str = Depends(require_client_token)):
    """Return the current status of the ProactiveMonitor."""
    return ProactiveMonitor.status()


@router.post("/trigger")
async def proactive_trigger(_: str = Depends(require_client_token)):
    """
    Manually trigger one ProactiveMonitor cycle.
    Useful for testing and on-demand checks without waiting for the scheduler.
    """
    try:
        result = await ProactiveMonitor.run_cycle()
        return {"status": "ok", "result": result}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/goals")
async def list_goals(_: str = Depends(require_client_token)):
    """Return all goals currently being tracked by GoalTracker."""
    try:
        from brain.goal_tracker import get_pending_goals
        return {"goals": get_pending_goals()}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.delete("/goals/{goal_id}")
async def resolve_goal(goal_id: str, _: str = Depends(require_client_token)):
    """Manually mark a tracked goal as achieved."""
    try:
        from brain.goal_tracker import mark_achieved
        mark_achieved(goal_id)
        return {"status": "ok", "goal_id": goal_id}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
