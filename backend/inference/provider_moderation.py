"""Strict moderation contracts shared by live and synthetic transports.

The mock recognizes reviewed synthetic fixtures only; an unknown sentence is
never implicitly permitted. Live HTTP is isolated in ``gemini_http``.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from importlib.resources import files
from pathlib import Path
from types import MappingProxyType
from typing import Literal, Protocol, Self

from pydantic import Field, ValidationError, model_validator

from inference.contracts import (
    AnalysisInput,
    AnalysisResult,
    ContractModel,
    EmotionResult,
    ReasonCode,
    SafetyResult,
)

PolicyRule = Literal["CP01", "CP02", "CP03", "CP04", "CP05", "CP06", "CP07", "CP08", "CP09", "CP10"]
MODEL_ID = "gemini-3.6-flash"
API_REVISION = "2026-05-20"
MAX_INPUT_TOKENS = 6144
MAX_REAL_INPUT_TOKENS = 16384
MAX_OUTPUT_TOKENS = 512
MAX_MODEL_JSON_BYTES = 2048
MAX_RESPONSE_BYTES = 65536
REDACTOR_VERSION = "restricted-identifiers-v1"
_RESOURCE_NAMES = ("moderation-prompt.txt", "moderation-response.schema.json", "moderation-cases.json")
_RULE_REASONS = {
    "CP01": "HARASSMENT",
    "CP02": "HATE",
    "CP03": "SEXUAL_CONTENT",
    "CP04": "THREAT",
    "CP05": "PERSONAL_INFORMATION",
    "CP06": "OTHER_UNSAFE",
    "CP07": "OTHER_UNSAFE",
    "CP08": "SELF_CRISIS",
    "CP09": "OTHER_UNSAFE",
}
_UNSAFE_REASONS = set(_RULE_REASONS.values()) - {"SELF_CRISIS"}


class ProviderModerationError(ValueError):
    """Safe fixed error codes; never put student input or wire bodies here."""

    def __init__(
        self,
        error_code: Literal["MODEL_NOT_READY", "INVALID_MODEL_OUTPUT", "TRANSIENT_DEPENDENCY", "INFERENCE_TIMEOUT"],
        *,
        failure_kind: Literal["AUTH", "INVALID_OUTPUT", "RATE_LIMIT", "UNAVAILABLE", "SAFETY", "TIMEOUT", "INPUT_LIMIT"]
        | None = None,
        retry_after_seconds: int | None = None,
        validated_usage: dict[str, int] | None = None,
    ):
        self.error_code = error_code
        self.failure_kind = (
            failure_kind
            or {
                "MODEL_NOT_READY": "AUTH",
                "INVALID_MODEL_OUTPUT": "INVALID_OUTPUT",
                "TRANSIENT_DEPENDENCY": "UNAVAILABLE",
                "INFERENCE_TIMEOUT": "TIMEOUT",
            }[error_code]
        )
        if retry_after_seconds is not None and (
            type(retry_after_seconds) is not int or not 0 <= retry_after_seconds <= 2147483647
        ):
            raise ValueError("invalid retry delay")
        self.retry_after_seconds = retry_after_seconds
        if validated_usage is not None and (
            not isinstance(validated_usage, dict)
            or set(validated_usage) != {"input_tokens", "output_tokens"}
            or any(type(value) is not int or not 0 <= value <= 2147483647 for value in validated_usage.values())
        ):
            raise ValueError("invalid validated usage metadata")
        # Numeric accounting only: no body, prompt, identifiers or credentials.
        self.validated_usage = MappingProxyType(dict(validated_usage)) if validated_usage is not None else None
        super().__init__(error_code)


class LocalFindings(ContractModel):
    restricted_identifier: bool = False
    confirmed_profanity: bool = False


class RedactedInput(ContractModel):
    schema_version: Literal["1"] = "1"
    text: str = Field(min_length=1)
    local_findings: LocalFindings


class ProviderModerationResult(ContractModel):
    schema_version: Literal["1"]
    decision: Literal["ALLOW", "BLOCK", "UNCERTAIN", "SUPPORT"]
    reason_codes: list[ReasonCode] = Field(max_length=5)
    policy_rule_ids: list[PolicyRule] = Field(max_length=5)

    @model_validator(mode="after")
    def validate_semantics(self) -> Self:
        reasons, rules = set(self.reason_codes), set(self.policy_rule_ids)
        if len(reasons) != len(self.reason_codes) or len(rules) != len(self.policy_rule_ids):
            raise ValueError("duplicate moderation code")
        if self.decision == "ALLOW":
            if reasons or rules:
                raise ValueError("ALLOW forbids reasons and policy rules")
        elif self.decision == "UNCERTAIN":
            if reasons not in ({"INSUFFICIENT_CONTEXT"}, {"OUT_OF_DOMAIN"}) or rules != {"CP10"}:
                raise ValueError("UNCERTAIN requires exactly one uncertainty reason and CP10")
        else:
            if not reasons or not rules or "CP10" in rules:
                raise ValueError("nonpublic result requires mapped rules")
            if reasons != {_RULE_REASONS[rule] for rule in rules}:
                raise ValueError("moderation reasons do not match policy rules")
            if self.decision == "SUPPORT":
                if "CP08" not in rules or "SELF_CRISIS" not in reasons:
                    raise ValueError("SUPPORT requires CP08 and SELF_CRISIS")
            elif "CP08" in rules or not reasons <= _UNSAFE_REASONS:
                raise ValueError("BLOCK forbids support and uncertainty reasons")
        return self


def _strict_json(value: str | bytes, *, max_bytes: int) -> dict:
    def pairs(items):
        result = {}
        for key, item in items:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = item
        return result

    try:
        if isinstance(value, bytes):
            if len(value) > max_bytes:
                raise ValueError("JSON size limit")
            value = value.decode("utf-8", errors="strict")
        if not isinstance(value, str) or len(value.encode("utf-8")) > max_bytes or value.startswith("\ufeff"):
            raise ValueError("invalid JSON encoding or size")
        result = json.loads(
            value,
            object_pairs_hook=pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite JSON number")),
        )
        if not isinstance(result, dict):
            raise ValueError("JSON object required")
        return result
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise ProviderModerationError("INVALID_MODEL_OUTPUT") from None


def parse_moderation_json(value: str | bytes) -> ProviderModerationResult:
    try:
        return ProviderModerationResult.model_validate(_strict_json(value, max_bytes=MAX_MODEL_JSON_BYTES))
    except ValidationError:
        raise ProviderModerationError("INVALID_MODEL_OUTPUT") from None


def interactions_envelope(value: bytes | str | dict) -> dict:
    """Decode a bounded wire envelope without preserving provider error text."""
    try:
        if isinstance(value, dict):
            value = json.dumps(value, ensure_ascii=False, allow_nan=False)
        return _strict_json(value, max_bytes=MAX_RESPONSE_BYTES)
    except ProviderModerationError:
        raise
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise ProviderModerationError("INVALID_MODEL_OUTPUT") from None


def parse_interactions_usage(
    value: dict,
    *,
    max_input_tokens: int = MAX_REAL_INPUT_TOKENS,
    max_output_tokens: int = MAX_OUTPUT_TOKENS,
    input_bound: int | None = None,
) -> dict[str, int]:
    """Validate text-only accounting. Output and thoughts are disjoint counts.

    The documented Interactions example has input=7, output=23, thoughts=49,
    total=79. Sum output+thoughts once and check that arithmetic before charging.
    Cached input is charged at the full input rate as a conservative local bound.
    """
    try:
        usage = value.get("usage")
        if not isinstance(usage, dict):
            raise ValueError("missing usage")
        required = {"total_input_tokens", "total_output_tokens", "total_tokens"}
        optional = {
            "initial_input_tokens",
            "total_cached_tokens",
            "total_tool_use_tokens",
            "total_thought_tokens",
            "input_tokens_by_modality",
            "output_tokens_by_modality",
            "cached_tokens_by_modality",
            "tool_use_tokens_by_modality",
            "grounding_tool_count",
        }
        if not required <= set(usage) or set(usage) - required - optional:
            raise ValueError("unexpected usage fields")
        scalar_names = required | {
            "initial_input_tokens",
            "total_cached_tokens",
            "total_tool_use_tokens",
            "total_thought_tokens",
        }
        for name in scalar_names & set(usage):
            if type(usage[name]) is not int or not 0 <= usage[name] <= 2147483647:
                raise ValueError("invalid token count")
        input_tokens, output_tokens, thoughts = (
            usage["total_input_tokens"],
            usage["total_output_tokens"],
            usage.get("total_thought_tokens", 0),
        )
        if input_tokens == 0 or usage["total_tokens"] != input_tokens + output_tokens + thoughts:
            raise ValueError("inconsistent usage totals")
        if usage.get("initial_input_tokens", input_tokens) != input_tokens:
            raise ValueError("multiple interaction turns are disabled")
        if usage.get("total_cached_tokens", 0) > input_tokens or usage.get("total_tool_use_tokens", 0):
            raise ValueError("invalid cached or tool usage")
        for name in ("input_tokens_by_modality", "output_tokens_by_modality", "cached_tokens_by_modality"):
            if name not in usage:
                continue
            parts = usage[name]
            if not isinstance(parts, list) or len(parts) > 1:
                raise ValueError("unexpected modality usage")
            for part in parts:
                if (
                    not isinstance(part, dict)
                    or set(part) != {"modality", "tokens"}
                    or part["modality"] != "text"
                    or type(part["tokens"]) is not int
                    or part["tokens"] < 0
                ):
                    raise ValueError("nontext usage")
            expected = {
                "input_tokens_by_modality": input_tokens,
                "output_tokens_by_modality": output_tokens,
                "cached_tokens_by_modality": usage.get("total_cached_tokens", 0),
            }[name]
            if parts and parts[0]["tokens"] != expected:
                raise ValueError("inconsistent modality count")
        for name in ("tool_use_tokens_by_modality", "grounding_tool_count"):
            if not isinstance(usage.get(name, []), list) or usage.get(name, []):
                raise ValueError("tools and grounding are disabled")
        validated_usage = {"input_tokens": input_tokens, "output_tokens": output_tokens + thoughts}
        if (
            input_tokens > max_input_tokens
            or output_tokens + thoughts > max_output_tokens
            or (input_bound is not None and input_tokens > input_bound)
        ):
            raise ProviderModerationError(
                "INVALID_MODEL_OUTPUT", failure_kind="INPUT_LIMIT", validated_usage=validated_usage
            )
        return validated_usage
    except ProviderModerationError:
        raise
    except (ValueError, TypeError, KeyError):
        raise ProviderModerationError("INVALID_MODEL_OUTPUT") from None


def parse_interactions_response(
    value: bytes | str | dict, *, expected_request: dict | None = None
) -> ProviderModerationResult:
    """Accept a completed, text-only Interactions response; never repair it."""
    try:
        response = interactions_envelope(value)
        allowed = {"id", "object", "model", "created", "updated", "status", "steps", "usage", "service_tier"}
        # Full Interaction resources can echo request metadata and server-added
        # generation defaults. These fields gain no authority over our policy.
        # Explicit storage/history/tool changes still fail closed.
        if expected_request is not None:
            for name in (
                "generation_config",
                "response_format",
                "system_instruction",
                "input",
                "safety_settings",
                "store",
                "background",
                "stream",
            ):
                if name in response:
                    if name in {"store", "background", "stream"} and response[name] is not False:
                        raise ValueError("unexpected privacy configuration")
                    allowed.add(name)
            for name in (
                "tools",
                "previous_interaction_id",
                "agent",
                "environment",
                "environment_id",
                "webhook_config",
            ):
                if name in response:
                    if response[name] not in (None, [], {}):
                        raise ValueError("unexpected tools, history or environment")
                    allowed.add(name)
        if set(response) - allowed or response.get("status") != "completed":
            raise ValueError("unexpected response envelope")
        if response.get("object", "interaction") != "interaction" or response.get("model", MODEL_ID) not in {
            MODEL_ID,
            "models/" + MODEL_ID,
        }:
            raise ValueError("unexpected response model")
        if response.get("service_tier", "standard") != "standard":
            raise ValueError("unexpected service tier")
        steps = response.get("steps")
        if not isinstance(steps, list) or not steps or len(steps) > 16:
            raise ValueError("missing or excessive response steps")
        output = None
        for index, step in enumerate(steps):
            if not isinstance(step, dict):
                raise ValueError("invalid step")
            kind = step.get("type")
            if kind == "user_input":
                if output is not None:
                    raise ValueError("input after output")
                continue  # Never read echoed student input as model instructions/output.
            if kind == "thought":
                if output is not None or set(step) - {"type", "signature", "summary"}:
                    raise ValueError("unexpected thought")
                if step.get("summary") not in (None, "", []):
                    raise ValueError("thought summaries are disabled")
                if "signature" in step and not isinstance(step["signature"], str):
                    raise ValueError("invalid thought signature")
                continue
            if kind != "model_output" or output is not None or index != len(steps) - 1:
                raise ValueError("unexpected output step")
            if set(step) != {"type", "content"}:
                raise ValueError("unexpected output fields")
            content = step["content"]
            if not isinstance(content, list) or len(content) != 1 or not isinstance(content[0], dict):
                raise ValueError("single text content required")
            part = content[0]
            if set(part) != {"type", "text"} or part["type"] != "text" or not isinstance(part["text"], str):
                raise ValueError("text content required")
            output = parse_moderation_json(part["text"])
        if output is None:
            raise ValueError("no output")
        if expected_request is not None:
            # The legacy decoder still understands persisted SUPPORT results.
            # New live responses must obey the policy schema actually sent;
            # never turn a retired decision (possibly with other violations)
            # into an ALLOW result or a new private-help receipt.
            properties = expected_request["response_format"]["schema"]["properties"]
            if (
                output.decision not in properties["decision"]["enum"]
                or any(reason not in properties["reason_codes"]["items"]["enum"] for reason in output.reason_codes)
                or any(rule not in properties["policy_rule_ids"]["items"]["enum"] for rule in output.policy_rule_ids)
            ):
                raise ValueError("response outside requested moderation policy")
        return output
    except ProviderModerationError:
        raise
    except (ValueError, TypeError, UnicodeError, RecursionError, KeyError):
        raise ProviderModerationError("INVALID_MODEL_OUTPUT") from None


# Intentionally narrow patterns. Names, ordinary numbers, aliases and concealed
# identifiers still require semantic moderation and are not claimed anonymous.
_PHONE = re.compile(
    r"(?<![0-9])(?:01[016789][ -]?[0-9]{3,4}[ -]?[0-9]{4}"
    r"|02[ -]?[0-9]{3,4}[ -]?[0-9]{4}"
    r"|0(?:3[1-3]|4[1-4]|5[1-5]|6[1-4])[ -]?[0-9]{3,4}[ -]?[0-9]{4})(?![0-9])"
)
_EMAIL = re.compile(r"(?<![A-Za-z0-9._%+\-])[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,63}")
_PASSWORD = re.compile(
    r"(?P<label>(?:비밀번호|비번|password|passwd)\s*(?:은|는|이|가)?\s*[:=]\s*)"
    r"(?P<value>[^\s,;]{1,128})",
    re.IGNORECASE,
)
_KOREAN_PASSWORD = re.compile(
    r"(?P<label>(?:비밀번호|비번)\s*(?:은|는)\s*)"
    r"(?P<value>[A-Za-z0-9!@#$%^&*()_+./?=:\-]*[A-Za-z0-9][A-Za-z0-9!@#$%^&*()_+./?=:\-]*)"
)


def redact_input(text: str, *, confirmed_profanity: bool = False) -> RedactedInput:
    """Create trusted flags in memory; no reverse map or matched value is kept."""
    if not isinstance(text, str) or not text or not isinstance(confirmed_profanity, bool):
        raise ValueError("invalid moderation input")
    text, phones = _PHONE.subn("[연락처]", text)
    text, emails = _EMAIL.subn("[이메일]", text)
    text, passwords = _PASSWORD.subn(lambda match: match["label"] + "[비밀정보]", text)
    text, korean_passwords = _KOREAN_PASSWORD.subn(lambda match: match["label"] + "[비밀정보]", text)
    return RedactedInput(
        text=text,
        local_findings=LocalFindings(
            restricted_identifier=bool(phones or emails or passwords or korean_passwords),
            confirmed_profanity=confirmed_profanity,
        ),
    )


def _resource(name: str) -> bytes:
    return files("inference").joinpath("resources", name).read_bytes()


def _compiler_parameters() -> dict:
    return {
        "model": MODEL_ID,
        "generation_config": {
            "max_output_tokens": MAX_OUTPUT_TOKENS,
            "thinking_level": "minimal",
            "thinking_summaries": "none",
        },
        "safety_settings": [
            # Moderation must be able to classify ordinary slang without an
            # extra medium-probability veto. The application policy still runs
            # on every input; this is not an ALLOW fallback or a per-text retry.
            {"type": category, "threshold": "block_only_high", "method": "probability"}
            for category in ("hate_speech", "dangerous_content", "harassment", "sexually_explicit", "jailbreak")
        ],
        "store": False,
        "background": False,
        "stream": False,
        "service_tier": "standard",
    }


def compile_request(
    value: RedactedInput,
    *,
    _assets: tuple[bytes, bytes] | None = None,
    _mock: bool = True,
    _max_output_tokens: int = MAX_OUTPUT_TOKENS,
) -> dict:
    """Return a JSON-ready request without credentials, IDs or remote effects."""
    value = RedactedInput.model_validate(value)
    prompt_bytes, schema_bytes = _assets or (
        _resource("moderation-prompt.txt"),
        _resource("moderation-response.schema.json"),
    )
    request = _compiler_parameters()
    if type(_max_output_tokens) is not int or not 1 <= _max_output_tokens <= MAX_OUTPUT_TOKENS:
        raise ProviderModerationError("MODEL_NOT_READY")
    request["generation_config"]["max_output_tokens"] = _max_output_tokens
    request["system_instruction"] = prompt_bytes.decode("utf-8")
    request["input"] = json.dumps(value.model_dump(), ensure_ascii=False, separators=(",", ":"))
    request["response_format"] = {
        "type": "text",
        "mime_type": "application/json",
        "schema": json.loads(schema_bytes),
    }
    # Synthetic mock accounting only. A real transport must supply validated
    # provider counting rather than reuse this estimate for financial admission.
    if _mock and estimate_input_tokens(request) > MAX_INPUT_TOKENS:
        raise ProviderModerationError("MODEL_NOT_READY")
    return request


def estimate_input_tokens(request: dict) -> int:
    """Synthetic mock accounting unit; actual provider usage is unmeasured."""
    text = request["system_instruction"] + request["input"] + json.dumps(request["response_format"], ensure_ascii=False)
    # Mock accounting uses a fixed synthetic ratio for reproducible quota tests;
    # Real dispatch uses provider preflight and validated actual usage instead.
    return (len(text.encode("utf-8")) + 3) // 4


def compute_release_id(
    *,
    inference_policy_version: str = "inference-v1",
    lexicon_version: str = "lexicon-v1",
    runtime_profile: dict | None = None,
) -> str:
    """Hash only immutable inputs; derived release/version fields are excluded."""
    manifest = {
        "format": "gemini-offline-release-v1",
        "assets": {name: hashlib.sha256(_resource(name)).hexdigest() for name in _RESOURCE_NAMES},
        "adapter_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "contracts_sha256": hashlib.sha256(Path(__file__).with_name("contracts.py").read_bytes()).hexdigest(),
        "http_transport_sha256": hashlib.sha256(Path(__file__).with_name("gemini_http.py").read_bytes()).hexdigest(),
        "http_client_version": "httpx-0.28.1",
        "http_core_version": "httpcore-1.0.9",
        "compiler": _compiler_parameters(),
        "api_revision": API_REVISION,
        "api_path": "/v1beta/interactions",
        "redactor_version": REDACTOR_VERSION,
        "inference_policy_version": inference_policy_version,
        "lexicon_version": lexicon_version,
        "runtime_profile": {
            key: value
            for key, value in (runtime_profile or {}).items()
            if key not in {"model_bundle_version", "moderation_policy_version", "release_id"}
        },
    }
    encoded = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    prefix = "gemini-" if (runtime_profile or {}).get("moderation_profile") == "gemini" else "gemini-mock-"
    return prefix + hashlib.sha256(encoded).hexdigest()


def merge_local_findings(result: ProviderModerationResult, findings: LocalFindings) -> ProviderModerationResult:
    """Preserve SUPPORT and trusted prohibitions within the five-rule limit."""
    result = ProviderModerationResult.model_validate(result)
    findings = LocalFindings.model_validate(findings)
    rules = set(result.policy_rule_ids) - {"CP10"}
    if findings.restricted_identifier:
        rules.add("CP05")
    if findings.confirmed_profanity:
        rules.add("CP06")
    if not findings.restricted_identifier and not findings.confirmed_profanity:
        return result
    priority = ["CP08"]
    if findings.restricted_identifier:
        priority.append("CP05")
    if findings.confirmed_profanity:
        priority.append("CP06")
    priority += ["CP04", "CP03", "CP02", "CP07", "CP09", "CP01", "CP05", "CP06"]
    ordered = list(dict.fromkeys(priority))
    selected = sorted([rule for rule in ordered if rule in rules][:5])
    return ProviderModerationResult(
        schema_version="1",
        decision="SUPPORT" if result.decision == "SUPPORT" else "BLOCK",
        policy_rule_ids=selected,
        reason_codes=sorted({_RULE_REASONS[rule] for rule in selected}),
    )


class ModerationTransport(Protocol):
    def respond(self, request: dict) -> bytes | str | dict: ...


class FixtureModerationTransport:
    """Exact-match public synthetic examples, with a fail-closed unknown case."""

    def __init__(self):
        self._cases = {}
        for case in json.loads(_resource("moderation-cases.json")):
            expected = ProviderModerationResult.model_validate(case["expected"])
            # The dataset contains examples that are already masked. Generate
            # redacted input only for real patterns; typed placeholders never
            # independently create a trusted flag.
            key = (case["text"], case["local_findings"]["confirmed_profanity"])
            if key in self._cases:
                raise ValueError("duplicate synthetic fixture")
            self._cases[key] = expected

    def respond(self, request: dict) -> dict:
        value = RedactedInput.model_validate_json(request["input"])
        result = self._cases.get((value.text, value.local_findings.confirmed_profanity))
        if result is None:
            result = ProviderModerationResult(
                schema_version="1",
                decision="UNCERTAIN",
                reason_codes=["INSUFFICIENT_CONTEXT"],
                policy_rule_ids=["CP10"],
            )
        return {
            "object": "interaction",
            "model": MODEL_ID,
            "status": "completed",
            "steps": [{"type": "model_output", "content": [{"type": "text", "text": result.model_dump_json()}]}],
        }


class UnavailableModerationTransport:
    """Explicit unavailable boundary for exercising a nonpublic dependency failure."""

    def respond(self, request: dict) -> bytes:
        raise ProviderModerationError("MODEL_NOT_READY")


class GeminiModerationAnalyzer:
    moderation_profile = "gemini_mock"

    def __init__(
        self,
        model_version: str,
        policy_version: str,
        *,
        transport: ModerationTransport | None = None,
        emotion_predictor: Callable[[AnalysisInput], EmotionResult] | None = None,
        moderation_profile: Literal["gemini_mock", "gemini"] = "gemini_mock",
        max_input_tokens: int = MAX_INPUT_TOKENS,
        max_output_tokens: int = MAX_OUTPUT_TOKENS,
    ):
        if moderation_profile == "gemini" and transport is None:
            raise ProviderModerationError("MODEL_NOT_READY")
        self.moderation_profile = moderation_profile
        self.model_bundle_version = model_version
        self.inference_policy_version = policy_version
        self._transport = transport if transport is not None else FixtureModerationTransport()
        self._emotion_predictor = emotion_predictor
        self._max_input_tokens, self._max_output_tokens = max_input_tokens, max_output_tokens
        self._assets = (_resource("moderation-prompt.txt"), _resource("moderation-response.schema.json"))
        self.last_usage: dict[str, int] | None = None
        self.last_stage: str | None = None

    def analyze(self, value: AnalysisInput) -> AnalysisResult:
        return self.analyze_moderated(value)

    def analyze_moderated(self, value: AnalysisInput, *, confirmed_profanity: bool = False) -> AnalysisResult:
        self.last_usage = None
        self.last_stage = "MODERATION"
        value = AnalysisInput.model_validate(value)
        redacted = redact_input(value.text, confirmed_profanity=confirmed_profanity)
        request = compile_request(
            redacted,
            _assets=self._assets,
            _mock=self.moderation_profile == "gemini_mock",
            _max_output_tokens=self._max_output_tokens,
        )
        try:
            raw = self._transport.respond(request)
        except ProviderModerationError:
            raise
        except TimeoutError:
            raise ProviderModerationError("INFERENCE_TIMEOUT") from None
        except Exception:
            raise ProviderModerationError("TRANSIENT_DEPENDENCY") from None
        if self.moderation_profile == "gemini":
            envelope = interactions_envelope(raw)
            try:
                self.last_usage = parse_interactions_usage(
                    envelope,
                    max_input_tokens=self._max_input_tokens,
                    max_output_tokens=self._max_output_tokens,
                    input_bound=getattr(self._transport, "last_input_bound", None),
                )
            except ProviderModerationError as error:
                # A valid measured overrun was already incurred. Preserve the
                # actual amount for settlement while refusing publication.
                if error.validated_usage is not None:
                    self.last_usage = dict(error.validated_usage)
                raise
            result = merge_local_findings(
                parse_interactions_response(envelope, expected_request=request), redacted.local_findings
            )
        else:
            result = merge_local_findings(parse_interactions_response(raw), redacted.local_findings)
        emotion = None
        if result.decision == "ALLOW":
            emotion = EmotionResult(label="NEUTRAL", source="FALLBACK", score=None)
            if self._emotion_predictor is not None:
                self.last_stage = "EMOTION"
                try:
                    emotion = EmotionResult.model_validate(self._emotion_predictor(value))
                except Exception:
                    pass  # Emotion failure cannot change safety or make content public.
        if self.moderation_profile == "gemini_mock":
            self.last_usage = {"input_tokens": estimate_input_tokens(request), "output_tokens": 128}
        analysis = AnalysisResult(
            model_bundle_version=self.model_bundle_version,
            inference_policy_version=self.inference_policy_version,
            safety=SafetyResult(
                verdict="BLOCK" if result.decision == "SUPPORT" else result.decision,
                score=None,
                reason_codes=result.reason_codes,
            ),
            emotion=emotion,
        )
        self.last_stage = "COMPLETE"
        return analysis
