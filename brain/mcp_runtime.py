from __future__ import annotations

import os
from typing import Any, Dict, List
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession


def mcp_enabled() -> bool:
    return os.getenv("ENABLE_INPROCESS_MCP_RUNTIME", "true").lower() in {"1", "true", "yes"}


def get_tool_catalog() -> List[Dict[str, Any]]:
    return [
        {
            "name": "execute_case_action",
            "description": "Execute a normalized case action using the internal execution router.",
            "input_schema": {"type": "object", "properties": {"step": {"type": "object"}}},
            "reversible": True,
        },
        {
            "name": "get_case_state",
            "description": "Return the latest persisted state for a specific autonomous case.",
            "input_schema": {"type": "object", "properties": {"case_id": {"type": "string"}}},
            "reversible": True,
        },
        {
            "name": "run_case",
            "description": "Run one autonomous case through its current decision loop.",
            "input_schema": {"type": "object", "properties": {"case_id": {"type": "string"}}},
            "reversible": False,
        },
        {
            "name": "list_case_queue",
            "description": "List queued autonomous cases ordered by priority and age.",
            "input_schema": {"type": "object", "properties": {"limit": {"type": "integer"}}},
            "reversible": True,
        },
        {
            "name": "run_case_queue",
            "description": "Process a batch of queued autonomous cases.",
            "input_schema": {"type": "object", "properties": {"limit": {"type": "integer"}}},
            "reversible": False,
        },
        {
            "name": "get_business_state",
            "description": "Load or refresh the current business state snapshot.",
            "input_schema": {"type": "object", "properties": {"refresh": {"type": "boolean"}, "window_minutes": {"type": "integer"}}},
            "reversible": True,
        },
        {
            "name": "dispatch_n8n_fallback",
            "description": "Dispatch a payload through the n8n fallback channel.",
            "input_schema": {"type": "object", "properties": {"payload": {"type": "object"}}},
            "reversible": False,
        },
        {
            "name": "run_guarded_http_action",
            "description": "Plan and validate an HTTP action through execution guardrails.",
            "input_schema": {"type": "object", "properties": {"payload": {"type": "object"}}},
            "reversible": True,
        },
        {
            "name": "run_rollback",
            "description": "Execute a rollback or compensation using rollback metadata.",
            "input_schema": {"type": "object", "properties": {"metadata": {"type": "object"}}},
            "reversible": False,
        },
        {
            "name": "get_rollbackable_guardrails",
            "description": "List recent rollbackable execution guardrail events.",
            "input_schema": {"type": "object", "properties": {"limit": {"type": "integer"}}},
            "reversible": True,
        },
    ]


def _tool_map() -> Dict[str, Any]:
    from brain.mcp_tools.business_ops import (
        execute_case_action,
        get_business_state_tool,
        get_case_state_tool,
        list_case_queue_tool,
        run_case_queue_tool,
        run_case_tool,
    )
    from brain.mcp_tools.guardrails import (
        get_rollbackable_guardrails_tool,
        run_rollback_tool,
    )
    from brain.mcp_tools.integrations import (
        dispatch_n8n_fallback_tool,
        run_guarded_http_action_tool,
    )

    return {
        "execute_case_action": execute_case_action,
        "get_case_state": get_case_state_tool,
        "run_case": run_case_tool,
        "list_case_queue": list_case_queue_tool,
        "run_case_queue": run_case_queue_tool,
        "get_business_state": get_business_state_tool,
        "dispatch_n8n_fallback": dispatch_n8n_fallback_tool,
        "run_guarded_http_action": run_guarded_http_action_tool,
        "run_rollback": run_rollback_tool,
        "get_rollbackable_guardrails": get_rollbackable_guardrails_tool,
    }


async def execute_tool(
    tool_name: str,
    *,
    args: Dict[str, Any],
    session: AsyncSession | None = None,
    organization_id: UUID | None = None,
) -> Dict[str, Any]:
    tool = _tool_map().get(str(tool_name or "").strip())
    if not tool:
        raise ValueError(f"Unknown MCP tool '{tool_name}'")
    if tool_name in {"dispatch_n8n_fallback", "run_guarded_http_action"}:
        return await tool(dict(args or {}))
    if session is None or organization_id is None:
        raise ValueError("session and organization_id are required for this tool")
    return await tool(session, organization_id=organization_id, args=dict(args or {}))
