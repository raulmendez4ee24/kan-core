from __future__ import annotations

from typing import Any, Dict, Optional

from browser_agent.web_agent import WebAgent


class WebAgentLoop(WebAgent):
    """
    Explicit runtime loop wrapper.
    Reuses WebAgent's observe/decide/act/verify loop while exposing
    run(goal, start_url=...) for pipeline integration.
    """

    async def run(self, goal: str, start_url: Optional[str] = None) -> Dict[str, Any]:
        target_goal = str(goal or "").strip()
        target_url = str(start_url or "").strip()

        if target_url:
            try:
                await self.open(target_url)
            except Exception as exc:
                return {
                    "success": False,
                    "summary": f"Failed to open start URL: {exc}",
                    "data": {},
                    "pages_visited": list(self.pages_visited),
                    "actions": list(self.actions_done),
                    "results": list(self.results),
                }
            if not target_goal:
                target_goal = f"Complete web task on {target_url}"

        return await super().run(target_goal)

