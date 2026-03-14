#!/usr/bin/env python3
"""Competitive scorecard against OpenClaw.

Evaluates hard win-condition thresholds and optional head-to-head comparison.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, Optional


DEFAULT_THRESHOLDS: Dict[str, float] = {
    "task_success_rate_min": 0.92,
    "recovery_success_rate_min": 0.70,
    "p95_latency_seconds_max": 60.0,
    "cost_per_run_usd_max": 0.02,
    "session_survival_turns_min": 200.0,
    "autonomy_reliability_index_min": 0.85,
    "regression_rate_weekly_max": 0.03,
}


def _load_json(path: str) -> Dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path}: JSON root must be an object")
    return payload


def _pick_number(mapping: Dict[str, Any], *keys: str) -> Optional[float]:
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    return None


def _extract_metrics(payload: Dict[str, Any]) -> Dict[str, Optional[float]]:
    score = payload.get("score")
    source = score if isinstance(score, dict) else payload
    return {
        "task_success_rate": _pick_number(source, "task_success_rate", "success_rate"),
        "recovery_success_rate": _pick_number(source, "recovery_success_rate", "recovery_proxy"),
        "p95_latency_seconds": _pick_number(source, "p95_latency_seconds"),
        "cost_per_run_usd": _pick_number(source, "cost_per_run_usd", "avg_cost_usd_per_run", "cost_proxy"),
        "session_survival_turns": _pick_number(source, "session_survival_turns", "compaction_survival_turns"),
        "autonomy_reliability_index": _pick_number(source, "autonomy_reliability_index"),
        "regression_rate_weekly": _pick_number(source, "regression_rate_weekly", "regression_rate"),
    }


def _pass_min(value: Optional[float], threshold: float) -> Optional[bool]:
    if value is None:
        return None
    return value >= threshold


def _pass_max(value: Optional[float], threshold: float) -> Optional[bool]:
    if value is None:
        return None
    return value <= threshold


def _format_metric(value: Optional[float]) -> str:
    if value is None:
        return "n/a"
    return f"{value:.4f}"


def _compare(ours: Optional[float], theirs: Optional[float], *, lower_is_better: bool) -> str:
    if ours is None or theirs is None:
        return "unknown"
    if abs(ours - theirs) < 1e-12:
        return "tie"
    if lower_is_better:
        return "ours" if ours < theirs else "openclaw"
    return "ours" if ours > theirs else "openclaw"


def _resolve_thresholds(path: Optional[str]) -> Dict[str, float]:
    if not path:
        return dict(DEFAULT_THRESHOLDS)
    payload = _load_json(path)
    merged = dict(DEFAULT_THRESHOLDS)
    for key in DEFAULT_THRESHOLDS:
        if isinstance(payload.get(key), (int, float)):
            merged[key] = float(payload[key])
    return merged


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--our-report", required=True, help="Path to our weekly eval JSON")
    parser.add_argument("--openclaw-report", help="Optional OpenClaw eval JSON (same schema)")
    parser.add_argument("--thresholds-file", help="Optional JSON overriding threshold values")
    parser.add_argument("--out", default=".run/competitive_scorecard.json")
    args = parser.parse_args()

    thresholds = _resolve_thresholds(args.thresholds_file)
    ours_payload = _load_json(args.our_report)
    ours = _extract_metrics(ours_payload)

    checks = {
        "task_success_rate": _pass_min(ours["task_success_rate"], thresholds["task_success_rate_min"]),
        "recovery_success_rate": _pass_min(
            ours["recovery_success_rate"], thresholds["recovery_success_rate_min"]
        ),
        "p95_latency_seconds": _pass_max(
            ours["p95_latency_seconds"], thresholds["p95_latency_seconds_max"]
        ),
        "cost_per_run_usd": _pass_max(ours["cost_per_run_usd"], thresholds["cost_per_run_usd_max"]),
        "session_survival_turns": _pass_min(
            ours["session_survival_turns"], thresholds["session_survival_turns_min"]
        ),
        "autonomy_reliability_index": _pass_min(
            ours["autonomy_reliability_index"], thresholds["autonomy_reliability_index_min"]
        ),
        "regression_rate_weekly": _pass_max(
            ours["regression_rate_weekly"], thresholds["regression_rate_weekly_max"]
        ),
    }

    known_checks = [value for value in checks.values() if value is not None]
    all_required_pass = bool(known_checks) and all(known_checks) and (len(known_checks) == len(checks))

    comparison: Dict[str, Any] = {}
    if args.openclaw_report:
        openclaw_payload = _load_json(args.openclaw_report)
        openclaw = _extract_metrics(openclaw_payload)
        comparison = {
            "task_success_rate_winner": _compare(
                ours["task_success_rate"], openclaw["task_success_rate"], lower_is_better=False
            ),
            "recovery_success_rate_winner": _compare(
                ours["recovery_success_rate"], openclaw["recovery_success_rate"], lower_is_better=False
            ),
            "p95_latency_seconds_winner": _compare(
                ours["p95_latency_seconds"], openclaw["p95_latency_seconds"], lower_is_better=True
            ),
            "cost_per_run_usd_winner": _compare(
                ours["cost_per_run_usd"], openclaw["cost_per_run_usd"], lower_is_better=True
            ),
            "session_survival_turns_winner": _compare(
                ours["session_survival_turns"], openclaw["session_survival_turns"], lower_is_better=False
            ),
            "autonomy_reliability_index_winner": _compare(
                ours["autonomy_reliability_index"],
                openclaw["autonomy_reliability_index"],
                lower_is_better=False,
            ),
            "regression_rate_weekly_winner": _compare(
                ours["regression_rate_weekly"], openclaw["regression_rate_weekly"], lower_is_better=True
            ),
            "openclaw_metrics": openclaw,
        }
        winner_votes = [v for k, v in comparison.items() if k.endswith("_winner") and v in {"ours", "openclaw"}]
        comparison["head_to_head_winner"] = (
            "ours"
            if winner_votes.count("ours") > winner_votes.count("openclaw")
            else "openclaw"
            if winner_votes.count("openclaw") > winner_votes.count("ours")
            else "tie_or_unknown"
        )

    verdict = "WINNING" if all_required_pass else "NOT_YET"
    report = {
        "verdict": verdict,
        "thresholds": thresholds,
        "our_metrics": ours,
        "checks": checks,
        "all_required_pass": all_required_pass,
        "comparison": comparison,
    }

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(report, ensure_ascii=True, indent=2), encoding="utf-8")

    print(f"Verdict: {verdict}")
    print("")
    print("Our Metrics:")
    for key, value in ours.items():
        print(f"- {key}: {_format_metric(value)}")
    print("")
    print("Threshold Checks:")
    for key, value in checks.items():
        status = "PASS" if value is True else "FAIL" if value is False else "UNKNOWN"
        print(f"- {key}: {status}")
    if comparison:
        print("")
        print("Head-to-Head:")
        print(f"- winner: {comparison.get('head_to_head_winner')}")


if __name__ == "__main__":
    main()
