"""Claude (Haiku 5.5) analyzer plugged in through MOODWALL_ANALYZER.

Usage: MOODWALL_ANALYZER=inference.claude_analyzer:create_analyzer with the
claude_bundle manifest (see compose.claude.yaml). Only the worker loads this.

One Messages API call per submission decides both public safety and emotion.
The reviewed Gemini policy prompt (resources/moderation-prompt.txt) is reused
as-is; claude_bundle/emotion-addendum.txt adds emotion labels and the bypass
profanity rule. No retries, no tools, no conversation history. The model only
ever sees redacted text, never student IDs, IPs or tickets. Any malformed or
unexpected answer fails closed (the post is not published).
"""

from __future__ import annotations

import hashlib
import json
import os

import httpx
from pydantic import Field, SecretStr, ValidationError

from inference.contracts import AnalysisInput, AnalysisResult, ContractModel, EmotionResult, SafetyResult
from inference.provider_moderation import (
    ProviderModerationError,
    _resource,
    _strict_json,
    redact_input,
)

ENDPOINT = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"
DEFAULT_MODEL = "claude-haiku-5-5"
MAX_OUTPUT_TOKENS = 400
MAX_RESPONSE_BYTES = 65536
EMOTIONS = ("CELEBRATION", "JOY", "SUPPORT", "EXCITEMENT", "TIRED", "SADNESS", "ANGER", "NEUTRAL")
REASONS = {"HARASSMENT", "HATE", "SEXUAL_CONTENT", "THREAT", "PERSONAL_INFORMATION", "OTHER_UNSAFE",
           "INSUFFICIENT_CONTEXT", "OUT_OF_DOMAIN"}
UNCERTAIN_REASONS = {"INSUFFICIENT_CONTEXT", "OUT_OF_DOMAIN"}


class ClaudeOutput(ContractModel):
    schema_version: str
    decision: str
    reason_codes: list[str]
    policy_rule_ids: list[str]
    emotion: str | None
    emotion_score: float | int | None = Field(default=None)


def _read_key() -> SecretStr:
    for name in ("MOODWALL_CLAUDE_API_KEY", "ANTHROPIC_API_KEY"):
        value = (os.environ.get(name) or "").strip()
        if value:
            return SecretStr(value)
    raise ProviderModerationError("MODEL_NOT_READY", failure_kind="AUTH")


class ClaudeAnalyzer:
    def __init__(
        self,
        *,
        api_key: SecretStr,
        system_prompt: str,
        model_bundle_version: str,
        inference_policy_version: str,
        model: str = DEFAULT_MODEL,
        timeout_seconds: float = 15,
        transport: httpx.BaseTransport | None = None,
    ):
        self.__api_key = api_key
        self._system = system_prompt
        self._model = model
        self.model_bundle_version = model_bundle_version
        self.inference_policy_version = inference_policy_version
        self._client = httpx.Client(
            timeout=httpx.Timeout(timeout_seconds, connect=3),
            follow_redirects=False,
            trust_env=False,
            transport=transport or httpx.HTTPTransport(retries=0),
        )
        self.last_usage: dict[str, int] | None = None
        self.last_debug: str | None = None

    def __repr__(self) -> str:
        return f"ClaudeAnalyzer(model={self._model!r}, api_key='**********')"

    def close(self) -> None:
        self._client.close()

    def _call(self, payload: dict) -> str:
        try:
            response = self._client.post(
                ENDPOINT,
                json=payload,
                headers={
                    "x-api-key": self.__api_key.get_secret_value(),
                    "anthropic-version": API_VERSION,
                    "content-type": "application/json",
                },
            )
        except httpx.TimeoutException:
            raise ProviderModerationError("INFERENCE_TIMEOUT") from None
        except Exception:
            # httpx errors can carry headers; never propagate them.
            raise ProviderModerationError("TRANSIENT_DEPENDENCY") from None
        status = response.status_code
        if status in (401, 403, 404):
            raise ProviderModerationError("MODEL_NOT_READY", failure_kind="AUTH")
        if status == 429 or status >= 500:
            raise ProviderModerationError("TRANSIENT_DEPENDENCY")
        if status != 200 or len(response.content) > MAX_RESPONSE_BYTES:
            raise ProviderModerationError("INVALID_MODEL_OUTPUT")
        body = _strict_json(response.content, max_bytes=MAX_RESPONSE_BYTES)
        usage = body.get("usage") or {}
        if isinstance(usage.get("input_tokens"), int) and isinstance(usage.get("output_tokens"), int):
            self.last_usage = {"input_tokens": usage["input_tokens"], "output_tokens": usage["output_tokens"]}
        self.last_debug = "stop_reason=" + str(body.get("stop_reason"))
        if body.get("stop_reason") not in ("end_turn", "stop_sequence"):
            raise ProviderModerationError("INVALID_MODEL_OUTPUT")
        texts = [part.get("text") for part in body.get("content") or [] if part.get("type") == "text"]
        if len(texts) != 1 or not isinstance(texts[0], str):
            raise ProviderModerationError("INVALID_MODEL_OUTPUT")
        self.last_debug += " text=" + texts[0][:300]
        return texts[0]

    def analyze(self, value: AnalysisInput) -> AnalysisResult:
        self.last_usage = None
        self.last_debug = None
        value = AnalysisInput.model_validate(value)
        redacted = redact_input(value.text)
        text = self._call(
            {
                "model": self._model,
                "max_tokens": MAX_OUTPUT_TOKENS,
                "system": self._system,
                "messages": [
                    {
                        "role": "user",
                        "content": json.dumps(redacted.model_dump(), ensure_ascii=False, separators=(",", ":")),
                    }
                ],
            }
        )
        return self._parse(text, redacted.local_findings.restricted_identifier)

    def _parse(self, text: str, restricted_identifier: bool) -> AnalysisResult:
        start = text.find("{")
        if start < 0:
            raise ProviderModerationError("INVALID_MODEL_OUTPUT")
        try:
            obj, _ = json.JSONDecoder().raw_decode(text[start:])
            raw = ClaudeOutput.model_validate(obj)
        except (ValueError, ValidationError):
            raise ProviderModerationError("INVALID_MODEL_OUTPUT") from None
        if raw.schema_version != "1" or raw.decision not in ("ALLOW", "BLOCK", "UNCERTAIN"):
            raise ProviderModerationError("INVALID_MODEL_OUTPUT")
        # policy_rule_ids are the model's working notes; the contract only needs
        # verdict + reason codes, so a rule/reason mismatch is not fatal.
        verdict, reasons = raw.decision, list(dict.fromkeys(raw.reason_codes))
        if any(reason not in REASONS for reason in reasons):
            raise ProviderModerationError("INVALID_MODEL_OUTPUT")
        if restricted_identifier:
            # A phone number, email or password was masked before the call.
            verdict = "BLOCK"
            reasons.append("PERSONAL_INFORMATION")
        if verdict == "ALLOW":
            reasons = []
        elif verdict == "BLOCK":
            reasons = [r for r in reasons if r not in UNCERTAIN_REASONS] or ["OTHER_UNSAFE"]
        else:
            reasons = [r for r in reasons if r in UNCERTAIN_REASONS][:1] or ["INSUFFICIENT_CONTEXT"]
        emotion = None
        if verdict == "ALLOW":
            if raw.emotion not in EMOTIONS:
                raise ProviderModerationError("INVALID_MODEL_OUTPUT")
            score = raw.emotion_score
            if score is not None and not 0 <= score <= 1:
                score = None
            emotion = EmotionResult(label=raw.emotion, score=score, source="MODEL")
        try:
            return AnalysisResult(
                model_bundle_version=self.model_bundle_version,
                inference_policy_version=self.inference_policy_version,
                safety=SafetyResult(verdict=verdict, score=None, reason_codes=sorted(set(reasons))),
                emotion=emotion,
            )
        except ValidationError:
            raise ProviderModerationError("INVALID_MODEL_OUTPUT") from None


def create_analyzer(context) -> ClaudeAnalyzer:
    """Loader factory. The loader already checked the manifest's pinned hashes."""
    manifest = context.manifest
    policy = _resource("moderation-prompt.txt")
    if hashlib.sha256(policy).hexdigest() != manifest["policy_prompt_sha256"]:
        raise ValueError("policy prompt changed; update claude_bundle/manifest.json after review")
    addendum = (context.manifest_path.parent / "emotion-addendum.txt").read_text(encoding="utf-8")
    return ClaudeAnalyzer(
        api_key=_read_key(),
        system_prompt=policy.decode("utf-8") + "\n" + addendum,
        model_bundle_version=manifest["model_bundle_version"],
        inference_policy_version=manifest["inference_policy_version"],
        model=manifest.get("model", DEFAULT_MODEL),
    )
