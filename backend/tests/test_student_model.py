import copy
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from inference import student_model
from inference.contracts import AnalysisInput


def probabilities(best):
    return [0.93 if index == best else 0.01 for index in range(8)]


@pytest.fixture
def context(tmp_path):
    config = {
        "model_type": "electra",
        "id2label": {str(index): label for index, label in enumerate(student_model.LABELS)},
        "label2id": {label: index for index, label in enumerate(student_model.LABELS)},
    }
    for name in student_model.REQUIRED_ARTIFACTS:
        (tmp_path / name).write_text(json.dumps(config) if name == "config.json" else "fixture", encoding="utf-8")
    manifest = {
        "model_bundle_version": "student-v0-fixture",
        "inference_policy_version": "review-only-v1",
        "artifacts": {name: "verified-by-loader" for name in student_model.REQUIRED_ARTIFACTS},
        "student_model": copy.deepcopy(student_model.STUDENT_MODEL_SPEC),
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return SimpleNamespace(environment="test", manifest_path=path, manifest=manifest)


@pytest.mark.parametrize("best", range(8))
def test_v0_class_order_and_full_precision_scores(best):
    result = student_model.prediction_from_probabilities(probabilities(best))
    assert result.label == student_model.LABELS[best]
    assert result.emotion == student_model.EMOTIONS[best]
    assert result.score == 0.93
    assert result.inappropriate_score == (0.93 if best == 7 else 0.01)
    assert result.probabilities == tuple(probabilities(best))


@pytest.mark.parametrize(
    "values",
    [
        [],
        [1 / 7] * 7,
        [1 / 9] * 9,
        [0.0] * 8,
        [0.2] * 8,
        [float("nan")] + [0.0] * 7,
        [float("inf")] + [0.0] * 7,
        [-0.1, 1.1] + [0.0] * 6,
        [True] + [0.0] * 7,
        ["1"] + [0.0] * 7,
    ],
)
def test_invalid_probabilities_never_become_a_result(values):
    with pytest.raises(ValueError, match="probabilities"):
        student_model.prediction_from_probabilities(values)


def test_softmax_roundoff_is_accepted_but_scores_are_not_rounded():
    values = [0.10000000149] * 7 + [0.30000001192]
    result = student_model.prediction_from_probabilities(values)
    assert result.score == values[-1]
    assert result.inappropriate_score == values[-1]


@pytest.mark.parametrize("field", ["missing_keys", "unexpected_keys", "mismatched_keys", "error_msgs"])
def test_partially_initialized_or_incompatible_weights_cannot_be_used(field):
    info = {"missing_keys": set(), "unexpected_keys": set(), "mismatched_keys": set(), "error_msgs": []}
    student_model._validate_loading_info(info)
    info[field] = ["synthetic incompatible tensor"]
    with pytest.raises(ValueError, match="checkpoint"):
        student_model._validate_loading_info(info)
    del info[field]
    with pytest.raises(ValueError, match="incomplete"):
        student_model._validate_loading_info(info)


@pytest.mark.parametrize("best", range(8))
def test_all_v0_classes_remain_uncertain_without_an_approved_safety_policy(best):
    calls = []

    def predictor(text):
        calls.append(text)
        return probabilities(best)

    analyzer = student_model.StudentModelAnalyzer(
        model_bundle_version="student-v0-fixture",
        inference_policy_version="review-only-v1",
        predictor=predictor,
    )
    original = "  합격 축하해 👨‍👩‍👧‍👦👨‍👩‍👧‍👦👨‍👩‍👧‍👦👨‍👩‍👧‍👦  "
    assert len(original) > 30
    result = analyzer.analyze(AnalysisInput(text=original))
    assert calls == [original]
    assert result.model_bundle_version == "student-v0-fixture"
    assert result.inference_policy_version == "review-only-v1"
    assert result.safety.verdict == "UNCERTAIN"
    assert result.safety.reason_codes == ["INSUFFICIENT_CONTEXT"]
    assert result.safety.score is None
    assert result.emotion is None


def test_inference_failure_propagates_without_fabricating_a_safety_decision():
    failure = RuntimeError("synthetic inference failure")

    def predictor(_text):
        raise failure

    analyzer = student_model.StudentModelAnalyzer(
        model_bundle_version="student-v0-fixture",
        inference_policy_version="review-only-v1",
        predictor=predictor,
    )
    with pytest.raises(RuntimeError) as raised:
        analyzer.analyze(AnalysisInput(text="synthetic input"))
    assert raised.value is failure


def test_factory_loads_once_after_validating_policy_and_labels(context, monkeypatch):
    calls = []

    def load(bundle_dir, **options):
        calls.append((bundle_dir, options))
        return lambda _text: probabilities(0)

    monkeypatch.setattr(student_model, "_load_cpu_predictor", load)
    analyzer = student_model.create_analyzer(context=context)
    assert calls == [(context.manifest_path.parent, {"cpu_threads": 2, "max_length": 64})]
    assert analyzer.predict("synthetic input").emotion == "CELEBRATION"
    assert analyzer.analyze(AnalysisInput(text="synthetic input")).safety.verdict == "UNCERTAIN"
    assert len(calls) == 1


@pytest.mark.parametrize("environment", ["production", "staging", "", None])
def test_v0_cannot_initialize_outside_development_and_test(context, monkeypatch, environment):
    context.environment = environment
    monkeypatch.setattr(student_model, "_load_cpu_predictor", lambda *_args, **_kwargs: pytest.fail("loaded a model"))
    with pytest.raises(ValueError, match="forbidden"):
        student_model.create_analyzer(context=context)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", 1),
        ("schema_version", "2"),
        ("kind", "approved-safety-model"),
        ("max_length", 30),
        ("max_length", 64.0),
        ("cpu_threads", 0),
        ("cpu_threads", True),
        ("safety_policy", "allow-by-threshold"),
        ("unsafe_threshold", 0.2),
    ],
)
def test_unsupported_or_ambiguous_policy_cannot_load(context, monkeypatch, field, value):
    context.manifest["student_model"][field] = value
    monkeypatch.setattr(student_model, "_load_cpu_predictor", lambda *_args, **_kwargs: pytest.fail("loaded a model"))
    with pytest.raises(ValueError, match="policy"):
        student_model.create_analyzer(context=context)


@pytest.mark.parametrize("missing", sorted(student_model.REQUIRED_ARTIFACTS))
def test_each_local_model_file_must_be_pinned(context, missing):
    del context.manifest["artifacts"][missing]
    with pytest.raises(ValueError, match="four"):
        student_model.create_analyzer(context=context)


def test_unpinned_tokenizer_sidecar_cannot_change_inference(context):
    (context.manifest_path.parent / "added_tokens.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="four pinned artifacts"):
        student_model.create_analyzer(context=context)


@pytest.mark.parametrize("mutation", ["id_order", "missing_id", "label_id", "boolean_id", "architecture", "head_size"])
def test_wrong_model_label_config_is_rejected_before_loading(context, monkeypatch, mutation):
    path = context.manifest_path.parent / "config.json"
    config = json.loads(path.read_text(encoding="utf-8"))
    if mutation == "id_order":
        config["id2label"]["0"], config["id2label"]["1"] = config["id2label"]["1"], config["id2label"]["0"]
    elif mutation == "missing_id":
        del config["id2label"]["7"]
    elif mutation == "label_id":
        config["label2id"]["부적절"] = 0
    elif mutation == "boolean_id":
        config["label2id"]["기쁨"] = True
    elif mutation == "architecture":
        config["model_type"] = "bert"
    else:
        config["num_labels"] = 9
    path.write_text(json.dumps(config), encoding="utf-8")
    monkeypatch.setattr(student_model, "_load_cpu_predictor", lambda *_args, **_kwargs: pytest.fail("loaded a model"))
    with pytest.raises(ValueError, match="student model"):
        student_model.create_analyzer(context=context)


def test_module_import_does_not_import_optional_model_dependencies():
    subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import inference.student_model; "
            "assert 'torch' not in sys.modules; assert 'transformers' not in sys.modules",
        ],
        cwd=Path(__file__).resolve().parents[1],
        check=True,
        capture_output=True,
        text=True,
        timeout=20,
    )
