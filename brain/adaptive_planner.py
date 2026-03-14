"""
AdaptivePlanner — generates alternative plans when the original plan fails.

Unlike StrictPlanner (which retries the same approach), AdaptivePlanner:
1. Consults WorldModel for proven alternative UI/action paths.
2. Feeds failure context into the LLM to generate a new plan.
3. Automatically falls back across tool types: API → Browser → Desktop.
4. Marks goals as "unresolvable" after exhausting all alternatives.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from database import AsyncSessionLocal
from observability import capture_exception

logger = logging.getLogger("kan_core.adaptive_planner")

# Tool fallback chain: if one tool type fails, try the next.
_TOOL_FALLBACK_CHAIN = ["api", "browser", "desktop"]


class AdaptivePlanner:
    """
    Generates alternative execution plans on failure by combining:
    - WorldModel proven transitions (probabilistic path discovery).
    - LLM-based replanning with failure context injected into the prompt.
    - Cross-tool fallback (API → Browser → Desktop).
    """

    @staticmethod
    async def replan(
        *,
        client_id: str,
        session_id: str,
        goal: str,
        failed_steps: List[Dict[str, Any]],
        failure_reason: str,
        current_tool: str = "api",
        db_session: Optional[AsyncSession] = None,
    ) -> Dict[str, Any]:
        """
        Build an alternative plan given what already failed.

        Returns a plan dict with keys: plan, tool_used, strict, from_world_model.
        """
        # Step 1: Check WorldModel for proven alternative paths.
        world_model_alternatives = await _fetch_world_model_alternatives(
            db_session=db_session,
            org_id=client_id,
            goal=goal,
        )

        # Step 2: Try LLM-based replanning with failure context.
        try:
            plan = await _replan_with_llm(
                client_id=client_id,
                session_id=session_id,
                goal=goal,
                failed_steps=failed_steps,
                failure_reason=failure_reason,
                world_model_hints=world_model_alternatives,
            )
            if plan:
                return {**plan, "tool_used": current_tool, "from_world_model": bool(world_model_alternatives)}
        except Exception as exc:
            capture_exception(exc, {"source": "adaptive_planner.llm_replan"})
            logger.warning("AdaptivePlanner: LLM replan failed: %s", exc)

        # Step 3: Fallback to the next tool in the chain.
        next_tool = _next_tool(current_tool)
        if next_tool:
            logger.info(
                "AdaptivePlanner: falling back from %s → %s for goal: %s",
                current_tool, next_tool, goal[:80],
            )
            fallback_plan = _build_fallback_plan(goal=goal, tool=next_tool)
            return {
                "plan": fallback_plan,
                "tool_used": next_tool,
                "strict": False,
                "from_world_model": False,
                "fallback": True,
            }

        # Step 4: All alternatives exhausted.
        logger.error(
            "AdaptivePlanner: goal unresolvable after exhausting all tools — %s", goal[:120]
        )
        return {
            "plan": None,
            "tool_used": None,
            "strict": False,
            "from_world_model": False,
            "unresolvable": True,
            "reason": failure_reason,
        }

    @staticmethod
    async def replan_from_timeout(
        *,
        client_id: str,
        goal_text: str,
        failed_action: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Called by GoalTracker when a goal times out.
        Attempts a lightweight replan and dispatches to n8n.
        """
        from brain.core import CognitiveEngine
        from tools.n8n_client import send_to_n8n_with_response

        logger.info(
            "AdaptivePlanner: replanning timed-out goal '%s' (action=%s)",
            goal_text[:80], failed_action,
        )
        try:
            session_id = f"adaptive_replan:{client_id}"
            engine = CognitiveEngine.get_instance()

            replan_prompt = (
                f"The previous automation failed or timed out. "
                f"Failed action: '{failed_action}'. "
                f"Original goal: '{goal_text}'. "
                f"Generate an alternative approach to achieve this goal using a different method. "
                f"Be specific and actionable."
            )
            ai_result = await engine.generate_response(
                client_id=client_id,
                session_id=session_id,
                message=replan_prompt,
            )

            await send_to_n8n_with_response(
                {
                    "source": "adaptive_planner.replan",
                    "client_id": client_id,
                    "original_goal": goal_text,
                    "failed_action": failed_action,
                    "replan_result": ai_result,
                    "metadata": metadata or {},
                }
            )
            logger.info("AdaptivePlanner: replan dispatched to n8n for goal '%s'", goal_text[:80])
        except Exception as exc:
            capture_exception(
                exc, {"source": "adaptive_planner.replan_timeout", "client_id": client_id}
            )
            logger.exception("AdaptivePlanner: replan_from_timeout failed")


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

async def _fetch_world_model_alternatives(
    *,
    db_session: Optional[AsyncSession],
    org_id: str,
    goal: str,
) -> List[Dict[str, Any]]:
    """Query WorldModel for the best-known transitions relevant to this goal."""
    try:
        from brain.world_model import best_transitions_for_state

        async def _query(session: AsyncSession) -> List[Dict[str, Any]]:
            # Use the goal as a rough UI signature (first 180 chars).
            transitions = await best_transitions_for_state(
                session,
                organization_id=UUID(org_id),
                from_signature=goal[:180],
                limit=5,
            )
            return [
                {
                    "action": t.action,
                    "to_signature": t.to_signature,
                    "success_rate": float(t.success_rate or 0),
                    "avg_duration_ms": float(t.avg_duration_ms or 0),
                    "sample_size": int(t.sample_size or 0),
                }
                for t in transitions
                if float(t.success_rate or 0) >= 0.5
            ]

        if db_session:
            return await _query(db_session)
        async with AsyncSessionLocal() as session:
            return await _query(session)
    except Exception as exc:
        capture_exception(exc, {"source": "adaptive_planner.world_model"})
        return []


async def _replan_with_llm(
    *,
    client_id: str,
    session_id: str,
    goal: str,
    failed_steps: List[Dict[str, Any]],
    failure_reason: str,
    world_model_hints: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Use StrictPlanner with failure context to generate an alternative plan."""
    from brain.core import CognitiveEngine
    from brain.strict_planner import StrictPlanner

    engine = CognitiveEngine.get_instance()
    planner = StrictPlanner(max_retries=2)

    context: Dict[str, Any] = {
        "replanning": True,
        "failure_reason": failure_reason[:500],
        "failed_steps": failed_steps[:5],
    }
    if world_model_hints:
        context["proven_alternatives"] = world_model_hints

    return await planner.build_plan(
        engine=engine,
        client_id=client_id,
        session_id=f"{session_id}:replan",
        goal=f"[REPLAN] {goal}",
        context=context,
    )


def _next_tool(current_tool: str) -> Optional[str]:
    """Return the next tool in the fallback chain, or None if exhausted."""
    try:
        idx = _TOOL_FALLBACK_CHAIN.index(current_tool.lower())
        if idx + 1 < len(_TOOL_FALLBACK_CHAIN):
            return _TOOL_FALLBACK_CHAIN[idx + 1]
    except ValueError:
        pass
    return None


def _build_fallback_plan(*, goal: str, tool: str) -> Dict[str, Any]:
    """Build a minimal conservative plan for the specified tool type."""
    action_type = {
        "api": "analyze",
        "browser": "browser",
        "desktop": "desktop",
    }.get(tool, "analyze")

    return {
        "reasoning": f"Fallback plan using {tool} after primary tool failure.",
        "actions": [
            {
                "type": action_type,
                "description": goal,
                "action": None,
                "source": None,
                "params": {},
                "url": None,
                "browser_url": None,
                "command": None,
                "seconds": 5.0,
            }
        ],
    }
