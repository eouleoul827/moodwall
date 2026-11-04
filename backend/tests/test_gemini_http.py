"""Live adapter contracts using local in-memory HTTP only; never real keys/API."""

import asyncio
import copy
import json
import time
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import httpx
import pytest
from pydantic import SecretStr

from inference.contracts import AnalysisInput
from inference.gemini_http import (
    COUNT_FRAMING_MARGIN,
    COUNT_TOKENS_PATH,
    INTERACTIONS_PATH,
    ORIGIN,
    GeminiHTTPTransport,
    _retry_after,
)
from inference.provider_moderation import (
    API_REVISION,
    MODEL_ID,
    GeminiModerationAnalyzer,
    ProviderModerationError,
    compile_request,
    parse_interactions_usage,
    redact_input,
)

SYNTHETIC_KEY = "synthetic-local-test-key-01234567890123456789"
ALLOW = {"schema_version": "1", "decision": "ALLOW", "reason_codes": [], "policy_rule_ids": []}


class Body(httpx.AsyncByteStream):
    def __init__(self, chunks, *, delay=0):
        self.chunks, self.delay, self.closed = chunks, delay, False

    async def __aiter__(self):
        for chunk in self.chunks:
            if self.delay:
                await asyncio.sleep(self.delay)
            yield chunk

    async def aclose(self):
        self.closed = True


def response(status=200, payload=None, *, body=None, headers=None):
    body = body or Body([json.dumps(payload, ensure_ascii=False).encode()])
    return httpx.Response(status, stream=body, headers={"Content-Type": "application/json", **(headers or {})})


def envelope(*, result=None, input_tokens=100, output_tokens=23, thoughts=49):
    return {
        "id": "synthetic-interaction",
        "object": "interaction",
        "model": MODEL_ID,
        "status": "completed",
        "service_tier": "standard",
        "steps": [
            {"type": "thought", "signature": "synthetic-opaque"},
            {
                "type": "model_output",
                "content": [{"type": "text", "text": json.dumps(result or ALLOW)}],
            },
        ],
        "usage": {
            "total_input_tokens": input_tokens,
            "total_output_tokens": output_tokens,
            "total_thought_tokens": thoughts,
            "total_tokens": input_tokens + output_tokens + thoughts,
            "total_cached_tokens": 0,
            "total_tool_use_tokens": 0,
        },
    }


def transport_for(handler, **kwargs):
    return GeminiHTTPTransport(SecretStr(SYNTHETIC_KEY), _transport=httpx.MockTransport(handler), **kwargs)


def analyzer_for(transport):
    return GeminiModerationAnalyzer(
        "synthetic-live-release",
        "synthetic-policy",
        transport=transport,
        moderation_profile="gemini",
        max_input_tokens=16384,
    )


def test_fixed_https_preflight_then_generation_with_real_usage_and_no_retention():
    seen = []

    def handler(request):
        seen.append(request)
        if request.url.path == COUNT_TOKENS_PATH:
            return response(payload={"totalTokens": 100})
        return response(payload=envelope())

    live = transport_for(handler)
    analyzer = analyzer_for(live)
    result = analyzer.analyze(AnalysisInput(text="오늘 너무 우울해"))
    assert result.safety.verdict == "ALLOW" and result.emotion.source == "FALLBACK"
    assert analyzer.last_usage == {"input_tokens": 100, "output_tokens": 72}
    assert live.last_input_bound == 100 + COUNT_FRAMING_MARGIN
    assert [item.url.path for item in seen] == [COUNT_TOKENS_PATH, INTERACTIONS_PATH]
    for item in seen:
        assert str(item.url).startswith(ORIGIN + "/v1beta/")
        assert not item.url.query and item.headers["x-goog-api-key"] == SYNTHETIC_KEY
        assert item.headers["Api-Revision"] == API_REVISION
        assert item.headers["Accept-Encoding"] == "identity"
    counted = json.loads(seen[0].content)["generateContentRequest"]
    generated = json.loads(seen[1].content)
    assert counted["model"] == "models/" + MODEL_ID
    assert counted["systemInstruction"]["parts"][0]["text"] == generated["system_instruction"]
    assert len(counted["contents"][0]["parts"]) == 2
    assert json.loads(counted["contents"][0]["parts"][1]["text"]) == generated["response_format"]["schema"]
    assert generated["store"] is generated["background"] is generated["stream"] is False
    assert generated["service_tier"] == "standard" and "tools" not in generated
    assert SYNTHETIC_KEY not in repr(live)


@pytest.mark.parametrize("change", ["store", "tools", "history", "model", "prompt", "schema", "unmasked_identifier"])
def test_transport_rejects_unreviewed_or_privacy_breaking_requests_before_network(change):
    calls = []
    live = transport_for(lambda request: calls.append(request))
    request = compile_request(redact_input("오늘 너무 우울해"))
    if change == "store":
        request["store"] = True
    elif change == "tools":
        request["tools"] = [{"type": "google_search"}]
    elif change == "history":
        request["previous_interaction_id"] = "previous-private-input"
    elif change == "model":
        request["model"] = "gemini-flash-latest"
    elif change == "prompt":
        request["system_instruction"] = "unreviewed policy"
    elif change == "schema":
        request["response_format"]["schema"] = {"type": "string"}
    else:
        value = json.loads(request["input"])
        value["text"] = "비번은1234야"
        request["input"] = json.dumps(value)
    with pytest.raises(ProviderModerationError, match="MODEL_NOT_READY"):
        live.respond(request)
    assert not calls


def test_redaction_is_applied_to_both_remote_requests_and_local_flags_still_block():
    seen = []

    def handler(request):
        seen.append(request.content.decode())
        return response(payload={"totalTokens": 100} if request.url.path == COUNT_TOKENS_PATH else envelope())

    result = analyzer_for(transport_for(handler)).analyze(AnalysisInput(text="010-1234-5678로 전화해"))
    assert all("010-1234-5678" not in item for item in seen)
    assert result.safety.verdict == "BLOCK" and result.safety.reason_codes == ["PERSONAL_INFORMATION"]


@pytest.mark.parametrize("count", [-1, True, "100", 0, 16384])
def test_bad_or_excessive_preflight_never_generates(count):
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return response(payload={"totalTokens": count})

    with pytest.raises(ProviderModerationError):
        analyzer_for(transport_for(handler)).analyze(AnalysisInput(text="오늘 너무 우울해"))
    assert calls == [COUNT_TOKENS_PATH]


@pytest.mark.parametrize(
    "status,kind",
    [(401, "AUTH"), (403, "AUTH"), (404, "AUTH"), (302, "AUTH"), (503, "UNAVAILABLE"), (400, "INVALID_OUTPUT")],
)
def test_http_errors_have_safe_typed_failures_and_no_retries(status, kind):
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return response(
            status,
            {"error": {"code": "invalid_request", "message": "private " + SYNTHETIC_KEY}},
            headers={"Location": "https://evil.invalid"},
        )

    with pytest.raises(ProviderModerationError) as caught:
        analyzer_for(transport_for(handler)).analyze(AnalysisInput(text="오늘 너무 우울해"))
    assert caught.value.failure_kind == kind
    assert len(calls) == 1 and SYNTHETIC_KEY not in repr(caught.value)


def test_safety_error_is_distinct_from_schema_failure_without_retaining_body():
    live = transport_for(lambda _: response(400, {"error": {"code": "safety", "message": "private body"}}))
    with pytest.raises(ProviderModerationError) as caught:
        live.respond(compile_request(redact_input("오늘 너무 우울해")))
    assert caught.value.failure_kind == "SAFETY"
    assert "private body" not in str(caught.value)


def test_429_preserves_retry_after_without_retrying_or_sending_generation():
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return response(429, {}, headers={"Retry-After": "7200"})

    with pytest.raises(ProviderModerationError) as caught:
        analyzer_for(transport_for(handler)).analyze(AnalysisInput(text="오늘 너무 우울해"))
    assert caught.value.failure_kind == "RATE_LIMIT" and caught.value.retry_after_seconds == 7200
    assert calls == [COUNT_TOKENS_PATH]
    future = format_datetime(datetime.now(UTC) + timedelta(seconds=90), usegmt=True)
    assert 89 <= _retry_after(future) <= 91
    with pytest.raises(ProviderModerationError):
        _retry_after("99999999999999999999")


@pytest.mark.parametrize("mode", ["declared_big", "stream_big", "compressed", "html", "duplicate_json"])
def test_bounded_identity_json_response_only(mode):
    def handler(_request):
        if mode == "declared_big":
            return response(payload={}, headers={"Content-Length": "999999"})
        if mode == "stream_big":
            return response(body=Body([b"x" * 4096] * 17))
        if mode == "compressed":
            return response(payload={}, headers={"Content-Encoding": "gzip"})
        if mode == "html":
            return response(payload={}, headers={"Content-Type": "text/html"})
        return response(body=Body([b'{"totalTokens":1,"totalTokens":100}']))

    with pytest.raises(ProviderModerationError):
        analyzer_for(transport_for(handler)).analyze(AnalysisInput(text="오늘 너무 우울해"))


def test_absolute_deadline_covers_combined_preflight_and_generation():
    calls, streams = [], []

    def handler(request):
        calls.append(request.url.path)
        payload = {"totalTokens": 100} if request.url.path == COUNT_TOKENS_PATH else envelope()
        body = Body([json.dumps(payload).encode()], delay=0.13)
        streams.append(body)
        return response(body=body)

    start = time.monotonic()
    with pytest.raises(ProviderModerationError) as caught:
        analyzer_for(transport_for(handler, timeout_seconds=0.2)).analyze(AnalysisInput(text="오늘 너무 우울해"))
    assert caught.value.failure_kind == "TIMEOUT"
    assert time.monotonic() - start < 0.7
    assert calls == [COUNT_TOKENS_PATH, INTERACTIONS_PATH] and all(item.closed for item in streams)


def test_connection_errors_never_expose_key_or_request_body():
    def handler(request):
        raise httpx.ConnectError("private diagnostic " + SYNTHETIC_KEY, request=request)

    with pytest.raises(ProviderModerationError) as caught:
        analyzer_for(transport_for(handler)).analyze(AnalysisInput(text="오늘 너무 우울해"))
    assert caught.value.failure_kind == "UNAVAILABLE"
    assert caught.value.__cause__ is None and SYNTHETIC_KEY not in repr(caught.value)


@pytest.mark.parametrize(
    "mutation",
    [
        {"total_input_tokens": True},
        {"total_output_tokens": -1},
        {"total_tokens": 999},
        {"total_thought_tokens": "49"},
        {"total_tool_use_tokens": 1},
        {"total_cached_tokens": 101},
        {"initial_input_tokens": 90},
        {"grounding_tool_count": [{"type": "google_search", "count": 1}]},
        {"input_tokens_by_modality": [{"modality": "image", "tokens": 100}]},
    ],
)
def test_actual_usage_must_be_consistent_text_only_and_integer(mutation):
    value = envelope()
    value["usage"].update(mutation)
    with pytest.raises(ProviderModerationError):
        parse_interactions_usage(value)


def test_omitted_zero_thought_count_is_accepted_only_when_totals_prove_zero():
    value = envelope(thoughts=0)
    del value["usage"]["total_thought_tokens"]
    assert parse_interactions_usage(value) == {"input_tokens": 100, "output_tokens": 23}
    value["usage"]["total_tokens"] += 1
    with pytest.raises(ProviderModerationError):
        parse_interactions_usage(value)


@pytest.mark.parametrize("input_tokens,output,thoughts", [(16385, 1, 1), (100, 500, 13), (1200, 23, 49)])
def test_actual_usage_over_budget_or_preflight_bound_fails_closed(input_tokens, output, thoughts):
    def handler(request):
        return response(
            payload={"totalTokens": 100}
            if request.url.path == COUNT_TOKENS_PATH
            else envelope(input_tokens=input_tokens, output_tokens=output, thoughts=thoughts)
        )

    analyzer = analyzer_for(transport_for(handler))
    with pytest.raises(ProviderModerationError) as caught:
        analyzer.analyze(AnalysisInput(text="오늘 너무 우울해"))
    measured = {"input_tokens": input_tokens, "output_tokens": output + thoughts}
    assert caught.value.failure_kind == "INPUT_LIMIT" and analyzer.last_usage == measured
    assert dict(caught.value.validated_usage) == measured
    with pytest.raises(TypeError):
        caught.value.validated_usage["input_tokens"] = 0


def test_invalid_usage_never_attaches_trusted_accounting_metadata():
    value = envelope()
    value["usage"]["total_input_tokens"] = "20000"
    with pytest.raises(ProviderModerationError) as caught:
        parse_interactions_usage(value)
    assert caught.value.validated_usage is None


def test_usage_is_retained_for_billing_when_completed_policy_json_is_invalid():
    def handler(request):
        value = envelope()
        value["steps"][-1]["content"][0]["text"] = "unstructured ALLOW"
        return response(payload={"totalTokens": 100} if request.url.path == COUNT_TOKENS_PATH else value)

    analyzer = analyzer_for(transport_for(handler))
    with pytest.raises(ProviderModerationError):
        analyzer.analyze(AnalysisInput(text="오늘 너무 우울해"))
    assert analyzer.last_usage == {"input_tokens": 100, "output_tokens": 72}


def test_documented_echoed_metadata_does_not_change_policy_but_privacy_mutation_fails():
    mutate_store = [False]

    def handler(request):
        if request.url.path == COUNT_TOKENS_PATH:
            return response(payload={"totalTokens": 100})
        submitted = json.loads(request.content)
        value = envelope()
        for name in ("system_instruction", "input", "response_format", "safety_settings", "background", "store"):
            value[name] = submitted[name]
        value["generation_config"] = {**submitted["generation_config"], "temperature": 1, "seed": 12}
        value["tools"] = []
        value["previous_interaction_id"] = None
        if mutate_store[0]:
            value["store"] = True
        return response(payload=value)

    analyzer = analyzer_for(transport_for(handler))
    assert analyzer.analyze(AnalysisInput(text="오늘 너무 우울해")).safety.verdict == "ALLOW"
    mutate_store[0] = True
    with pytest.raises(ProviderModerationError):
        analyzer.analyze(AnalysisInput(text="오늘 너무 우울해"))


def test_counter_namespace_and_no_mutation_of_generation_schema():
    request = compile_request(redact_input("오늘 너무 우울해"))
    original = copy.deepcopy(request)
    calls = []

    def handler(value):
        calls.append(value)
        return response(payload={"totalTokens": 100})

    assert transport_for(handler).count_input_tokens(request) == 1124
    assert request == original and len(calls) == 1


def test_loader_builds_live_analyzer_without_hidden_remote_warmup(monkeypatch):
    from app.config import Settings
    from inference.loader import load_analyzer

    monkeypatch.setattr(GeminiHTTPTransport, "respond", lambda *_: pytest.fail("loader must never call HTTP"))
    settings = Settings(
        _env_file=None,
        database_url="postgresql://postgres@localhost/moodwall_test",
        public_origin="https://moodwall.test",
        environment="test",
        secret_key="synthetic-live-session-key-01234567890123",
        qr_key="synthetic-live-qr-key-01234567890123456789",
        moderation_profile="gemini",
        gemini_api_key=SecretStr(SYNTHETIC_KEY),
    )
    analyzer = load_analyzer(settings)
    assert analyzer.moderation_profile == "gemini" and analyzer.last_usage is None
    assert settings.model_bundle_version.startswith("gemini-")
    assert not settings.model_bundle_version.startswith("gemini-mock-")
