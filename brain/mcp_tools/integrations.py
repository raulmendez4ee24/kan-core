from __future__ import annotations

from typing import Any, Dict

from brain.execution_guard import observe_guarded_execution, plan_guarded_execution
from tools.n8n_client import send_to_n8n_with_response


async def dispatch_n8n_fallback_tool(args: Dict[str, Any]) -> Dict[str, Any]:
    payload = dict(args.get("payload") or {})
    if "source" not in payload:
        payload["source"] = "mcp_fallback"
    return await send_to_n8n_with_response(payload)


async def run_guarded_http_action_tool(args: Dict[str, Any]) -> Dict[str, Any]:
    payload = dict(args.get("payload") or {})
    if "method" not in payload:
        payload["method"] = str(args.get("method") or "GET").upper()
    if "url" not in payload and args.get("url"):
        payload["url"] = str(args.get("url"))
    plan = plan_guarded_execution(payload, channel="http")
    result = {
        "status": "paused" if plan.get("should_pause") else "accepted",
        "completed": not bool(plan.get("should_pause")),
        "guardrail": plan,
    }
    result["tripwire"] = observe_guarded_execution(plan, result)
    return result

