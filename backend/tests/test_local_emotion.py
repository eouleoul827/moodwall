"""Synthetic bundles and transport only; no provider calls or model downloads."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.config import Settings
from inference import local_emotion, student_model
from inference.contracts import AnalysisInput
from inference.loader import load_analyzer
from inference.provider_moderation import MODEL_ID, GeminiModerationAnalyzer


def distribution(best):
    return [0.93 if index == best else 0.01 for index in range(8)]


@pytest.fixture
def bundle(tmp_path):
    root = tmp_path / "synthetic-bundle"
    root.mkdir()
    config = {
        "model_type": "electra",
        "id2label": {str(index): label for index, label in enumerate(student_model.LABELS)},
        "label2id": {label: index for index, label in enumerate(student_model.LABELS)},
    }
    artifacts = {}
    for name in sorted(student_model.REQUIRED_ARTIFACTS):
        payload = json.dumps(config).encode() if name == "config.json" else b"synthetic local model artifact"
        (root / name).write_bytes(payload)
        artifacts[name] = hashlib.sha256(payload).hexdigest()
    manifest = {
        "model_bundle_version": "synthetic-emotion-fixture",
        "inference_policy_version": "student-review-only-v1",
        "artifacts": artifacts,
        "student_model": dict(student_model.STUDENT_MODEL_SPEC),
    }
    path = root / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def settings_for(bundle=None, **overrides):
    return Settings(
        _env_file=None,
        database_url="postgresql://postgres@localhost/moodwall_test",
        secret_key="synthetic-emotion-session-key-01234567890",
        qr_key="synthetic-emotion-qr-key-0123456789012345",
        environment="test",
        moderation_profile="gemini_mock",
        emotion_manifest_path=str(bundle) if bundle else None,
        **overrides,
    )


@pytest.mark.parametrize("best", range(7))
def test_each_local_color_maps_without_safety_fields(best):
    predictor = local_emotion.LocalEmotionPredictor(lambda _: distribution(best))
    result = predictor(AnalysisInput(text="synthetic approved text"))
    assert result.label == student_model.EMOTIONS[best]
    assert result.source == "MODEL" and result.score == 0.93
    assert set(result.model_dump()) == {"label", "score", "source"}


@pytest.mark.parametrize("values", [distribution(7), [0.125] * 8, [0.5, 0.5] + [0.0] * 6, [], [float("nan")] * 8])
def test_inappropriate_uncertain_and_invalid_local_output_is_neutral(values):
    result = local_emotion.LocalEmotionPredictor(lambda _: values)(AnalysisInput(text="synthetic approved text"))
    assert result.model_dump() == {"label": "NEUTRAL", "source": "FALLBACK", "score": None}


def test_local_runtime_exception_is_neutral():
    def broken(_):
        raise RuntimeError("synthetic local failure")

    assert local_emotion.LocalEmotionPredictor(broken)(AnalysisInput(text="approved")).label == "NEUTRAL"


@pytest.mark.parametrize(
    "decision,reasons,rules,expected_calls",
    [
        ("ALLOW", [], [], 1),
        ("BLOCK", ["HARASSMENT"], ["CP01"], 0),
        ("UNCERTAIN", ["INSUFFICIENT_CONTEXT"], ["CP10"], 0),
        ("SUPPORT", ["SELF_CRISIS"], ["CP08"], 0),
    ],
)
def test_local_model_runs_only_after_gemini_allow(decision, reasons, rules, expected_calls):
    calls = []
    transport_calls = []

    class Transport:
        def respond(self, _):
            transport_calls.append(True)
            result = {"schema_version": "1", "decision": decision, "reason_codes": reasons, "policy_rule_ids": rules}
            return {
                "model": MODEL_ID,
                "status": "completed",
                "steps": [{"type": "model_output", "content": [{"type": "text", "text": json.dumps(result)}]}],
            }

    def predict(text):
        calls.append(text)
        return distribution(1)

    analyzer = GeminiModerationAnalyzer(
        "synthetic", "policy", transport=Transport(), emotion_predictor=local_emotion.LocalEmotionPredictor(predict)
    )
    result = analyzer.analyze(AnalysisInput(text="synthetic approved text"))
    assert len(calls) == expected_calls
    assert len(transport_calls) == 1
    assert result.safety.verdict == ("BLOCK" if decision == "SUPPORT" else decision)
    assert result.safety.reason_codes == reasons
    assert (result.emotion.label if result.emotion else None) == ("JOY" if expected_calls else None)


@pytest.mark.parametrize("values", [distribution(7), [0.125] * 8])
def test_local_inappropriate_or_ambiguous_class_cannot_reject_gemini_approved_text(values):
    class Transport:
        def respond(self, _):
            result = {"schema_version": "1", "decision": "ALLOW", "reason_codes": [], "policy_rule_ids": []}
            return {
                "model": MODEL_ID,
                "status": "completed",
                "steps": [{"type": "model_output", "content": [{"type": "text", "text": json.dumps(result)}]}],
            }

    analyzer = GeminiModerationAnalyzer(
        "synthetic",
        "policy",
        transport=Transport(),
        emotion_predictor=local_emotion.LocalEmotionPredictor(lambda _: values),
    )
    result = analyzer.analyze(AnalysisInput(text="synthetic approved text"))
    assert result.safety.verdict == "ALLOW" and result.safety.reason_codes == []
    assert result.emotion.label == "NEUTRAL"


def test_bundle_identity_is_location_independent_and_covers_every_artifact(bundle, tmp_path):
    original = local_emotion.verify_emotion_bundle(bundle).release_parameters
    copied = tmp_path / "identical-copy"
    shutil.copytree(bundle.parent, copied)
    assert local_emotion.verify_emotion_bundle(copied / "manifest.json").release_parameters == original
    manifest = json.loads(bundle.read_text())
    (bundle.parent / "tokenizer.json").write_bytes(b"different tokenizer")
    manifest["artifacts"]["tokenizer.json"] = hashlib.sha256(b"different tokenizer").hexdigest()
    bundle.write_text(json.dumps(manifest))
    changed = local_emotion.verify_emotion_bundle(bundle).release_parameters
    assert changed["bundle_sha256"] != original["bundle_sha256"]
    assert changed["manifest_sha256"] != original["manifest_sha256"]


@pytest.mark.parametrize("name", sorted(student_model.REQUIRED_ARTIFACTS))
def test_each_artifact_is_verified_before_loading(bundle, monkeypatch, name):
    (bundle.parent / name).write_bytes(b"tampered")
    monkeypatch.setattr(student_model, "_load_cpu_predictor", lambda *a, **k: pytest.fail("model loaded"))
    with pytest.raises(ValueError):
        local_emotion.load_emotion_predictor(bundle, expected_parameters={})


def test_wrong_labels_and_unpinned_sidecars_fail_configuration(bundle):
    config_path = bundle.parent / "config.json"
    config = json.loads(config_path.read_text())
    config["id2label"]["0"] = "unexpected class"
    config_path.write_text(json.dumps(config))
    with pytest.raises(ValueError, match="label"):
        settings_for(bundle)


def test_declared_bundle_identity_cannot_lie(bundle):
    manifest = json.loads(bundle.read_text())
    manifest["bundle_identity"] = {"digest": "0" * 64}
    bundle.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="identity"):
        settings_for(bundle)


def test_extra_sidecar_and_duplicate_manifest_fields_are_rejected(bundle):
    sidecar = bundle.parent / "added_tokens.json"
    sidecar.write_text("{}")
    with pytest.raises(ValueError, match="four pinned artifacts"):
        settings_for(bundle)
    sidecar.unlink()
    original = bundle.read_text()
    bundle.write_text('{"student_model":{},' + original[1:])
    with pytest.raises(ValueError, match="duplicate"):
        settings_for(bundle)


def test_release_is_cached_secret_free_and_immutable_to_callers(bundle, monkeypatch):
    settings = settings_for(bundle)
    first = settings.provider_release_parameters
    assert first["local_emotion"]["model_bundle_version"] == "synthetic-emotion-fixture"
    assert "manifest_path" not in json.dumps(first)
    monkeypatch.setattr(local_emotion, "verify_emotion_bundle", lambda _: pytest.fail("rehashed on request"))
    first["local_emotion"]["artifacts"]["config.json"] = "changed"
    assert settings.provider_release_parameters["local_emotion"]["artifacts"]["config.json"] != "changed"
    assert settings.model_bundle_version != settings_for().model_bundle_version


def test_loader_rechecks_and_loads_exactly_once_with_bounded_startup_warmup(bundle, monkeypatch):
    settings = settings_for(bundle)
    calls = []

    def load(directory, **options):
        calls.append((directory, options))
        return lambda _: distribution(0)

    monkeypatch.setattr(student_model, "_load_cpu_predictor", load)
    analyzer = load_analyzer(settings)
    assert len(calls) == 1
    assert calls[0] == (bundle.parent, {"cpu_threads": 2, "max_length": 64})
    assert analyzer._emotion_predictor(AnalysisInput(text="approved")).label == "CELEBRATION"
    assert len(calls) == 1
    (bundle.parent / "model.safetensors").write_bytes(b"changed after settings")
    with pytest.raises(ValueError, match="hash mismatch"):
        load_analyzer(settings)
    assert len(calls) == 1


def test_bad_explicit_bundle_never_silently_disables_local_model(bundle, monkeypatch):
    with pytest.raises(OSError):
        settings_for(bundle.parent / "missing.json")
    settings = settings_for(bundle)
    monkeypatch.setattr(student_model, "_load_cpu_predictor", lambda *a, **k: lambda _: [])
    with pytest.raises(ValueError, match="probabilities"):
        load_analyzer(settings)


def test_standalone_legacy_guard_remains_while_presentation_only_can_load_in_production(bundle, monkeypatch):
    verified = local_emotion.verify_emotion_bundle(bundle)
    context = SimpleNamespace(environment="production", manifest_path=bundle, manifest=verified.manifest)
    monkeypatch.setattr(student_model, "_load_cpu_predictor", lambda *a, **k: lambda _: distribution(2))
    with pytest.raises(ValueError, match="forbidden"):
        student_model.create_analyzer(context=context)
    predictor = local_emotion.load_emotion_predictor(bundle, expected_parameters=verified.release_parameters)
    assert predictor(AnalysisInput(text="approved")).label == "SUPPORT"


def test_real_profile_release_includes_local_emotion_without_secrets(bundle, monkeypatch):
    monkeypatch.setattr("app.config.datetime", SimpleNamespace(now=lambda *_: datetime(2026, 11, 5, tzinfo=UTC)))
    settings = Settings(
        _env_file=None,
        database_url="postgresql://postgres@localhost/moodwall_test",
        secret_key="synthetic-emotion-session-key-01234567890",
        qr_key="synthetic-emotion-qr-key-0123456789012345",
        moderation_profile="gemini",
        gemini_api_key="synthetic-secret-never-sent",
        emotion_manifest_path=str(bundle),
    )
    assert "local_emotion" in settings.provider_release_parameters
    assert "synthetic-secret" not in json.dumps(settings.provider_release_parameters)
    assert not settings.auto_strikes


def test_local_emotion_config_is_opt_in_and_does_not_import_heavy_dependencies():
    subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import inference.local_emotion; "
            "assert 'torch' not in sys.modules; assert 'transformers' not in sys.modules",
        ],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        check=True,
        timeout=20,
    )
    assert "local_emotion" not in settings_for().provider_release_parameters
