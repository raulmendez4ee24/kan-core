#!/usr/bin/env python3
"""
Run a single goal without Telegram: uses AgentBridge (MasterBrain + desktop loop).

Requires: JARVIS_CLIENT_ID or TELEGRAM_DEFAULT_CLIENT_ID in env.
Runner must be connected on the Mac for desktop actions.

  python scripts/cli_run_goal.py "Abre Chrome"
  python scripts/cli_run_goal.py "Ve a google.com y busca kan logic"
"""
from __future__ import annotations

import asyncio
import os
import sys


def main() -> None:
    if len(sys.argv) < 2:
        print("Uso: python scripts/cli_run_goal.py \"<objetivo>\"")
        sys.exit(1)
    goal = " ".join(sys.argv[1:]).strip()
    if not goal:
        print("Objetivo vacío.")
        sys.exit(1)
    client_id = os.getenv("JARVIS_CLIENT_ID") or os.getenv("TELEGRAM_DEFAULT_CLIENT_ID")
    if not client_id:
        print("Configura JARVIS_CLIENT_ID o TELEGRAM_DEFAULT_CLIENT_ID")
        sys.exit(1)
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    result = asyncio.run(_run(client_id, goal))
    status = result.get("status", "?")
    summary = (result.get("summary") or "").strip()
    print(f"status: {status}")
    print(summary or "(sin resumen)")
    if result.get("error"):
        print(f"error: {result['error']}")
    sys.exit(0 if status in ("completed", "runner_offline") else 1)


async def _run(client_id: str, goal: str) -> dict:
    from brain.agent_bridge import AgentBridge
    bridge = AgentBridge(client_id=client_id, max_iterations=8)
    return await bridge.run(goal)


if __name__ == "__main__":
    main()
