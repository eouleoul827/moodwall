"""Opt-in local-artifact checks; ordinary CI does not need torch or model files."""

import math

import pytest

from inference.contracts import AnalysisInput, AnalysisResult
from inference.student_model import LABELS
from tools.check_student_model import SYNTHETIC_EXAMPLES


def test_real_v0_scores_are_finite_normalized_and_repeatable(real_student_analyzer):
    text = SYNTHETIC_EXAMPLES[0][1]
    first = real_student_analyzer.predict(text)
    second = real_student_analyzer.predict(text)
    assert first.label in LABELS
    assert len(first.probabilities) == 8
    assert all(math.isfinite(value) and 0 <= value <= 1 for value in first.probabilities)
    assert math.fsum(first.probabilities) == pytest.approx(1.0, abs=1e-6)
    assert second.label == first.label
    assert second.probabilities == pytest.approx(first.probabilities, rel=0, abs=1e-7)


def test_real_v0_stays_nonpublic_after_successful_inference(real_student_analyzer):
    result = AnalysisResult.model_validate(real_student_analyzer.analyze(AnalysisInput(text=SYNTHETIC_EXAMPLES[1][1])))
    assert result.model_bundle_version == real_student_analyzer.model_bundle_version
    assert result.inference_policy_version == real_student_analyzer.inference_policy_version
    assert result.safety.verdict == "UNCERTAIN"
    assert result.safety.reason_codes == ["INSUFFICIENT_CONTEXT"]
    assert result.safety.score is None
    assert result.emotion is None


def test_real_tokenizer_receives_unmodified_30_grapheme_input(real_student_analyzer, monkeypatch):
    import regex

    original = SYNTHETIC_EXAMPLES[-1][1]
    assert len(regex.findall(r"\X", original)) == 30
    assert len(original) > 30
    predictor = real_student_analyzer._predictor
    tokenize = predictor._tokenizer
    calls = []

    def observed_tokenizer(text, **options):
        calls.append((text, options))
        return tokenize(text, **options)

    monkeypatch.setattr(predictor, "_tokenizer", observed_tokenizer)
    result = real_student_analyzer.predict(original)
    assert len(result.probabilities) == 8
    assert calls == [(original, {"truncation": True, "max_length": 64, "return_tensors": "pt"})]
