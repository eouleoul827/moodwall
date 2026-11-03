"""Measure CPU latency with the same strict loader as the diagnostic API."""

from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import time
from pathlib import Path

if __package__:
    from .app import load_predictor, resolve_model_dir
else:
    from app import load_predictor, resolve_model_dir

SAMPLES = ("OO야 생일 축하해!!", "과제 너무 많아 지친다", "내일 축제 기대됨ㅋㅋ", "오늘 학식 맛있었다")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", type=Path, help="Override SLM_MODEL_DIR with a prepared model bundle")
    args = parser.parse_args(argv)
    started = time.perf_counter()
    try:
        predictor = load_predictor(resolve_model_dir(args.model_dir))
    except (OSError, ValueError, RuntimeError) as error:
        parser.exit(1, f"Model setup failed: {error}\n")
    load_seconds = time.perf_counter() - started
    timings = []
    for index in range(105):
        started = time.perf_counter()
        predictor(SAMPLES[index % len(SAMPLES)])
        if index >= 5:
            timings.append((time.perf_counter() - started) * 1000)
    timings.sort()
    print(
        json.dumps(
            {
                "diagnostic_only": True,
                "public_display_authorized": False,
                "logical_cpus": os.cpu_count(),
                "cpu_threads": 2,
                "load_seconds": load_seconds,
                "samples": len(timings),
                "mean_ms": statistics.mean(timings),
                "p50_ms": statistics.median(timings),
                "p95_ms": timings[math.ceil(len(timings) * 0.95) - 1],
                "max_ms": timings[-1],
                "scope": "100 sequential warm predictions of four fixed synthetic sentences; not an accuracy test",
            },
            allow_nan=False,
        )
    )


if __name__ == "__main__":
    main()
