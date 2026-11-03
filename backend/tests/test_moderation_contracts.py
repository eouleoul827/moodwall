import copy
import hashlib
import json
import sys
import types
from types import SimpleNamespace

import pytest
from pydantic import TypeAdapter, ValidationError

from inference.contracts import AnalysisInput, AnalysisResult, FinalizationOutcome
from inference.fake import FakeAnalyzer
from inference.loader import load_analyzer


@pytest.fixture
def analysis():
    return FakeAnalyzer().analyze(AnalysisInput(text="합격 축하해!")).model_dump()


@pytest.mark.parametrize("score", [float("nan"), float("inf"), -0.1, 1.1])
def test_invalid_confidence_never_becomes_public(analysis, score):
    analysis["safety"]["score"] = score
    with pytest.raises(ValidationError):
        AnalysisResult.model_validate(analysis)


@pytest.mark.parametrize(
    "mutation",
    ["missing_emotion", "unknown_emotion", "unsafe_with_emotion", "unsafe_without_reason", "fake_fallback", "extra"],
)
def test_result_combinations(analysis, mutation):
    value = copy.deepcopy(analysis)
    if mutation == "missing_emotion":
        value["emotion"] = None
    elif mutation == "unknown_emotion":
        value["emotion"]["label"] = "UNKNOWN"
    elif mutation == "unsafe_with_emotion":
        value["safety"] = {"verdict": "BLOCK", "reason_codes": ["HARASSMENT"]}
    elif mutation == "unsafe_without_reason":
        value["safety"] = {"verdict": "UNCERTAIN", "reason_codes": []}
        value["emotion"] = None
    elif mutation == "fake_fallback":
        value["emotion"] = {"label": "JOY", "source": "FALLBACK"}
    else:
        value["unexpected"] = "field"
    with pytest.raises(ValidationError):
        AnalysisResult.model_validate(value)


def test_rules_model_and_infrastructure_are_distinct(analysis):
    adapter = TypeAdapter(FinalizationOutcome)
    rule = {
        "kind": "RULE_REJECTED",
        "lexicon_version": "lexicon-v1",
        "matches": [{"rule_id": "r1", "variant_id": "exact"}],
    }
    assert adapter.validate_python(rule).kind == "RULE_REJECTED"
    assert adapter.validate_python({"kind": "MODEL_ANALYZED", "analysis": analysis}).kind == "MODEL_ANALYZED"
    assert adapter.validate_python({"kind": "RUN_FAILED", "error_code": "INFERENCE_TIMEOUT"}).kind == "RUN_FAILED"
    with pytest.raises(ValidationError):
        adapter.validate_python({**rule, "analysis": analysis})
    with pytest.raises(ValidationError):
        adapter.validate_python({**rule, "matches": []})
    with pytest.raises(ValidationError):
        adapter.validate_python({**rule, "matches": rule["matches"] * 2})


def test_development_fake_cannot_load_in_production():
    settings = SimpleNamespace(
        analyzer="fake",
        environment="production",
        model_bundle_version="fake-v1",
        inference_policy_version="inference-v1",
    )
    with pytest.raises(ValueError, match="forbidden"):
        load_analyzer(settings)


def test_fake_fixtures_exercise_nonpublic_paths():
    analyzer = FakeAnalyzer()
    assert analyzer.analyze(AnalysisInput(text="[fixture:block]")).safety.verdict == "BLOCK"
    assert analyzer.analyze(AnalysisInput(text="[fixture:uncertain]")).emotion is None
    assert analyzer.analyze(AnalysisInput(text="오늘 좀 화나네")).safety.verdict == "ALLOW"


def test_real_artifacts_are_verified_before_factory_executes(tmp_path, monkeypatch):
    artifact = tmp_path / "weights.fixture"
    artifact.write_bytes(b"synthetic frozen artifact")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "model_bundle_version": "local-v1",
                "inference_policy_version": "inference-v1",
                "artifacts": {"weights.fixture": hashlib.sha256(artifact.read_bytes()).hexdigest()},
            }
        ),
        encoding="utf-8",
    )
    module = types.ModuleType("moodwall_synthetic_plugin")
    calls = []

    def factory():
        calls.append(True)
        return FakeAnalyzer("local-v1", "inference-v1")

    module.factory = factory
    module.MANIFEST_PATH = manifest
    monkeypatch.setitem(sys.modules, module.__name__, module)
    settings = SimpleNamespace(
        analyzer=module.__name__ + ":factory",
        environment="test",
        model_bundle_version="local-v1",
        inference_policy_version="inference-v1",
    )
    assert load_analyzer(settings).model_bundle_version == "local-v1"
    assert len(calls) == 1
    artifact.write_bytes(b"tampered synthetic artifact")
    with pytest.raises(ValueError, match="hash mismatch"):
        load_analyzer(settings)
    assert len(calls) == 1, "factory ran before the corrupted artifact was rejected"


def test_context_factory_receives_the_verified_bundle_and_deployment(tmp_path, monkeypatch):
    artifact = tmp_path / "model.fixture"
    artifact.write_bytes(b"synthetic local model")
    manifest = {
        "model_bundle_version": "context-v1",
        "inference_policy_version": "review-only-v1",
        "artifacts": {artifact.name: hashlib.sha256(artifact.read_bytes()).hexdigest()},
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    module = types.ModuleType("moodwall_context_plugin")
    received = []

    def factory(*, context):
        received.append(context)
        return FakeAnalyzer(context.manifest["model_bundle_version"], context.manifest["inference_policy_version"])

    module.factory = factory
    monkeypatch.setitem(sys.modules, module.__name__, module)
    settings = SimpleNamespace(
        analyzer=module.__name__ + ":factory",
        environment="test",
        model_manifest_path=str(manifest_path),
        model_bundle_version="context-v1",
        inference_policy_version="review-only-v1",
    )
    load_analyzer(settings)
    assert len(received) == 1
    assert received[0].manifest_path == manifest_path.resolve()
    assert received[0].manifest == manifest
    assert received[0].environment == "test"
    artifact.write_bytes(b"changed artifact")
    with pytest.raises(ValueError, match="hash mismatch"):
        load_analyzer(settings)
    assert len(received) == 1
