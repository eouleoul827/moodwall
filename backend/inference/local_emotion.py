"""Optional local color classification after Gemini has already allowed text.

This module never creates a safety verdict. Its only output is a display emotion;
the old review-only student analyzer retains its independent production guard.
Importing it does not import torch or transformers or download any artifacts.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

from inference import student_model
from inference.contracts import AnalysisInput, EmotionResult

JSON_LIMIT = 1024 * 1024
MIN_PRESENTATION_SCORE = 0.5


def _canonical(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("ascii")


def _regular(path: Path) -> os.stat_result:
    for component in (*reversed(path.parents), path):
        if component.is_symlink() or component.is_junction():
            raise ValueError("Emotion artifacts must not use symbolic links or junctions")
    info = path.stat(follow_symlinks=False)
    if not stat.S_ISREG(info.st_mode):
        raise ValueError("Emotion artifacts must be regular files")
    return info


def _json(path: Path) -> dict:
    _regular(path)
    with path.open("rb") as stream:
        payload = stream.read(JSON_LIMIT + 1)
    if len(payload) > JSON_LIMIT:
        raise ValueError("Emotion configuration exceeds the size limit")

    def pairs(items):
        result = {}
        for key, item in items:
            if key in result:
                raise ValueError("Emotion configuration contains duplicate fields")
            result[key] = item
        return result

    value = json.loads(payload, object_pairs_hook=pairs)
    if not isinstance(value, dict):
        raise ValueError("Emotion configuration must be an object")
    return value


def _file_identity(info: os.stat_result) -> tuple[int, ...]:
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def _sha256(path: Path) -> str:
    before = _regular(path)
    with path.open("rb") as stream:
        if _file_identity(os.fstat(stream.fileno())) != _file_identity(before):
            raise ValueError("Emotion artifact changed before verification")
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if _file_identity(os.fstat(stream.fileno())) != _file_identity(before):
            raise ValueError("Emotion artifact changed during verification")
    if _file_identity(_regular(path)) != _file_identity(before):
        raise ValueError("Emotion artifact changed during verification")
    return digest


@dataclass(frozen=True)
class VerifiedEmotionBundle:
    manifest_path: Path
    manifest: dict
    release_parameters: dict


def verify_emotion_bundle(configured_path: str | Path) -> VerifiedEmotionBundle:
    """Verify each pinned byte without importing optional model dependencies."""
    supplied = Path(os.path.abspath(Path(configured_path).expanduser()))
    _regular(supplied)
    manifest_path = supplied.resolve(strict=True)
    manifest = _json(manifest_path)
    config = _json(manifest_path.parent / "config.json")
    context = SimpleNamespace(environment="production", manifest_path=manifest_path, manifest=manifest)
    # Validate architecture, exact label order, policy and absence of sidecars.
    student_model._validate_bundle(context, emotion_only=True)
    student_model._validate_labels(config)
    artifacts = manifest["artifacts"]
    for name, expected in artifacts.items():
        if not isinstance(expected, str) or len(expected) != 64 or any(c not in "0123456789abcdef" for c in expected):
            raise ValueError("Emotion manifest must pin SHA-256 artifact hashes")
        if _sha256(manifest_path.parent / name) != expected:
            raise ValueError("Emotion artifact hash mismatch")
    artifact_digest = hashlib.sha256(_canonical(artifacts)).hexdigest()
    declared_identity = manifest.get("bundle_identity")
    if declared_identity is not None and declared_identity != {
        "schema_version": "1",
        "algorithm": "sha256",
        "canonicalization": "sorted-artifact-sha256-json-v1",
        "digest": artifact_digest,
    }:
        raise ValueError("Emotion bundle identity does not match its artifacts")
    parameters = {
        "adapter_version": "gemini-approved-local-emotion-v1",
        "manifest_sha256": hashlib.sha256(_canonical(manifest)).hexdigest(),
        "bundle_sha256": artifact_digest,
        "artifacts": dict(artifacts),
        "model_bundle_version": manifest["model_bundle_version"],
        "label_emotions": dict(zip(student_model.LABELS, student_model.EMOTIONS, strict=True)),
        "min_presentation_score_exclusive": MIN_PRESENTATION_SCORE,
        "adapter_sha256": _sha256(Path(__file__)),
        "student_runtime_sha256": _sha256(Path(student_model.__file__)),
        "model_dependencies_sha256": _sha256(Path(__file__).parents[1] / "requirements-model.lock"),
    }
    return VerifiedEmotionBundle(manifest_path, manifest, parameters)


class LocalEmotionPredictor:
    def __init__(self, predictor: Callable[[str], Sequence[float]]):
        self._predictor = predictor

    def __call__(self, value: AnalysisInput) -> EmotionResult:
        fallback = EmotionResult(label="NEUTRAL", source="FALLBACK", score=None)
        try:
            prediction = student_model.prediction_from_probabilities(self._predictor(value.text))
            # The v0 score is uncalibrated. A strict majority is only a color
            # presentation heuristic, never a safety confidence threshold.
            if prediction.emotion is None or prediction.score <= MIN_PRESENTATION_SCORE:
                return fallback
            return EmotionResult(label=prediction.emotion, source="MODEL", score=prediction.score)
        except Exception:
            return fallback


def load_emotion_predictor(configured_path: str | Path, *, expected_parameters: dict) -> LocalEmotionPredictor:
    """Load once inside the worker's bounded startup after rechecking artifacts."""
    bundle = verify_emotion_bundle(configured_path)
    if bundle.release_parameters != expected_parameters:
        raise ValueError("Emotion bundle changed after deployment configuration")
    predictor = student_model._load_cpu_predictor(bundle.manifest_path.parent, cpu_threads=2, max_length=64)
    # A broken explicitly configured model fails startup; runtime failures after
    # an ALLOW decision instead receive the neutral presentation fallback.
    student_model.prediction_from_probabilities(predictor("연결 확인"))
    return LocalEmotionPredictor(predictor)
