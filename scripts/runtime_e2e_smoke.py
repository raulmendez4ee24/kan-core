#!/usr/bin/env python3
"""End-to-end smoke for runtime kernel + event replay + hook registry.

Checks:
1) Runtime hook registration persists and versions.
2) Runtime start returns run_id.
3) Wait endpoint returns terminal/timeout status.
4) Events endpoint returns lifecycle events (replay-capable path).
"""

from __future__ import annotations

import argparse
import json
import os
import time
from typing import Any, Dict

import httpx


def _headers(client_id: str, token: str) -> Dict[str, str]:
    return {
        "X-Client-Id": client_id,
        "X-Client-Token": token,
        "Content-Type": "application/json",
    }


def _expect_ok(resp: httpx.Response, ctx: str) -> Dict[str, Any]:
    if resp.status_code >= 400:
        raise SystemExit(f"{ctx} failed: HTTP {resp.status_code} body={resp.text[:800]}")
    try:
        return resp.json()
    except Exception as exc:
        raise SystemExit(f"{ctx} returned non-JSON: {exc}") from exc


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=os.getenv("KAN_BASE_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--client-id", default=os.getenv("KAN_CLIENT_ID", ""))
    parser.add_argument("--token", default=os.getenv("KAN_CLIENT_TOKEN", ""))
    parser.add_argument("--timeout", type=float, default=120.0)
    args = parser.parse_args()

    if not args.client_id or not args.token:
        raise SystemExit("KAN_CLIENT_ID and KAN_CLIENT_TOKEN are required")

    base = args.base_url.rstrip("/")
    headers = _headers(args.client_id, args.token)
    suffix = str(int(time.time()))

    with httpx.Client(timeout=args.timeout) as client:
        # 0) health
        health = client.get(f"{base}/healthz")
        if health.status_code >= 400:
            raise SystemExit(f"healthz failed: {health.status_code} {health.text[:300]}")

        # 1) register hook
        hook_payload = {
            "event": "before_tool",
            "mode": "annotate",
            "metadata": {"smoke": True, "scope": "runtime_e2e", "ts": suffix},
        }
        reg = _expect_ok(
            client.post(f"{base}/runtime/hooks/register", headers=headers, json=hook_payload),
            "hook/register",
        )
        regs = list(reg.get("registrations") or [])
        if not regs:
            raise SystemExit("hook/register did not return registrations")
        latest = regs[-1]
        if str(latest.get("event")) != "before_tool":
            raise SystemExit(f"unexpected hook event in latest registration: {latest}")

        # 2) start runtime
        session_id = f"smoke-session-{suffix}"
        start_payload = {
            "session_id": session_id,
            "goal": "Smoke runtime kernel and event replay flow",
            "max_steps": 2,
            "execution_mode": "balanced",
            "context": {"smoke": True},
            "dry_run": True,
        }
        started = _expect_ok(
            client.post(f"{base}/runtime/start", headers=headers, json=start_payload),
            "runtime/start",
        )
        run_id = str(started.get("run_id") or "")
        if not run_id:
            raise SystemExit(f"runtime/start missing run_id: {started}")

        # 3) wait
        waited = _expect_ok(
            client.get(f"{base}/runtime/wait/{run_id}?timeout_seconds=90", headers=headers),
            "runtime/wait",
        )
        status = str(waited.get("status") or "")
        if status not in {"completed", "failed", "blocked", "timeout"}:
            raise SystemExit(f"unexpected runtime/wait status={status} payload={waited}")

        # 4) events
        events = _expect_ok(
            client.get(f"{base}/runtime/events/{run_id}?after_seq=0", headers=headers),
            "runtime/events",
        )
        items = list(events.get("items") or [])
        if not items:
            raise SystemExit(f"runtime/events empty for run_id={run_id}")
        event_types = {str(item.get("event_type") or "") for item in items}
        if not any(x.startswith("lifecycle.") for x in event_types):
            raise SystemExit(f"runtime/events missing lifecycle.* events: {sorted(event_types)}")

        out = {
            "ok": True,
            "run_id": run_id,
            "wait_status": status,
            "events_count": len(items),
            "event_types": sorted(event_types),
            "hook_latest_version": latest.get("version"),
        }
        print(json.dumps(out, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
