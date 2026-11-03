"""Local KcELECTRA v0 integration for development, without an approved safety policy.

Loading and predicting are real; no v0 prediction authorizes public display. Heavy
dependencies are imported only after the caller has verified the local bundle.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from numbers import Real
from pathlib import Path
from typing import TYPE_CHECKING

from inference.contracts import AnalysisInput, AnalysisResult, Emotion, SafetyResult

if TYPE_CHECKING:
    from inference.loader import AnalyzerContext

LABELS = ("축하", "기쁨", "응원", "설렘", "피곤", "슬픔", "화남", "부적절")
EMOTIONS: tuple[Emotion | None, ...] = (
    "CELEBRATION",
    "JOY",
    "SUPPORT",
    "EXCITEMENT",
    "TIRED",
    "SADNESS",
    "ANGER",
    None,
)
REQUIRED_ARTIFACTS = frozenset({"config.json", "tokenizer.json", "tokenizer_config.json", "model.safetensors"})
STUDENT_MODEL_SPEC = {
    "schema_version": "1",
    "kind": "electra-emotion-v0",
    "max_length": 64,
    "cpu_threads": 2,
    "safety_policy": "review-only",
}


@dataclass(frozen=True, slots=True)
class Prediction:
    """Uncalibrated model output for local evaluation, not a safety verdict."""

    label: str
    emotion: Emotion | None
    score: float
    inappropriate_score: float
    probabilities: tuple[float, ...]


def prediction_from_probabilities(values: Sequence[float]) -> Prediction:
    if len(values) != len(LABELS):
        raise ValueError("student model must return exactly eight probabilities")
    if any(isinstance(value, bool) or not isinstance(value, Real) for value in values):
        raise ValueError("student model probabilities must be real numbers")
    probabilities = tuple(float(value) for value in values)
    if any(not math.isfinite(value) or not 0 <= value <= 1 for value in probabilities):
        raise ValueError("student model probabilities must be finite and between zero and one")
    if not math.isclose(math.fsum(probabilities), 1.0, rel_tol=1e-5, abs_tol=1e-6):
        raise ValueError("student model probabilities must sum to one")
    best = max(range(len(probabilities)), key=probabilities.__getitem__)
    return Prediction(
        label=LABELS[best],
        emotion=EMOTIONS[best],
        score=probabilities[best],
        inappropriate_score=probabilities[-1],
        probabilities=probabilities,
    )


def _validate_labels(config: dict) -> None:
    expected_ids = {str(index): label for index, label in enumerate(LABELS)}
    expected_labels = {label: index for index, label in enumerate(LABELS)}
    ids = config.get("id2label")
    labels = config.get("label2id")
    if not isinstance(ids, dict) or len(ids) != len(LABELS):
        raise ValueError("student model id2label must contain the eight v0 labels")
    if {str(key): value for key, value in ids.items()} != expected_ids:
        raise ValueError("student model id2label order does not match v0")
    if not isinstance(labels, dict) or labels != expected_labels:
        raise ValueError("student model label2id does not match v0")
    if any(type(value) is not int for value in labels.values()):
        raise ValueError("student model label IDs must be integers")
    if config.get("model_type") != "electra":
        raise ValueError("student model must use the electra architecture")
    if "num_labels" in config and (type(config["num_labels"]) is not int or config["num_labels"] != len(LABELS)):
        raise ValueError("student model must contain eight classification outputs")


def _validate_bundle(context: AnalyzerContext) -> Path:
    if context.environment not in {"development", "test"}:
        raise ValueError("student model v0 is forbidden outside development/test")
    manifest = context.manifest
    spec = manifest.get("student_model")
    if not isinstance(spec, dict) or spec.keys() != STUDENT_MODEL_SPEC.keys():
        raise ValueError("student model manifest must specify the supported v0 policy")
    if any(
        type(spec[key]) is not type(expected) or spec[key] != expected for key, expected in STUDENT_MODEL_SPEC.items()
    ):
        raise ValueError("student model v0 only supports the fixed review-only policy")
    for field in ("model_bundle_version", "inference_policy_version"):
        value = manifest.get(field)
        if not isinstance(value, str) or not 1 <= len(value) <= 100:
            raise ValueError("student model manifest requires valid bundle and policy versions")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict) or artifacts.keys() != REQUIRED_ARTIFACTS:
        raise ValueError("student model manifest must pin exactly the four supported local artifacts")
    bundle_dir = context.manifest_path.parent
    # Hugging Face also discovers optional local sidecars. An unpinned sidecar
    # must not silently alter tokenization or choose a different weight file.
    allowed = REQUIRED_ARTIFACTS | {context.manifest_path.name}
    if context.manifest_path.name in REQUIRED_ARTIFACTS or any(
        not path.is_file() or path.name not in allowed for path in bundle_dir.iterdir()
    ):
        raise ValueError("student model directory must contain only its manifest and four pinned artifacts")
    config = json.loads((bundle_dir / "config.json").read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise ValueError("student model config must be a JSON object")
    _validate_labels(config)
    return bundle_dir


class _CpuPredictor:
    def __init__(self, torch, tokenizer, model, max_length: int):
        self._torch = torch
        self._tokenizer = tokenizer
        self._model = model
        self._max_length = max_length

    def __call__(self, text: str) -> tuple[float, ...]:
        inputs = self._tokenizer(text, truncation=True, max_length=self._max_length, return_tensors="pt")
        inputs = {name: tensor.to("cpu") for name, tensor in inputs.items()}
        with self._torch.inference_mode():
            logits = self._model(**inputs).logits
            if not self._torch.is_tensor(logits) or tuple(logits.shape) != (1, len(LABELS)):
                raise ValueError("student model must return a one-by-eight logits tensor")
            if not self._torch.is_floating_point(logits) or not bool(self._torch.isfinite(logits).all().item()):
                raise ValueError("student model logits must be finite floating-point values")
            probabilities = self._torch.softmax(logits, dim=-1)[0]
            return tuple(probabilities.tolist())


def _validate_loading_info(info: dict) -> None:
    fields = {"missing_keys", "unexpected_keys", "mismatched_keys", "error_msgs"}
    if not isinstance(info, dict) or not fields.issubset(info):
        raise ValueError("student checkpoint loading report is incomplete")
    if any(not isinstance(info[field], (set, list, tuple)) or info[field] for field in fields):
        raise ValueError("student checkpoint must load without missing, unexpected or mismatched weights")


def _load_cpu_predictor(bundle_dir: Path, *, cpu_threads: int, max_length: int) -> Callable[[str], Sequence[float]]:
    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    torch.set_num_threads(cpu_threads)
    tokenizer = AutoTokenizer.from_pretrained(
        str(bundle_dir), local_files_only=True, trust_remote_code=False, use_fast=True
    )
    if not tokenizer.is_fast:
        raise ValueError("student model requires the pinned fast tokenizer")
    model, loading_info = AutoModelForSequenceClassification.from_pretrained(
        str(bundle_dir),
        local_files_only=True,
        trust_remote_code=False,
        use_safetensors=True,
        ignore_mismatched_sizes=False,
        output_loading_info=True,
    )
    _validate_loading_info(loading_info)
    _validate_labels(model.config.to_dict())
    model.to("cpu")
    model.eval()
    return _CpuPredictor(torch, tokenizer, model, max_length)


class StudentModelAnalyzer:
    def __init__(
        self,
        *,
        model_bundle_version: str,
        inference_policy_version: str,
        predictor: Callable[[str], Sequence[float]],
    ):
        self.model_bundle_version = model_bundle_version
        self.inference_policy_version = inference_policy_version
        self._predictor = predictor

    def predict(self, text: str) -> Prediction:
        if not isinstance(text, str) or not text:
            raise ValueError("student model input must be a nonempty string")
        # The service already validates grapheme length. Never strip or slice
        # the original text into a different statement before analyzing it.
        return prediction_from_probabilities(self._predictor(text))

    def analyze(self, value: AnalysisInput) -> AnalysisResult:
        self.predict(value.text)
        # v0 has no independent safety evaluation or approved score boundary.
        # Even confident predictions remain nonpublic, without a violation.
        return AnalysisResult(
            model_bundle_version=self.model_bundle_version,
            inference_policy_version=self.inference_policy_version,
            safety=SafetyResult(verdict="UNCERTAIN", reason_codes=["INSUFFICIENT_CONTEXT"]),
            emotion=None,
        )


def create_analyzer(*, context: AnalyzerContext) -> StudentModelAnalyzer:
    """Called by the loader after every local artifact has passed SHA-256 checks."""
    bundle_dir = _validate_bundle(context)
    predictor = _load_cpu_predictor(bundle_dir, cpu_threads=2, max_length=64)
    return StudentModelAnalyzer(
        model_bundle_version=context.manifest["model_bundle_version"],
        inference_policy_version=context.manifest["inference_policy_version"],
        predictor=predictor,
    )
