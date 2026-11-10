"""Current outbound policy and legacy decoding, using synthetic responses only."""

import json

import pytest

import inference.provider_moderation as provider
from inference.contracts import AnalysisInput, EmotionResult

ALLOW = {"schema_version": "1", "decision": "ALLOW", "reason_codes": [], "policy_rule_ids": []}


class StaticTransport:
    def __init__(self, result):
        self.result = result
        self.request = None

    def respond(self, request):
        self.request = request
        return {
            "model": provider.MODEL_ID,
            "status": "completed",
            "steps": [{"type": "model_output", "content": [{"type": "text", "text": json.dumps(self.result)}]}],
            "usage": {"total_input_tokens": 100, "total_output_tokens": 20, "total_tokens": 120},
        }


def test_compiled_schema_excludes_retired_help_outputs_and_uses_pinned_asset():
    request = provider.compile_request(provider.redact_input("살기 싫다"), _mock=False)
    schema = request["response_format"]["schema"]
    assert schema == json.loads(provider._resource("moderation-response.schema.json"))
    properties = schema["properties"]
    assert properties["decision"]["enum"] == ["ALLOW", "BLOCK", "UNCERTAIN"]
    assert "SELF_CRISIS" not in properties["reason_codes"]["items"]["enum"]
    assert "CP08" not in properties["policy_rule_ids"]["items"]["enum"]


def test_outbound_schema_policy_change_changes_release_identity(monkeypatch):
    current = provider.compute_release_id()
    resource = provider._resource
    legacy = json.loads(resource("moderation-response.schema.json"))
    legacy["properties"]["decision"]["enum"].append("SUPPORT")
    legacy["properties"]["reason_codes"]["items"]["enum"].append("SELF_CRISIS")
    legacy["properties"]["policy_rule_ids"]["items"]["enum"].append("CP08")
    monkeypatch.setattr(
        provider,
        "_resource",
        lambda name: json.dumps(legacy).encode() if name == "moderation-response.schema.json" else resource(name),
    )
    assert provider.compute_release_id() != current


@pytest.mark.parametrize("mixed_violation", [False, True], ids=["legacy_support", "support_with_hate"])
def test_live_analyzer_rejects_retired_support_without_autoallow_or_new_help(mixed_violation):
    result = {
        "schema_version": "1",
        "decision": "SUPPORT",
        "reason_codes": ["SELF_CRISIS"] + (["HATE"] if mixed_violation else []),
        "policy_rule_ids": ["CP08"] + (["CP02"] if mixed_violation else []),
    }
    # Old records remain decodable, including their independent prohibitions.
    legacy = provider.parse_moderation_json(json.dumps(result))
    assert legacy.decision == "SUPPORT" and legacy.reason_codes == result["reason_codes"]

    transport = StaticTransport(result)
    emotions = []
    analyzer = provider.GeminiModerationAnalyzer(
        "synthetic-release",
        "synthetic-policy",
        transport=transport,
        moderation_profile="gemini",
        emotion_predictor=emotions.append,
    )
    with pytest.raises(provider.ProviderModerationError, match="INVALID_MODEL_OUTPUT"):
        analyzer.analyze(AnalysisInput(text="합성 정책 검증"))
    assert emotions == []
    assert analyzer.last_usage == {"input_tokens": 100, "output_tokens": 20}


@pytest.mark.parametrize("decision", ["ALLOW", "BLOCK"])
def test_current_live_decisions_keep_emotion_and_independent_harm_policy(decision):
    result = (
        ALLOW
        if decision == "ALLOW"
        else {
            **ALLOW,
            "decision": "BLOCK",
            "reason_codes": ["OTHER_UNSAFE"],
            "policy_rule_ids": ["CP07"],
        }
    )
    emotions = []

    def predict(value):
        emotions.append(value.text)
        return EmotionResult(label="SADNESS", source="MODEL", score=0.8)

    analyzer = provider.GeminiModerationAnalyzer(
        "synthetic-release",
        "synthetic-policy",
        transport=StaticTransport(result),
        moderation_profile="gemini",
        emotion_predictor=predict,
    )
    analysis = analyzer.analyze(AnalysisInput(text="살기 싫다" if decision == "ALLOW" else "합성 위해 정책 검증"))
    assert analysis.safety.verdict == decision
    assert "SELF_CRISIS" not in analysis.safety.reason_codes
    if decision == "ALLOW":
        assert emotions == ["살기 싫다"] and analysis.emotion.label == "SADNESS"
    else:
        assert emotions == [] and analysis.emotion is None
