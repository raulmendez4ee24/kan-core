#!/usr/bin/env python3
"""Release guard based on consecutive pass_all failures and SLO health."""

import argparse
import json
import os
from typing import Any, Dict, List, Optional, Tuple


def _read_jsonl(path: str) -> List[Dict[str, Any]]:
    if not path or (not os.path.exists(path)):
        return []
    rows: List[Dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            token = line.strip()
            if not token:
                continue
            try:
                payload = json.loads(token)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                rows.append(payload)
    return rows


def _extract_pass_all(item: Dict[str, Any]) -> bool:
    if "pass_all" in item:
        return bool(item.get("pass_all"))
    slo = item.get("slo")
    if isinstance(slo, dict):
        return bool(slo.get("pass_all"))
    return False


def _latest_report_path(rows: List[Dict[str, Any]]) -> str:
    if not rows:
        return ""
    latest = rows[-1]
    path = str(latest.get("report_path") or "").strip()
    return path


def _load_json(path: str) -> Optional[Dict[str, Any]]:
    if not path or (not os.path.exists(path)):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _extract_metrics(report: Dict[str, Any]) -> Dict[str, float]:
    score = report.get("score") if isinstance(report.get("score"), dict) else {}
    return {
        "success_rate": float(score.get("success_rate") or 0.0),
        "recovery_success_rate": float(score.get("recovery_success_rate") or 0.0),
        "p95_latency_seconds": float(score.get("p95_latency_seconds") or 0.0),
    }


def _slo_degraded(
    report: Optional[Dict[str, Any]],
    *,
    min_success_rate: float,
    min_recovery_rate: float,
    max_p95_latency_seconds: float,
) -> Tuple[bool, Dict[str, float]]:
    if not report:
        return False, {}
    metrics = _extract_metrics(report)
    degraded = (
        float(metrics.get("success_rate") or 0.0) < float(min_success_rate)
        or float(metrics.get("recovery_success_rate") or 0.0) < float(min_recovery_rate)
        or float(metrics.get("p95_latency_seconds") or 0.0) > float(max_p95_latency_seconds)
    )
    return degraded, metrics


def should_block_release(rows: List[Dict[str, Any]], *, consecutive_failures: int) -> bool:
    required = max(1, int(consecutive_failures))
    if len(rows) < required:
        return False
    tail = rows[-required:]
    return all((not _extract_pass_all(x)) for x in tail)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--history",
        default=os.getenv("WEEKLY_EVAL_HISTORY_PATH", ".run/weekly_eval_history.jsonl"),
        help="Path to weekly eval JSONL history.",
    )
    parser.add_argument(
        "--consecutive-failures",
        type=int,
        default=int(os.getenv("RELEASE_GUARD_CONSECUTIVE_FAILURES", "2")),
        help="Block release after this many pass_all failures in a row.",
    )
    parser.add_argument(
        "--latest-report",
        default=os.getenv("WEEKLY_EVAL_REPORT_PATH", ""),
        help="Optional latest weekly eval JSON report path.",
    )
    parser.add_argument(
        "--min-success-rate",
        type=float,
        default=float(os.getenv("RELEASE_GUARD_MIN_SUCCESS_RATE", "0.90")),
    )
    parser.add_argument(
        "--min-recovery-rate",
        type=float,
        default=float(os.getenv("RELEASE_GUARD_MIN_RECOVERY_RATE", "0.70")),
    )
    parser.add_argument(
        "--max-p95-latency-seconds",
        type=float,
        default=float(os.getenv("RELEASE_GUARD_MAX_P95_LATENCY_SECONDS", "90")),
    )
    parser.add_argument(
        "--require-report",
        default=os.getenv("RELEASE_GUARD_REQUIRE_REPORT", "false"),
        help="true|false. If true and latest report is missing, block release.",
    )
    args = parser.parse_args()

    rows = _read_jsonl(args.history)
    block_failures = should_block_release(rows, consecutive_failures=args.consecutive_failures)
    report_path = str(args.latest_report or "").strip() or _latest_report_path(rows)
    report = _load_json(report_path)
    require_report = str(args.require_report or "").strip().lower() in {"1", "true", "yes", "on"}
    block_missing_report = bool(require_report and (not report))
    block_slo, metrics = _slo_degraded(
        report,
        min_success_rate=float(args.min_success_rate),
        min_recovery_rate=float(args.min_recovery_rate),
        max_p95_latency_seconds=float(args.max_p95_latency_seconds),
    )
    block = bool(block_failures or block_slo or block_missing_report)
    tail = rows[-max(1, int(args.consecutive_failures)) :] if rows else []
    state = {
        "history_path": args.history,
        "history_size": len(rows),
        "consecutive_failures_threshold": int(args.consecutive_failures),
        "recent_pass_all": [_extract_pass_all(x) for x in tail],
        "latest_report_path": report_path,
        "report_found": bool(report),
        "require_report": bool(require_report),
        "metrics": metrics,
        "slo_thresholds": {
            "min_success_rate": float(args.min_success_rate),
            "min_recovery_rate": float(args.min_recovery_rate),
            "max_p95_latency_seconds": float(args.max_p95_latency_seconds),
        },
        "block_by_consecutive_failures": bool(block_failures),
        "block_by_slo_degradation": bool(block_slo),
        "block_by_missing_report": bool(block_missing_report),
        "block_release": block,
    }
    print(json.dumps(state, ensure_ascii=True, indent=2))
    if block:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
