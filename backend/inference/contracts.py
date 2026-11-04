from __future__ import annotations

from typing import Annotated, Literal, Protocol, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

Emotion = Literal["CELEBRATION", "JOY", "SUPPORT", "EXCITEMENT", "TIRED", "SADNESS", "ANGER", "NEUTRAL"]
ReasonCode = Literal[
    "HARASSMENT",
    "HATE",
    "SEXUAL_CONTENT",
    "THREAT",
    "PERSONAL_INFORMATION",
    "OTHER_UNSAFE",
    "INSUFFICIENT_CONTEXT",
    "OUT_OF_DOMAIN",
    "SELF_CRISIS",
]


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, allow_inf_nan=False)


class AnalysisInput(ContractModel):
    schema_version: Literal["1"] = "1"
    text: str = Field(min_length=1)


class SafetyResult(ContractModel):
    verdict: Literal["ALLOW", "BLOCK", "UNCERTAIN"]
    score: float | None = Field(default=None, ge=0, le=1)
    reason_codes: list[ReasonCode] = Field(default_factory=list, max_length=5)

    @model_validator(mode="after")
    def validate_reasons(self) -> Self:
        if len(set(self.reason_codes)) != len(self.reason_codes):
            raise ValueError("duplicate reason code")
        if (self.verdict == "ALLOW") != (not self.reason_codes):
            raise ValueError("verdict and reasons disagree")
        if "SELF_CRISIS" in self.reason_codes and self.verdict != "BLOCK":
            raise ValueError("SELF_CRISIS requires a nonpublic BLOCK result")
        return self


class EmotionResult(ContractModel):
    label: Emotion
    score: float | None = Field(default=None, ge=0, le=1)
    source: Literal["MODEL", "FALLBACK"] = "MODEL"

    @model_validator(mode="after")
    def validate_fallback(self) -> Self:
        if self.source == "FALLBACK" and (self.label != "NEUTRAL" or self.score is not None):
            raise ValueError("fallback must be unscored NEUTRAL")
        return self


class AnalysisResult(ContractModel):
    schema_version: Literal["1"] = "1"
    model_bundle_version: str = Field(min_length=1, max_length=100)
    inference_policy_version: str = Field(min_length=1, max_length=100)
    safety: SafetyResult
    emotion: EmotionResult | None

    @model_validator(mode="after")
    def validate_combination(self) -> Self:
        if (self.safety.verdict == "ALLOW") != (self.emotion is not None):
            raise ValueError("ALLOW requires emotion; nonpublic results forbid emotion")
        return self


class Analyzer(Protocol):
    def analyze(self, value: AnalysisInput) -> AnalysisResult: ...


class RuleEvidence(ContractModel):
    rule_id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_.-]+$")
    variant_id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_.-]+$")


class RuleRejected(ContractModel):
    kind: Literal["RULE_REJECTED"] = "RULE_REJECTED"
    lexicon_version: str = Field(min_length=1, max_length=100)
    matches: list[RuleEvidence] = Field(min_length=1, max_length=5)

    @model_validator(mode="after")
    def validate_matches(self) -> Self:
        keys = [(item.rule_id, item.variant_id) for item in self.matches]
        if keys != sorted(set(keys)):
            raise ValueError("rule evidence must be unique and sorted")
        return self


class ModelAnalyzed(ContractModel):
    kind: Literal["MODEL_ANALYZED"] = "MODEL_ANALYZED"
    analysis: AnalysisResult


class RunFailed(ContractModel):
    kind: Literal["RUN_FAILED"] = "RUN_FAILED"
    error_code: Literal[
        "INPUT_INVALID",
        "MODEL_NOT_READY",
        "TRANSIENT_DEPENDENCY",
        "INFERENCE_TIMEOUT",
        "INVALID_MODEL_OUTPUT",
        "WORKER_LOST",
        "POLICY_VERSION_MISMATCH",
        "LEXICON_ERROR",
        "INTERNAL_ERROR",
        "DEADLINE_EXCEEDED",
    ]


FinalizationOutcome = Annotated[RuleRejected | ModelAnalyzed | RunFailed, Field(discriminator="kind")]


class FinalizationEnvelope(ContractModel):
    submission_id: UUID
    run_token: UUID
    outcome: FinalizationOutcome
