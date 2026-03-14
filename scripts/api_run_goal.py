#!/usr/bin/env python3
"""
Run a desktop autonomy goal via the REST API (no Telegram, no local AgentBridge).

Use when the backend is remote (e.g. Railway). Requires BACKEND_URL, X-Client-Id and X-Client-Token.

  export BACKEND_URL="https://tu-app.up.railway.app"
  export CLIENT_ID="<uuid>"
  export CLIENT_TOKEN="<token>"
  python scripts/api_run_goal.py "Abre Chrome"
  python scripts/api_run_goal.py "Abre Chrome" --autonomous
"""
from __future__ import annotations

import argparse
import json
import os
import sys


def main() -> None:
    parser = argparse.ArgumentParser(description="Run desktop goal via REST API")
    parser.add_argument("goal", nargs="+", help="Goal text (e.g. 'Abre Chrome')")
    parser.add_argument(
        "--autonomous",
        action="store_true",
        help="Use execution_mode=autonomous (no confirmations)",
    )
    parser.add_argument("--max-steps", type=int, default=8, help="Max steps (default 8)")
    parser.add_argument("--dry-run", action="store_true", help="Only plan, do not execute")
    parser.add_argument("--timeout", type=int, default=120, help="Request timeout seconds")
    args = parser.parse_args()
    goal = " ".join(args.goal).strip()
    if not goal:
        print("Goal vacío.")
        sys.exit(1)
    base_url = (os.getenv("BACKEND_URL") or os.getenv("API_URL") or "").rstrip("/")
    client_id = os.getenv("CLIENT_ID") or os.getenv("X_CLIENT_ID") or os.getenv("JARVIS_CLIENT_ID")
    client_token = (
        os.getenv("CLIENT_TOKEN")
        or os.getenv("X_CLIENT_TOKEN")
    )
    if not base_url:
        print("Configura BACKEND_URL o API_URL")
        sys.exit(1)
    if not client_id or not client_token:
        print("Configura CLIENT_ID y CLIENT_TOKEN (o X_CLIENT_ID / X_CLIENT_TOKEN)")
        sys.exit(1)
    url = f"{base_url}/desktop-autonomy/run"
    headers = {
        "Content-Type": "application/json",
        "X-Client-Id": client_id,
        "X-Client-Token": client_token,
    }
    payload = {
        "goal": goal,
        "max_steps": args.max_steps,
        "execution_mode": "autonomous" if args.autonomous else "balanced",
        "wait_for_results": True,
        "dry_run": args.dry_run,
    }
    try:
        import urllib.request
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode(),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=args.timeout) as resp:
            data = json.loads(resp.read().decode())
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)
    status = data.get("status", "?")
    summary = (data.get("summary") or "").strip()
    print("status:", status)
    print(summary or "(sin resumen)")
    if data.get("error"):
        print("error:", data["error"])
    if status == "runner_offline":
        print("\n--- Instrucciones en summary arriba ---")
    sys.exit(0 if status in ("completed", "dry_run", "runner_offline") else 1)


if __name__ == "__main__":
    main()
