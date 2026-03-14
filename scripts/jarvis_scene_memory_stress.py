#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import gc
import os
import sys
import time
from pathlib import Path
from statistics import mean

import psutil

# Allow running from any cwd while keeping local project imports.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import jarvis_listener as jl


def _mb(value_bytes: float) -> float:
    return float(value_bytes) / (1024.0 * 1024.0)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Stress test para flujo Jarvis de 'que estoy haciendo' "
            "con medicion de memoria RSS."
        )
    )
    parser.add_argument("--iterations", type=int, default=20, help="Numero de corridas (default: 20).")
    parser.add_argument(
        "--pause-sec",
        type=float,
        default=0.25,
        help="Pausa entre corridas en segundos (default: 0.25).",
    )
    parser.add_argument(
        "--max-trend-mb",
        type=float,
        default=80.0,
        help="Umbral de crecimiento promedio inicio->fin para marcar posible leak.",
    )
    parser.add_argument(
        "--mock-vision",
        action="store_true",
        help="Evita llamada de red a Vision y usa respuesta simulada.",
    )
    return parser


async def _run_stress(iterations: int, pause_sec: float) -> dict:
    process = psutil.Process(os.getpid())
    rss_samples: list[int] = []
    durations: list[float] = []
    statuses: list[str] = []

    initial_rss = process.memory_info().rss
    rss_samples.append(initial_rss)
    print(
        f"Inicio stress scene vision | iteraciones={iterations} | "
        f"rss_inicial={_mb(initial_rss):.2f} MB"
    )

    for idx in range(1, iterations + 1):
        t0 = time.perf_counter()
        message = await jl._handle_scene_query()
        elapsed = time.perf_counter() - t0

        # Extra pass en el script para hacer visible drift de RSS.
        gc.collect()

        rss_now = process.memory_info().rss
        rss_samples.append(rss_now)
        durations.append(elapsed)
        statuses.append((message or "").strip()[:80])

        print(
            f"[{idx:02d}/{iterations}] "
            f"time={elapsed:.2f}s "
            f"rss={_mb(rss_now):.2f}MB "
            f"delta={_mb(rss_now - initial_rss):+.2f}MB "
            f"msg='{(message or '').strip()[:64]}'"
        )

        if pause_sec > 0:
            await asyncio.sleep(pause_sec)

    peak_rss = max(rss_samples)
    final_rss = rss_samples[-1]
    window = min(5, max(2, iterations // 4))
    if len(rss_samples) >= (2 * window + 1):
        start_slice = rss_samples[1 : 1 + window]
        end_slice = rss_samples[-window:]
    elif len(rss_samples) > 1:
        start_slice = [rss_samples[1]]
        end_slice = [rss_samples[-1]]
    else:
        start_slice = [initial_rss]
        end_slice = [final_rss]
    start_avg = mean(start_slice)
    end_avg = mean(end_slice)

    summary = {
        "initial_mb": _mb(initial_rss),
        "final_mb": _mb(final_rss),
        "peak_mb": _mb(peak_rss),
        "delta_final_mb": _mb(final_rss - initial_rss),
        "delta_peak_mb": _mb(peak_rss - initial_rss),
        "trend_mb": _mb(end_avg - start_avg),
        "avg_latency_s": float(mean(durations)) if durations else 0.0,
        "last_status": statuses[-1] if statuses else "",
    }
    return summary


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    if args.iterations < 1:
        parser.error("--iterations debe ser >= 1")

    if args.mock_vision:
        async def _fake_vision(*_args, **_kwargs):
            return "Vision simulada para prueba de memoria."

        jl._describe_scene_with_vision = _fake_vision  # type: ignore[assignment]

    report = asyncio.run(_run_stress(args.iterations, args.pause_sec))
    print(
        "Resumen stress | "
        f"initial={report['initial_mb']:.2f}MB "
        f"final={report['final_mb']:.2f}MB "
        f"peak={report['peak_mb']:.2f}MB "
        f"delta_final={report['delta_final_mb']:+.2f}MB "
        f"trend={report['trend_mb']:+.2f}MB "
        f"avg_latency={report['avg_latency_s']:.2f}s"
    )

    if report["trend_mb"] > float(args.max_trend_mb):
        print(
            "ALERTA: crecimiento de memoria sostenido por encima del umbral "
            f"({report['trend_mb']:.2f}MB > {args.max_trend_mb:.2f}MB)."
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
