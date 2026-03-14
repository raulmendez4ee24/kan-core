#!/usr/bin/env python3
"""Continuous eval harness for desktop autonomy runs.

Dual-eval design:
- competitive (operational): throughput/latency/cost profile.
- real_outcome: stricter profile requiring evidence and executed outcomes.
"""

import argparse
import asyncio
import json
import os
import re
import time
from datetime import datetime, timezone
from statistics import mean

import httpx


SUITES = {
    "sales_ops": [
        {"goal": "Open the configured dashboard and capture status evidence"},
        {"goal": "Navigate reports and attempt monthly report generation"},
        {"goal": "Validate login flow and collect screenshot evidence"},
    ],
    "support_ops": [
        {"goal": "Open support queue, prioritize urgent tickets, and capture evidence"},
        {"goal": "Search unresolved conversations and extract current status"},
        {"goal": "Validate escalation path and produce screenshot evidence"},
    ],
    "growth_ops": [
        {"goal": "Open campaign analytics and summarize top KPI changes"},
        {"goal": "Navigate CRM leads list and prepare follow-up hints"},
        {"goal": "Capture visual evidence from growth dashboard widgets"},
    ],
}

CANARY_TASK = {"goal": "Run a minimal health check flow and capture a screenshot"}
SCREENSHOT_ACTIONS = {"take_screenshot", "browser_screenshot"}

PROFILES = {
    "competitive": {
        "execution_mode": "autonomous",
        "wait_for_results": False,
        "step_timeout_seconds": 25.0,
        "stop_on_failure": True,
        "enforce_quality_gate": True,
        "quality_min_score": 0.35,
        "dynamic_risk_controls": False,
        "allow_high_risk_auto_approval": True,
    },
    "real_outcome": {
        "execution_mode": "autonomous",
        "wait_for_results": True,
        "step_timeout_seconds": 45.0,
        "stop_on_failure": False,
        "enforce_quality_gate": True,
        "quality_min_score": 0.65,
        "dynamic_risk_controls": True,
        "allow_high_risk_auto_approval": False,
    },
}


def _as_bool(value: str, default: bool) -> bool:
    token = str(value or "").strip().lower()
    if not token:
        return bool(default)
    if token in {"1", "true", "yes", "on"}:
        return True
    if token in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _extract_number(summary: str, token: str) -> int:
    pattern = rf"(\d+)\s+{re.escape(token)}"
    m = re.search(pattern, summary.lower())
    if not m:
        return 0
    try:
        return int(m.group(1))
    except Exception:
        return 0


def _analyze_result(data: dict) -> dict:
    steps = list(data.get("steps") or [])
    evaluate_ok = 0
    screenshot_ok = 0
    for step in steps:
        if not isinstance(step, dict):
            continue
        phase = str(step.get("phase") or "")
        status = str(step.get("status") or "")
        action = str(step.get("action") or "")
        if phase == "evaluate" and status == "ok":
            evaluate_ok += 1
        if action in SCREENSHOT_ACTIONS and status in {"ok", "queued"}:
            screenshot_ok += 1

    summary = str(data.get("summary") or "")
    executed_ok = _extract_number(summary, "executed ok")
    recovered = _extract_number(summary, "recovered")
    self_healed = _extract_number(summary, "self-healed")

    return {
        "steps_count": len(steps),
        "evaluate_ok_count": evaluate_ok,
        "screenshot_evidence_count": screenshot_ok,
        "executed_ok_count": executed_ok,
        "recovered_count": recovered,
        "self_healed_count": self_healed,
    }


async def _run_task(client: httpx.AsyncClient, *, base_url: str, token: str, payload: dict) -> dict:
    started = time.perf_counter()
    req_headers = dict(payload.get("headers") or {})
    if not req_headers:
        req_headers = {"Authorization": f"Bearer {token}"}
    try:
        resp = await client.post(
            f"{base_url.rstrip('/')}/desktop-autonomy/run",
            headers=req_headers,
            json={
                "goal": payload["goal"],
                "max_steps": int(payload.get("max_steps") or 6),
                "execution_mode": str(payload.get("execution_mode") or "autonomous"),
                "wait_for_results": bool(payload.get("wait_for_results", False)),
                "step_timeout_seconds": float(payload.get("step_timeout_seconds") or 25.0),
                "stop_on_failure": bool(payload.get("stop_on_failure", True)),
                "enable_recovery": True,
                "enable_self_healing": True,
                "enforce_quality_gate": bool(payload.get("enforce_quality_gate", True)),
                "quality_min_score": float(payload.get("quality_min_score") or 0.35),
                "dynamic_risk_controls": bool(payload.get("dynamic_risk_controls", False)),
                "context": dict(payload.get("context") or {}),
            },
            timeout=120,
        )
        latency = time.perf_counter() - started
        data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
    except Exception as exc:
        latency = time.perf_counter() - started
        return {
            "status_code": 0,
            "latency_seconds": round(latency, 3),
            "status": "failed",
            "summary": None,
            "error": str(exc),
        }
    return {
        "status_code": resp.status_code,
        "latency_seconds": round(latency, 3),
        "status": data.get("status"),
        "summary": data.get("summary"),
        "error": data.get("error"),
        **_analyze_result(data if isinstance(data, dict) else {}),
    }


def _compute_score(results: list[dict], *, canary: dict, mode: str) -> dict:
    latencies = [float(x.get("latency_seconds") or 0.0) for x in results]
    p95_latency = 0.0
    if latencies:
        sorted_lat = sorted(latencies)
        p95_idx = max(0, min(len(sorted_lat) - 1, int(round(0.95 * (len(sorted_lat) - 1)))))
        p95_latency = round(float(sorted_lat[p95_idx]), 3)

    if mode == "competitive":
        ok = [x for x in results if str(x.get("status") or "") == "completed"]
    else:
        # Real outcome success requires:
        # completed + (execution signal or recovery/heal) + screenshot evidence.
        ok = []
        for row in results:
            if str(row.get("status") or "") != "completed":
                continue
            outcome_signal = (
                int(row.get("executed_ok_count") or 0) > 0
                or int(row.get("recovered_count") or 0) > 0
                or int(row.get("self_healed_count") or 0) > 0
                or int(row.get("evaluate_ok_count") or 0) > 0
            )
            evidence_signal = int(row.get("screenshot_evidence_count") or 0) > 0
            if outcome_signal and evidence_signal:
                ok.append(row)

    blocked_or_failed = [x for x in results if str(x.get("status") or "") in {"failed", "blocked"}]
    recovery_proxy = round(max(0.0, min(1.0, 1.0 - (len(blocked_or_failed) / max(1, len(results))))), 4)
    cost_proxy = round(sum(float(x.get("latency_seconds") or 0.0) for x in results) * 0.0002, 6)
    stability_proxy = round(
        max(0.0, min(1.0, (len(ok) / max(1, len(results))) - (0.1 if canary.get("status") != "completed" else 0.0))),
        4,
    )
    composite_score = round(
        (0.35 * (len(ok) / max(1, len(results))))
        + (0.2 * recovery_proxy)
        + (0.2 * max(0.0, min(1.0, 1.0 - (p95_latency / 90.0))))
        + (0.15 * max(0.0, min(1.0, 1.0 - min(cost_proxy, 1.0))))
        + (0.1 * stability_proxy),
        4,
    )
    return {
        "success_rate": round((len(ok) / max(1, len(results))), 4),
        "task_success_rate": round((len(ok) / max(1, len(results))), 4),
        "avg_latency_seconds": round(mean(latencies), 3) if latencies else 0.0,
        "p95_latency_seconds": p95_latency,
        "recovery_proxy": recovery_proxy,
        "recovery_success_rate": recovery_proxy,
        "cost_proxy": cost_proxy,
        "cost_per_run_usd": round((cost_proxy / max(1, len(results))), 6),
        "stability_proxy": stability_proxy,
        "composite_score": composite_score,
        "total_runs": len(results),
        "canary_status": canary.get("status"),
        "mode": mode,
    }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=os.getenv("KAN_API_BASE_URL", "http://localhost:8000"))
    parser.add_argument("--token", default=os.getenv("KAN_CLIENT_TOKEN", ""))
    parser.add_argument("--client-id", default=os.getenv("KAN_CLIENT_ID", ""))
    parser.add_argument("--out", default=".run/weekly_eval_report.json")
    parser.add_argument(
        "--history-out",
        default=os.getenv("WEEKLY_EVAL_HISTORY_PATH", ".run/weekly_eval_history.jsonl"),
        help="Path to append run summaries as JSONL for release gating.",
    )
    parser.add_argument("--suite", default="all", choices=["all", "sales_ops", "support_ops", "growth_ops"])
    parser.add_argument(
        "--eval-profiles",
        default=os.getenv("EVAL_PROFILES", "competitive,real_outcome"),
        help="comma-separated: competitive,real_outcome",
    )
    parser.add_argument("--execution-mode", default=os.getenv("EVAL_EXECUTION_MODE", "autonomous"))
    parser.add_argument(
        "--wait-for-results",
        default=os.getenv("EVAL_WAIT_FOR_RESULTS", "false"),
        help="true|false",
    )
    parser.add_argument(
        "--enforce-quality-gate",
        default=os.getenv("EVAL_ENFORCE_QUALITY_GATE", "true"),
        help="true|false",
    )
    parser.add_argument(
        "--quality-min-score",
        type=float,
        default=float(os.getenv("EVAL_QUALITY_MIN_SCORE", "0.35")),
    )
    parser.add_argument(
        "--dynamic-risk-controls",
        default=os.getenv("EVAL_DYNAMIC_RISK_CONTROLS", "false"),
        help="true|false",
    )
    parser.add_argument(
        "--allow-high-risk-auto-approval",
        default=os.getenv("EVAL_ALLOW_HIGH_RISK_AUTO_APPROVAL", "true"),
        help="true|false",
    )
    parser.add_argument("--slo-success-rate", type=float, default=float(os.getenv("SLO_SUCCESS_RATE", "0.92")))
    parser.add_argument("--slo-recovery-rate", type=float, default=float(os.getenv("SLO_RECOVERY_RATE", "0.70")))
    parser.add_argument("--slo-p95-latency-seconds", type=float, default=float(os.getenv("SLO_P95_LATENCY_SECONDS", "60")))
    parser.add_argument(
        "--slo-outcome-success-rate",
        type=float,
        default=float(os.getenv("SLO_OUTCOME_SUCCESS_RATE", "0.80")),
    )
    parser.add_argument(
        "--slo-outcome-recovery-rate",
        type=float,
        default=float(os.getenv("SLO_OUTCOME_RECOVERY_RATE", "0.70")),
    )
    parser.add_argument(
        "--slo-outcome-p95-latency-seconds",
        type=float,
        default=float(os.getenv("SLO_OUTCOME_P95_LATENCY_SECONDS", "120")),
    )
    args = parser.parse_args()

    if not args.token:
        raise SystemExit("KAN_CLIENT_TOKEN or --token is required")
    if not args.client_id:
        raise SystemExit("KAN_CLIENT_ID or --client-id is required")

    headers = {
        "Authorization": f"Bearer {args.token}",
        "X-Client-Id": str(args.client_id),
        "X-Client-Token": str(args.token),
    }
    requested_profiles = [x.strip().lower() for x in str(args.eval_profiles or "").split(",") if x.strip()]
    active_profiles = [x for x in requested_profiles if x in PROFILES] or ["competitive", "real_outcome"]

    async with httpx.AsyncClient() as client:
        active_suites = (
            list(SUITES.keys())
            if args.suite == "all"
            else [args.suite]
        )
        profile_runs: dict[str, dict] = {}
        for profile_name in active_profiles:
            base = dict(PROFILES[profile_name])
            override = {
                "execution_mode": str(args.execution_mode or base["execution_mode"]),
                "wait_for_results": _as_bool(str(args.wait_for_results), bool(base["wait_for_results"])),
                "step_timeout_seconds": float(base["step_timeout_seconds"]),
                "stop_on_failure": bool(base["stop_on_failure"]),
                "enforce_quality_gate": _as_bool(str(args.enforce_quality_gate), bool(base["enforce_quality_gate"])),
                "quality_min_score": float(args.quality_min_score or base["quality_min_score"]),
                "dynamic_risk_controls": _as_bool(str(args.dynamic_risk_controls), bool(base["dynamic_risk_controls"])),
                "allow_high_risk_auto_approval": _as_bool(
                    str(args.allow_high_risk_auto_approval), bool(base["allow_high_risk_auto_approval"])
                ),
            }
            # For real_outcome enforce strict profile even if env overrides are permissive.
            if profile_name == "real_outcome":
                override["wait_for_results"] = True
                override["step_timeout_seconds"] = max(45.0, float(override["step_timeout_seconds"]))
                override["stop_on_failure"] = False
                override["dynamic_risk_controls"] = True
                override["allow_high_risk_auto_approval"] = False
                override["quality_min_score"] = max(0.65, float(override["quality_min_score"]))
            eval_payload_common = {
                "execution_mode": override["execution_mode"],
                "wait_for_results": override["wait_for_results"],
                "step_timeout_seconds": override["step_timeout_seconds"],
                "stop_on_failure": override["stop_on_failure"],
                "enforce_quality_gate": override["enforce_quality_gate"],
                "quality_min_score": override["quality_min_score"],
                "dynamic_risk_controls": override["dynamic_risk_controls"],
                "context": {
                    "allow_high_risk_auto_approval": override["allow_high_risk_auto_approval"],
                    "evaluation_profile": profile_name,
                },
            }
            results = []
            for suite_name in active_suites:
                for task in SUITES[suite_name]:
                    run = await _run_task(
                        client,
                        base_url=args.base_url,
                        token=args.token,
                        payload={**task, **eval_payload_common, "headers": headers},
                    )
                    run["goal"] = task["goal"]
                    run["suite"] = suite_name
                    run["profile"] = profile_name
                    results.append(run)
            canary = await _run_task(
                client,
                base_url=args.base_url,
                token=args.token,
                payload={**CANARY_TASK, **eval_payload_common, "headers": headers},
            )
            canary["goal"] = CANARY_TASK["goal"]
            canary["suite"] = "canary"
            canary["profile"] = profile_name
            score = _compute_score(results, canary=canary, mode=profile_name)
            profile_runs[profile_name] = {"config": eval_payload_common, "score": score, "canary": canary, "results": results}

    operational_score = profile_runs.get("competitive", {}).get("score", {})
    outcome_score = profile_runs.get("real_outcome", {}).get("score", {})
    operational_targets = {
        "success_rate_min": float(args.slo_success_rate),
        "recovery_rate_min": float(args.slo_recovery_rate),
        "p95_latency_seconds_max": float(args.slo_p95_latency_seconds),
    }
    operational_pass = bool(operational_score) and (
        operational_score["success_rate"] >= operational_targets["success_rate_min"]
        and operational_score["recovery_success_rate"] >= operational_targets["recovery_rate_min"]
        and operational_score["p95_latency_seconds"] <= operational_targets["p95_latency_seconds_max"]
    )
    outcome_targets = {
        "success_rate_min": float(args.slo_outcome_success_rate),
        "recovery_rate_min": float(args.slo_outcome_recovery_rate),
        "p95_latency_seconds_max": float(args.slo_outcome_p95_latency_seconds),
    }
    outcome_pass = bool(outcome_score) and (
        outcome_score["success_rate"] >= outcome_targets["success_rate_min"]
        and outcome_score["recovery_success_rate"] >= outcome_targets["recovery_rate_min"]
        and outcome_score["p95_latency_seconds"] <= outcome_targets["p95_latency_seconds_max"]
    )
    payload = {
        "profiles": profile_runs,
        "score": operational_score,
        "canary": profile_runs.get("competitive", {}).get("canary"),
        "results": profile_runs.get("competitive", {}).get("results", []),
        "slo": {
            "operational": {"targets": operational_targets, "pass": operational_pass},
            "outcome": {"targets": outcome_targets, "pass": outcome_pass},
            "pass_all": bool(operational_pass and outcome_pass),
        },
    }

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=True, indent=2)

    history_path = str(args.history_out or "").strip()
    if history_path:
        os.makedirs(os.path.dirname(history_path) or ".", exist_ok=True)
        history_row = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "pass_all": bool(payload.get("slo", {}).get("pass_all")),
            "operational_pass": bool(payload.get("slo", {}).get("operational", {}).get("pass")),
            "outcome_pass": bool(payload.get("slo", {}).get("outcome", {}).get("pass")),
            "report_path": args.out,
            "score": payload.get("score", {}),
        }
        with open(history_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(history_row, ensure_ascii=True) + "\n")

    print(json.dumps(payload, ensure_ascii=True, indent=2))
    if not payload["slo"]["pass_all"]:
        raise SystemExit(2)


if __name__ == "__main__":
    asyncio.run(main())
