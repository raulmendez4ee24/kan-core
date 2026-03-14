import json
from pathlib import Path

from scripts.release_guard import _slo_degraded, should_block_release


def test_release_guard_blocks_on_two_consecutive_failures() -> None:
    rows = [
        {"pass_all": True},
        {"pass_all": False},
        {"pass_all": False},
    ]
    assert should_block_release(rows, consecutive_failures=2) is True


def test_release_guard_does_not_block_when_last_run_passes() -> None:
    rows = [
        {"pass_all": False},
        {"pass_all": True},
    ]
    assert should_block_release(rows, consecutive_failures=2) is False


def test_release_guard_reads_nested_slo_shape() -> None:
    rows = [
        {"slo": {"pass_all": False}},
        {"slo": {"pass_all": False}},
    ]
    assert should_block_release(rows, consecutive_failures=2) is True


def test_release_guard_jsonl_compatible_payload_shape(tmp_path: Path) -> None:
    history = tmp_path / "history.jsonl"
    entries = [
        {"timestamp": "2026-02-22T00:00:00Z", "pass_all": True},
        {"timestamp": "2026-02-22T00:10:00Z", "pass_all": False},
        {"timestamp": "2026-02-22T00:20:00Z", "pass_all": False},
    ]
    with history.open("w", encoding="utf-8") as f:
        for item in entries:
            f.write(json.dumps(item) + "\n")
    lines = [json.loads(x) for x in history.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert should_block_release(lines, consecutive_failures=2) is True


def test_release_guard_slo_degradation_detection() -> None:
    report = {
        "score": {
            "success_rate": 0.82,
            "recovery_success_rate": 0.75,
            "p95_latency_seconds": 40.0,
        }
    }
    degraded, metrics = _slo_degraded(
        report,
        min_success_rate=0.90,
        min_recovery_rate=0.70,
        max_p95_latency_seconds=90.0,
    )
    assert degraded is True
    assert metrics["success_rate"] == 0.82


def test_release_guard_slo_not_degraded_when_metrics_healthy() -> None:
    report = {
        "score": {
            "success_rate": 0.95,
            "recovery_success_rate": 0.80,
            "p95_latency_seconds": 35.0,
        }
    }
    degraded, _metrics = _slo_degraded(
        report,
        min_success_rate=0.90,
        min_recovery_rate=0.70,
        max_p95_latency_seconds=90.0,
    )
    assert degraded is False


def test_release_guard_cli_blocks_when_report_required_and_missing(tmp_path: Path) -> None:
    history = tmp_path / "history.jsonl"
    history.write_text("", encoding="utf-8")
    import subprocess

    proc = subprocess.run(
        [
            "python3",
            "scripts/release_guard.py",
            "--history",
            str(history),
            "--latest-report",
            str(tmp_path / "missing_report.json"),
            "--require-report",
            "true",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 2
