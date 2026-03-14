import json
import logging
import os
from typing import Any, Dict, Optional

from schemas.autonomy_plan import AutonomyPlan

logger = logging.getLogger("kan_core.strict_planner")


class StrictPlanner:
    def __init__(self, *, max_retries: int = 3):
        self._max_retries = max(1, int(max_retries))

    async def build_plan(
        self,
        *,
        engine: Any,
        client_id: str,
        session_id: str,
        goal: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        ctx = dict(context or {})
        retries = max(1, int(os.getenv("STRICT_PLANNER_RETRIES", str(self._max_retries))))
        last_error = ""

        for attempt in range(1, retries + 1):
            prompt = self._build_prompt(
                goal=goal,
                context=ctx,
                attempt=attempt,
                previous_error=last_error or None,
            )
            result = await engine.generate_response(
                client_id=client_id,
                session_id=session_id,
                message=prompt,
            )
            content = str(result.get("content") or "")
            parsed = self._parse_plan(content)
            if parsed:
                return {
                    "plan": parsed.model_dump(),
                    "raw_response": content,
                    "strict": True,
                    "attempt": attempt,
                }
            last_error = f"Invalid planner payload at attempt {attempt}"

        logger.warning("strict_planner_fallback goal=%s error=%s", goal[:120], last_error)
        fallback = AutonomyPlan(
            reasoning="Fallback planner generated a conservative task-oriented sequence.",
            actions=[
                {
                    "type": "analyze",
                    "description": goal,
                }
            ],
        )
        return {
            "plan": fallback.model_dump(),
            "raw_response": last_error,
            "strict": False,
            "attempt": retries,
        }

    def _parse_plan(self, text: str) -> Optional[AutonomyPlan]:
        candidate = (text or "").strip()
        if not candidate:
            return None

        if candidate.startswith("```"):
            candidate = candidate.strip("`\n ")
            if candidate.lower().startswith("json"):
                candidate = candidate[4:].strip()

        start = candidate.find("{")
        end = candidate.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        json_blob = candidate[start : end + 1]

        try:
            data = json.loads(json_blob)
        except json.JSONDecodeError:
            return None

        try:
            return AutonomyPlan.model_validate(data)
        except Exception:
            return None

    def _build_prompt(
        self,
        *,
        goal: str,
        context: Dict[str, Any],
        attempt: int,
        previous_error: Optional[str] = None,
    ) -> str:
        context_json = json.dumps(context, ensure_ascii=True)
        correction = (
            f"Previous validation error: {previous_error}. Fix your JSON and retry. "
            if previous_error
            else ""
        )
        return (
            "Return ONLY valid JSON matching this schema exactly: "
            '{"reasoning":"string","actions":[{"type":"browser|desktop|discover_apis|wait|analyze",'
            '"action":"string|null","source":"url|browser|terminal|null","params":{},'
            '"url":"string|null","browser_url":"string|null","command":"string|null",'
            '"seconds":1.0,"description":"string|null"}]}. '\
            "No markdown, no extra keys. "
            f"{correction}"
            f"Attempt: {attempt}. Goal: {goal}. Context: {context_json}."
        )
