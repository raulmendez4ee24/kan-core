#!/usr/bin/env python3
"""
Smoke test: desktop + Telegram flow on Mac.

Run with runner connected and ENABLE_DESKTOP_AGENT=true. Sends goals via
AgentBridge/MasterBrain; expects real execution on the Mac.

Steps (manual or automated):
  1. "Abre Chrome"
  2. "Ve a google.com"
  3. "Busca 'kan logic sistem'"
  4. "Abre kanlogicsistem.com"
  5. "Toma screenshot y dime qué ves"

Usage:
  # From repo root, with .env loaded (BACKEND_URL, JARVIS_CLIENT_ID, etc.)
  python scripts/smoke_desktop_telegram.py

  # Or only print the steps for manual testing in Telegram
  python scripts/smoke_desktop_telegram.py --dry-run
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys


SMOKE_GOALS = [
    "Abre Chrome",
    "Ve a google.com",
    "Busca 'kan logic sistem'",
    "Abre kanlogicsistem.com",
    "Toma screenshot y dime qué ves",
]


def main() -> None:
    parser = argparse.ArgumentParser(description="Smoke desktop + Telegram")
    parser.add_argument("--dry-run", action="store_true", help="Only print goals, do not call API")
    args = parser.parse_args()
    if args.dry_run:
        print("Smoke steps (send these in Telegram with runner connected):")
        for i, goal in enumerate(SMOKE_GOALS, 1):
            print(f"  {i}. {goal}")
        return
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    asyncio.run(_run_smoke())


async def _run_smoke() -> None:
    from brain.agent_bridge import AgentBridge
    client_id = os.getenv("JARVIS_CLIENT_ID") or os.getenv("TELEGRAM_DEFAULT_CLIENT_ID")
    if not client_id:
        print("Set JARVIS_CLIENT_ID or TELEGRAM_DEFAULT_CLIENT_ID")
        return
    bridge = AgentBridge(client_id=client_id, max_iterations=6)
    for i, goal in enumerate(SMOKE_GOALS, 1):
        print(f"[{i}/{len(SMOKE_GOALS)}] Goal: {goal}")
        try:
            result = await bridge.run(goal)
            status = result.get("status", "?")
            summary = (result.get("summary") or "")[:200]
            print(f"  -> status={status} summary={summary}")
            if result.get("error"):
                print(f"  error={result.get('error')}")
        except Exception as e:
            print(f"  -> exception: {e}")
        await asyncio.sleep(1)
    print("Smoke done.")


if __name__ == "__main__":
    main()
