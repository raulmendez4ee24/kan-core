"""Autodiagnóstico: envs efectivos, OpenAI, Telegram, n8n, runner, DB. Usado por /doctor y /console/doctor."""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Dict, List

logger = logging.getLogger("kan_core.doctor")

# Env keys that affect loops / behaviour (for doctor report)
LOOP_FLAG_KEYS = [
    "MASTER_BRAIN_ENABLE_WEB_RESEARCH",
    "MASTER_BRAIN_ENABLE_INTENT_LLM",
    "TELEGRAM_FLOW_MODE",
    "TELEGRAM_AGENT_MODE",
    "ENABLE_LLM_SANDBOX",
]


async def _check_openai() -> Dict[str, Any]:
    key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not key or key.startswith("__") or key == "sk-...":
        return {"ok": False, "reason": "OPENAI_API_KEY missing or placeholder"}
    try:
        import httpx
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.get(
                "https://api.openai.com/v1/models",
                headers={"Authorization": f"Bearer {key}"},
            )
        if r.status_code == 200:
            return {"ok": True, "reason": "ok"}
        return {"ok": False, "reason": f"http_{r.status_code}"}
    except Exception as e:
        return {"ok": False, "reason": str(e)[:80]}


async def _check_telegram_send(chat_id_override: str | None = None) -> Dict[str, Any]:
    token = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    if not token:
        return {"ok": False, "reason": "TELEGRAM_BOT_TOKEN missing"}
    chat_id = chat_id_override or os.getenv("TELEGRAM_DOCTOR_CHAT_ID", "").strip()
    if not chat_id:
        return {"ok": True, "reason": "token_set_send_skipped_no_chat"}
    try:
        import httpx
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": "Doctor check: Telegram OK"},
            )
        data = r.json() if r.text else {}
        if data.get("ok") is True:
            return {"ok": True, "reason": "ok"}
        return {"ok": False, "reason": str(data.get("description", r.status_code))[:80]}
    except Exception as e:
        return {"ok": False, "reason": str(e)[:80]}


async def _check_n8n_webhook() -> Dict[str, Any]:
    url = (os.getenv("N8N_WEBHOOK_URL") or "").strip()
    if not url:
        return {"ok": False, "reason": "N8N_WEBHOOK_URL missing"}
    try:
        from tools.n8n_client import send_to_n8n_with_response
        result = await send_to_n8n_with_response({"source": "doctor_check", "test": True})
        dispatched = result.get("dispatched", False)
        return {"ok": dispatched, "reason": "ok" if dispatched else "request_failed", "status_code": result.get("status_code")}
    except Exception as e:
        return {"ok": False, "reason": str(e)[:80]}


def _env_effective() -> Dict[str, str]:
    """Effective env (no secrets, just set/missing)."""
    keys = [
        "DATABASE_URL", "OPENAI_API_KEY", "TELEGRAM_BOT_TOKEN", "N8N_WEBHOOK_URL",
        "JARVIS_CLIENT_ID", "RUNNER_MODE", "ENABLE_DESKTOP_AGENT", "RUN_DB_MIGRATIONS",
        "AUTONOMY_LEVEL", "TELEGRAM_FLOW_MODE", "TELEGRAM_AGENT_MODE",
    ]
    out: Dict[str, str] = {}
    for k in keys:
        v = os.getenv(k, "")
        if not v or v.startswith("__") or "SECRET" in k.upper() or "KEY" in k.upper() or "TOKEN" in k.upper():
            out[k] = "set" if (v and len(v) > 4) else "missing"
        else:
            out[k] = "set"
    return out


def _cloud_vs_local() -> str:
    if os.getenv("RAILWAY_ENVIRONMENT") or os.getenv("HEROKU_APP_NAME") or os.getenv("KUBERNETES_SERVICE_HOST"):
        return "cloud"
    if os.getenv("RUNNER_MODE") == "remote":
        return "cloud_headless"
    return "local"


async def _runner_connected(client_id: str | None) -> Dict[str, Any]:
    """Check if a desktop runner is connected (WS). Keys are agent UUIDs."""
    try:
        from routes import desktop_agent as desktop_agent_routes
        manager = getattr(desktop_agent_routes, "_ws_manager", None)
        if manager is None:
            return {"connected": False, "reason": "no_ws_manager", "connected_count": 0}
        conns = getattr(manager, "_connections", None) or {}
        count = len(conns)
        if client_id and count > 0:
            from database import AsyncSessionLocal
            from sqlalchemy import select
            from models import DesktopAgent
            from uuid import UUID
            try:
                org_uuid = UUID(client_id)
                async with AsyncSessionLocal() as session:
                    r = await session.execute(
                        select(DesktopAgent.id).where(DesktopAgent.organization_id == org_uuid)
                    )
                    agent_ids = [row[0] for row in r.fetchall() if row]
                for aid in agent_ids:
                    if aid in conns:
                        return {"connected": True, "reason": "ok", "connected_count": count}
                return {"connected": False, "reason": "no_runner_for_client", "connected_count": count}
            except Exception:
                pass
        return {"connected": count > 0, "connected_count": count, "reason": "ok"}
    except Exception as e:
        return {"connected": False, "reason": str(e)[:60], "connected_count": 0}


async def _db_mismatches() -> List[Dict[str, Any]]:
    """Detect common DB issues (e.g. objective_runs.client_id type mismatch)."""
    issues: List[Dict[str, Any]] = []
    try:
        from database import AsyncSessionLocal
        from sqlalchemy import text
        async with AsyncSessionLocal() as session:
            # Check if objective_runs exists and has client_id
            try:
                r = await session.execute(text(
                    "SELECT column_name, data_type FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = 'objective_runs'"
                ))
                rows = r.fetchall()
            except Exception:
                rows = []
            if rows:
                col_types = {str(r[0]): str(r[1]) for r in rows if r and len(r) >= 2}
                if col_types.get("client_id") == "character varying" and "organization_id" not in col_types:
                    issues.append({
                        "table": "objective_runs",
                        "issue": "client_id may be string; learning expects organization_id UUID",
                        "fix": "Alembic migration or bootstrap to add organization_id / align types",
                    })
    except Exception as e:
        issues.append({"error": str(e)[:120]})
    return issues


async def run_doctor_checks(
    *,
    chat_id: str | None = None,
    client_id: str | None = None,
) -> Dict[str, Any]:
    """Run all doctor checks. Returns structured result for /console/doctor and Telegram /doctor."""
    env_effective = _env_effective()
    openai_task = asyncio.create_task(_check_openai())
    telegram_task = asyncio.create_task(_check_telegram_send(chat_id))
    n8n_task = asyncio.create_task(_check_n8n_webhook())
    runner_task = asyncio.create_task(_runner_connected(client_id))
    db_task = asyncio.create_task(_db_mismatches())

    openai_ok = await openai_task
    telegram_ok = await telegram_task
    n8n_ok = await n8n_task
    runner = await runner_task
    db_issues = await db_task

    loop_flags = {k: os.getenv(k, "")[:20] or "(unset)" for k in LOOP_FLAG_KEYS}

    return {
        "env_effective": env_effective,
        "openai": openai_ok,
        "telegram_send": telegram_ok,
        "n8n_webhook": n8n_ok,
        "cloud_vs_local": _cloud_vs_local(),
        "runner": runner,
        "loop_flags": loop_flags,
        "db_issues": db_issues,
        "summary_ok": (
            openai_ok.get("ok") is True
            and (telegram_ok.get("ok") is True or telegram_ok.get("reason") == "token_set_send_skipped_no_chat")
        ),
    }
