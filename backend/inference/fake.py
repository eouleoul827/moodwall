"""Development fixture, not a trained moderation model."""

import time

from inference.contracts import AnalysisInput, AnalysisResult, EmotionResult, SafetyResult


class FakeAnalyzer:
    def __init__(self, model_version: str = "fake-v1", policy_version: str = "inference-v1"):
        self.model_bundle_version = model_version
        self.inference_policy_version = policy_version

    def analyze(self, value: AnalysisInput) -> AnalysisResult:
        # Explicit fixtures exercise unavailable/timeout paths without student data.
        if value.text == "[fixture:error]":
            raise RuntimeError("synthetic inference failure")
        if value.text == "[fixture:hang]":
            while True:
                time.sleep(1)
        verdict = "ALLOW"
        reasons = []
        if value.text == "[fixture:block]":
            verdict, reasons = "BLOCK", ["HARASSMENT"]
        elif value.text == "[fixture:uncertain]":
            verdict, reasons = "UNCERTAIN", ["INSUFFICIENT_CONTEXT"]
        label = "NEUTRAL"
        for word, candidate in [
            ("축하", "CELEBRATION"),
            ("기뻐", "JOY"),
            ("힘내", "SUPPORT"),
            ("설레", "EXCITEMENT"),
            ("피곤", "TIRED"),
            ("슬퍼", "SADNESS"),
            ("화나", "ANGER"),
        ]:
            if word in value.text:
                label = candidate
                break
        return AnalysisResult(
            model_bundle_version=self.model_bundle_version,
            inference_policy_version=self.inference_policy_version,
            safety=SafetyResult(verdict=verdict, reason_codes=reasons),
            emotion=EmotionResult(label=label) if verdict == "ALLOW" else None,
        )
