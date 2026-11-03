"""Check a local v0 bundle with fixed synthetic examples, never student records.

Run from backend/: python -m tools.check_student_model --manifest PATH
This diagnostic measures execution and contract compatibility, not accuracy or
moderation safety. It does not read .env or activate an approved safety policy.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import statistics
import sys
import time
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from inference.contracts import AnalysisInput, AnalysisResult
from inference.loader import load_analyzer

SYNTHETIC_EXAMPLES = (
    ("celebration", "합격 진심으로 축하해!"),
    ("joy", "오늘 점심이 맛있어서 행복해"),
    ("support", "발표 잘할 수 있어 힘내!"),
    ("excitement", "내일 축제가 너무 기대돼"),
    ("tired", "과제가 많아서 너무 피곤해"),
    ("sadness", "친구랑 못 만나서 슬퍼"),
    ("anger", "버스 놓쳐서 정말 화나네"),
    ("safety_review", "OO야 너는 여기서 빠져"),
    ("unicode_30_graphemes", " 기분좋아" + "👨‍👩‍👧‍👦" * 24 + " "),
)
BENCHMARK_REPETITIONS = 20


def load_local_analyzer(manifest_path: Path):
    """Use only explicit test scope; loading still verifies every artifact hash."""
    manifest_path = manifest_path.resolve(strict=True)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    settings = SimpleNamespace(
        environment="test",
        analyzer="inference.student_model:create_analyzer",
        model_manifest_path=str(manifest_path),
        model_bundle_version=manifest["model_bundle_version"],
        inference_policy_version=manifest["inference_policy_version"],
    )
    return load_analyzer(settings), manifest_path, manifest


def _latency_summary(values: list[float]) -> dict:
    ordered = sorted(values)
    return {
        "samples": len(ordered),
        "p50_ms": statistics.median(ordered),
        "p95_ms": ordered[math.ceil(len(ordered) * 0.95) - 1],
        "max_ms": ordered[-1],
        "p95_method": "nearest rank",
    }


def check_bundle(manifest_path: Path) -> dict:
    import regex

    from inference.student_model import LABELS

    started_at = datetime.now(timezone.utc).isoformat()
    started = time.perf_counter()
    analyzer, manifest_path, manifest = load_local_analyzer(manifest_path)
    load_and_warmup_ms = (time.perf_counter() - started) * 1000

    # Imports occur only during the explicit check, after the configured loader.
    import torch
    import transformers

    results = []
    for case, text in SYNTHETIC_EXAMPLES:
        started = time.perf_counter()
        prediction = analyzer.predict(text)
        elapsed_ms = (time.perf_counter() - started) * 1000
        results.append(
            {
                "case": case,
                "synthetic_text": text,
                "grapheme_count": len(regex.findall(r"\X", text)),
                "codepoint_count": len(text),
                "input_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "elapsed_ms": elapsed_ms,
                "label": prediction.label,
                "emotion_mapping": prediction.emotion,
                "uncalibrated_top_score": prediction.score,
                "uncalibrated_inappropriate_score": prediction.inappropriate_score,
                "probabilities_by_label": dict(zip(LABELS, prediction.probabilities, strict=True)),
            }
        )
    unicode_result = results[-1]
    if unicode_result["grapheme_count"] != 30 or unicode_result["codepoint_count"] <= 30:
        raise ValueError("the synthetic unicode example must exercise the 30-grapheme boundary")

    contract = AnalysisResult.model_validate(analyzer.analyze(AnalysisInput(text=SYNTHETIC_EXAMPLES[0][1])))
    if (
        contract.safety.verdict != "UNCERTAIN"
        or contract.safety.reason_codes != ["INSUFFICIENT_CONTEXT"]
        or contract.safety.score is not None
        or contract.emotion is not None
    ):
        raise ValueError("v0 must remain review-only without an approved safety policy")

    timings = []
    for _ in range(BENCHMARK_REPETITIONS):
        started = time.perf_counter()
        analyzer.predict(SYNTHETIC_EXAMPLES[0][1])
        timings.append((time.perf_counter() - started) * 1000)

    return {
        "report_schema_version": "1",
        "started_at": started_at,
        "purpose": "Local execution, contract check and synthetic timing only",
        "safety_policy": "review-only",
        "public_display_authorized": False,
        "model_bundle_version": manifest["model_bundle_version"],
        "inference_policy_version": manifest["inference_policy_version"],
        "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "verified_artifacts": {
            name: {"sha256": digest, "size_bytes": (manifest_path.parent / name).stat().st_size}
            for name, digest in sorted(manifest["artifacts"].items())
        },
        "runtime": {
            "python": platform.python_version(),
            "torch": torch.__version__,
            "transformers": transformers.__version__,
            "platform": platform.platform(),
            "processor": platform.processor(),
            "logical_cpus": os.cpu_count(),
            "cpu_threads": torch.get_num_threads(),
            "device": "cpu",
            "max_length": 64,
        },
        "load_hash_verification_and_warmup_ms": load_and_warmup_ms,
        "examples": results,
        "contract_check": {
            "verdict": contract.safety.verdict,
            "reason_codes": contract.safety.reason_codes,
            "emotion": None,
        },
        "benchmark": {
            "case": SYNTHETIC_EXAMPLES[0][0],
            "scope": "Twenty sequential warm predictions of one fixed synthetic sentence",
            **_latency_summary(timings),
        },
        "limitations": [
            "Scenario names describe synthetic inputs and are not verified ground-truth labels.",
            "Softmax outputs are uncalibrated; no safety threshold is selected by this check.",
            "No accuracy, harmful-content recall, false-blocking rate or deployment safety is evaluated.",
            "Timing applies to this machine and these inputs; it is not a service-level load test.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--output", type=Path, help="Write the report outside the verified model directory")
    args = parser.parse_args()
    manifest_path = args.manifest.resolve(strict=True)
    if args.output and args.output.resolve().is_relative_to(manifest_path.parent):
        parser.error("--output must be outside the verified model directory")
    # Keep the default stdout stream valid JSON even if an optional model
    # dependency prints loading diagnostics to stdout.
    with redirect_stdout(sys.stderr):
        report = check_bundle(manifest_path)
    document = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(document, encoding="utf-8")
    else:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        sys.stdout.write(document)


if __name__ == "__main__":
    main()
