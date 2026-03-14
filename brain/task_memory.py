"""Task-level memory for the LLM sandbox.

Records successful action sequences (trajectories) so future runs can:
  1. Start with a suggested plan from a similar past run (few-shot).
  2. Avoid actions that previously failed for the same goal type.
  3. Report aggregate success rates per goal category.

Storage: PostgreSQL (AutomationRun + BusinessEvent tables via AsyncSessionLocal).
Fallback: in-memory dict when DB is unavailable.

API:
    record_run(result)       — called after a successful sandbox run
    get_similar_runs(goal)   — returns up to 3 past trajectories for context injection
    format_memory_hint(runs) — returns a string injected at the top of the ReAct prompt
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger("kan_core.task_memory")

# In-memory fallback (single-process only, cleared on restart)
_mem_store: Dict[str, List[Dict[str, Any]]] = {}
_MAX_STORED_PER_KEY = 5


def _goal_category(goal: str) -> str:
    """Reduce a goal string to a coarse category key for similarity matching.

    Uses keyword extraction — not LLM-based, intentionally fast.
    """
    goal_lower = goal.lower()
    categories = [
        ("discover", ["discover", "descubr", "find api", "encontrar api"]),
        ("crm", ["crm", "hubspot", "pipedrive", "contact", "lead", "cliente"]),
        ("email", ["email", "correo", "send mail", "enviar email", "newsletter"]),
        ("order", ["order", "pedido", "compra", "checkout", "carrito"]),
        ("payment", ["payment", "pago", "cobro", "factura", "stripe", "invoice"]),
        ("calendar", ["calendar", "calendly", "schedule", "reunion", "meeting"]),
        ("slack", ["slack", "notif", "mensaje", "channel"]),
        ("report", ["report", "reporte", "dashboard", "analyt", "metric"]),
        ("inventory", ["inventory", "inventario", "stock", "producto", "product"]),
        ("sync", ["sync", "sincron", "integr", "connect"]),
    ]
    for cat, keywords in categories:
        if any(kw in goal_lower for kw in keywords):
            return cat
    # Fallback: first 3 words
    words = re.sub(r"[^a-z ]", "", goal_lower).split()
    return "_".join(words[:3]) or "general"


def _trajectory_summary(steps: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """Extract a compact action summary from sandbox steps."""
    summary = []
    for s in steps:
        action = s.get("action") or {}
        if not action:
            continue
        summary.append({
            "action_type": str(action.get("action_type") or ""),
            "action": str(action.get("action") or ""),
            "integration": str(action.get("integration") or ""),
        })
    return summary


async def record_run(result: Any) -> None:
    """Persist a successful sandbox run to task memory.

    ``result`` is a SandboxResult instance or dict.
    Only records if the run ended with final_answer (true completion).
    """
    try:
        if hasattr(result, "model_dump"):
            data = result.model_dump()
        else:
            data = dict(result)

        if data.get("stopped_reason") != "final_answer":
            return
        if not data.get("final_answer"):
            return

        goal = str(data.get("goal") or "")
        category = _goal_category(goal)
        trajectory = _trajectory_summary(data.get("steps") or [])
        if not trajectory:
            return

        entry = {
            "goal": goal[:200],
            "category": category,
            "trajectory": trajectory,
            "iterations": data.get("iterations", 0),
            "total_tokens": data.get("total_tokens", 0),
            "client_id": str(data.get("client_id") or ""),
        }

        # Try persistent storage first
        await _persist_to_db(category, entry, client_id=entry["client_id"])
    except Exception as exc:
        logger.debug("record_run failed (non-critical): %s", exc)


async def _persist_to_db(category: str, entry: Dict[str, Any], client_id: str) -> None:
    """Save entry to BusinessEvent table as source='task_memory'."""
    try:
        import uuid as _uuid
        from database import AsyncSessionLocal
        from brain.business_monitor import ingest_business_event

        try:
            org_id = _uuid.UUID(client_id)
        except (ValueError, AttributeError):
            _store_in_memory(category, entry)
            return

        async with AsyncSessionLocal() as db:
            await ingest_business_event(
                db,
                organization_id=org_id,
                event_type="task_memory_record",
                source="task_memory",
                channel="sandbox",
                entity_id=category,
                payload=entry,
            )
    except Exception as exc:
        logger.debug("DB persist failed, using memory: %s", exc)
        _store_in_memory(category, entry)


def _store_in_memory(category: str, entry: Dict[str, Any]) -> None:
    existing = _mem_store.setdefault(category, [])
    existing.append(entry)
    # Keep only the last N per category
    if len(existing) > _MAX_STORED_PER_KEY:
        _mem_store[category] = existing[-_MAX_STORED_PER_KEY:]


async def get_similar_runs(goal: str, client_id: str = "", limit: int = 3) -> List[Dict[str, Any]]:
    """Return up to `limit` past successful trajectories similar to this goal."""
    category = _goal_category(goal)
    runs: List[Dict[str, Any]] = []

    # Try DB first
    try:
        import uuid as _uuid
        from database import AsyncSessionLocal
        from sqlalchemy import select, text
        from models import BusinessEvent

        try:
            org_id = _uuid.UUID(client_id)
        except (ValueError, AttributeError):
            org_id = None

        async with AsyncSessionLocal() as db:
            stmt = (
                select(BusinessEvent)
                .where(
                    BusinessEvent.source == "task_memory",
                    BusinessEvent.entity_id == category,
                )
            )
            if org_id:
                stmt = stmt.where(BusinessEvent.organization_id == org_id)
            stmt = stmt.order_by(BusinessEvent.created_at.desc()).limit(limit)
            rows = (await db.execute(stmt)).scalars().all()
            for row in rows:
                payload = row.payload or {}
                if isinstance(payload, str):
                    try:
                        payload = json.loads(payload)
                    except Exception:
                        continue
                runs.append(payload)
    except Exception as exc:
        logger.debug("DB lookup failed: %s", exc)

    # Fallback to in-memory
    if not runs and category in _mem_store:
        runs = list(_mem_store[category])[-limit:]

    return runs[:limit]


def format_memory_hint(runs: List[Dict[str, Any]]) -> str:
    """Format past trajectories as a context hint for the ReAct prompt."""
    if not runs:
        return ""
    lines = ["EXPERIENCIA PREVIA (ejemplos de runs exitosos similares):"]
    for i, run in enumerate(runs, 1):
        traj = run.get("trajectory") or []
        steps_text = " → ".join(
            f"{s.get('action_type','?')}:{s.get('action','?')}({s.get('integration','?')})"
            for s in traj[:6]
        )
        iters = run.get("iterations", "?")
        lines.append(f"  [{i}] Goal: {run.get('goal','')[:80]}")
        lines.append(f"       Secuencia: {steps_text}")
        lines.append(f"       Iteraciones: {iters}")
    lines.append("Usa esta experiencia como guía, no como regla fija.")
    return "\n".join(lines)
