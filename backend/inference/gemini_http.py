"""Bounded Gemini HTTP: one token preflight and one generation, never retries.

Only the worker constructs this transport. Credentials live in one private
SecretStr and are sent exclusively in a request header to a fixed HTTPS origin.
The public synchronous method owns an async client so asyncio.timeout bounds
the complete two-request exchange, including slow response-body delivery.
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import httpx
from pydantic import SecretStr

from inference.provider_moderation import (
    API_REVISION,
    MAX_REAL_INPUT_TOKENS,
    MAX_RESPONSE_BYTES,
    MODEL_ID,
    ProviderModerationError,
    RedactedInput,
    _compiler_parameters,
    _resource,
    _strict_json,
    redact_input,
)

ORIGIN = "https://generativelanguage.googleapis.com"
INTERACTIONS_PATH = "/v1beta/interactions"
COUNT_TOKENS_PATH = f"/v1beta/models/{MODEL_ID}:countTokens"
COUNT_FRAMING_MARGIN = 1024
_BLOCKED_CODES = {
    "safety",
    "recitation",
    "language",
    "prohibited_content",
    "spii",
    "blocklist",
    "image_safety",
    "image_prohibited_content",
    "image_recitation",
    "image_other",
    "content_blocked",
}


def _retry_after(value: str | None) -> int | None:
    if value is None:
        return None
    value = value.strip()
    try:
        if value.isascii() and value.isdecimal():
            if len(value) > 10:
                raise ProviderModerationError("MODEL_NOT_READY", failure_kind="AUTH")
            seconds = int(value)
        else:
            target = parsedate_to_datetime(value)
            if target.tzinfo is None:
                raise ValueError("missing date zone")
            seconds = max(0, int((target - datetime.now(UTC)).total_seconds()) + 1)
        if seconds > 2147483647:
            # An unrepresentable cooldown must not silently become a short retry.
            raise ProviderModerationError("MODEL_NOT_READY", failure_kind="AUTH")
        return seconds
    except ProviderModerationError:
        raise
    except (ValueError, TypeError, OverflowError):
        return None


def count_tokens_request(request: dict) -> dict:
    """Map model inputs to the supported CountTokens generateContentRequest.

    Schema is also supplied as a plain text part solely for conservative counting:
    the counter does not promise to include generationConfig schema in its count.
    Generation receives the original request, without this duplicated text part.
    """
    try:
        return {
            "generateContentRequest": {
                "model": "models/" + MODEL_ID,
                "systemInstruction": {"parts": [{"text": request["system_instruction"]}]},
                "contents": [
                    {
                        "role": "user",
                        "parts": [
                            {"text": request["input"]},
                            {
                                "text": json.dumps(
                                    request["response_format"]["schema"], ensure_ascii=False, separators=(",", ":")
                                )
                            },
                        ],
                    }
                ],
                "generationConfig": {
                    "responseMimeType": "application/json",
                    "responseJsonSchema": request["response_format"]["schema"],
                },
            }
        }
    except (KeyError, TypeError, ValueError):
        raise ProviderModerationError("MODEL_NOT_READY", failure_kind="AUTH") from None


class GeminiHTTPTransport:
    """No mutable URL, proxies, redirects, SDK retries, tools or conversation."""

    def __init__(
        self,
        api_key: SecretStr,
        *,
        timeout_seconds: float = 8,
        connect_timeout_seconds: float = 2,
        max_input_tokens: int = MAX_REAL_INPUT_TOKENS,
        _transport: httpx.AsyncBaseTransport | None = None,
    ):
        if (
            not isinstance(api_key, SecretStr)
            or not re.fullmatch(r"[A-Za-z0-9_\-]{20,256}", api_key.get_secret_value())
            or not 0 < timeout_seconds <= 8
            or not 0 < connect_timeout_seconds <= 2
            or not 1 <= max_input_tokens <= MAX_REAL_INPUT_TOKENS
        ):
            raise ProviderModerationError("MODEL_NOT_READY", failure_kind="AUTH")
        self.__api_key = api_key
        self.timeout_seconds, self.connect_timeout_seconds = timeout_seconds, connect_timeout_seconds
        self.max_input_tokens = max_input_tokens
        self._transport = _transport
        self._prompt = _resource("moderation-prompt.txt").decode("utf-8")
        self._schema = json.loads(_resource("moderation-response.schema.json"))
        self.last_input_bound: int | None = None

    def __repr__(self) -> str:
        return "GeminiHTTPTransport(api_key=SecretStr('**********'), endpoint='fixed Gemini HTTPS')"

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout_seconds, connect=self.connect_timeout_seconds, pool=2),
            limits=httpx.Limits(max_connections=1, max_keepalive_connections=1),
            follow_redirects=False,
            trust_env=False,
            http2=False,
            transport=self._transport or httpx.AsyncHTTPTransport(retries=0, trust_env=False),
            headers={
                "x-goog-api-key": self.__api_key.get_secret_value(),
                "Api-Revision": API_REVISION,
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Accept-Encoding": "identity",
            },
        )

    async def _post(self, client: httpx.AsyncClient, path: str, payload: dict) -> bytes:
        if path not in {COUNT_TOKENS_PATH, INTERACTIONS_PATH}:
            raise ProviderModerationError("MODEL_NOT_READY", failure_kind="AUTH")
        async with client.stream("POST", ORIGIN + path, json=payload) as response:
            status = response.status_code
            if status == 429:
                raise ProviderModerationError(
                    "TRANSIENT_DEPENDENCY",
                    failure_kind="RATE_LIMIT",
                    retry_after_seconds=_retry_after(response.headers.get("retry-after")),
                )
            if status in {401, 403, 404} or 300 <= status < 400:
                raise ProviderModerationError("MODEL_NOT_READY", failure_kind="AUTH")
            if status >= 500:
                raise ProviderModerationError("TRANSIENT_DEPENDENCY", failure_kind="UNAVAILABLE")
            if response.headers.get("content-encoding", "identity").lower() not in {"", "identity"}:
                raise ProviderModerationError("INVALID_MODEL_OUTPUT")
            if response.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
                raise ProviderModerationError("INVALID_MODEL_OUTPUT")
            length = response.headers.get("content-length")
            if length is not None and (
                not length.isascii() or not length.isdecimal() or int(length) > MAX_RESPONSE_BYTES
            ):
                raise ProviderModerationError("INVALID_MODEL_OUTPUT")
            chunks, total = [], 0
            async for chunk in response.aiter_raw(chunk_size=4096):
                total += len(chunk)
                if total > MAX_RESPONSE_BYTES:
                    raise ProviderModerationError("INVALID_MODEL_OUTPUT")
                chunks.append(chunk)
            body = b"".join(chunks)
            if status != 200:
                # Whitelisted machine error codes are safe to inspect. Never
                # retain or propagate provider messages, which can quote input.
                parsed = _strict_json(body, max_bytes=MAX_RESPONSE_BYTES)
                error = parsed.get("error")
                code = error.get("code") if isinstance(error, dict) else None
                if code in _BLOCKED_CODES:
                    raise ProviderModerationError("TRANSIENT_DEPENDENCY", failure_kind="SAFETY")
                raise ProviderModerationError("INVALID_MODEL_OUTPUT", failure_kind="INVALID_OUTPUT")
            return body

    def _validate_request(self, request: dict) -> None:
        try:
            expected = _compiler_parameters()
            if not isinstance(request, dict) or set(request) != set(expected) | {
                "system_instruction",
                "input",
                "response_format",
            }:
                raise ValueError("unexpected request fields")
            generation = request["generation_config"]
            max_output = generation["max_output_tokens"]
            if type(max_output) is not int or not 1 <= max_output <= 512:
                raise ValueError("invalid generation cap")
            expected["generation_config"]["max_output_tokens"] = max_output
            if any(request[name] != value for name, value in expected.items()):
                raise ValueError("unexpected compiler settings")
            if request["system_instruction"] != self._prompt or request["response_format"] != {
                "type": "text",
                "mime_type": "application/json",
                "schema": self._schema,
            }:
                raise ValueError("unreviewed assets")
            value = RedactedInput.model_validate(_strict_json(request["input"], max_bytes=4096))
            if (
                redact_input(value.text, confirmed_profanity=value.local_findings.confirmed_profanity).text
                != value.text
            ):
                raise ValueError("unmasked restricted identifier")
        except Exception:
            raise ProviderModerationError("MODEL_NOT_READY", failure_kind="AUTH") from None

    async def _count_input_tokens(self, client: httpx.AsyncClient, request: dict) -> int:
        body = await self._post(client, COUNT_TOKENS_PATH, count_tokens_request(request))
        response = _strict_json(body, max_bytes=MAX_RESPONSE_BYTES)
        allowed = {"totalTokens", "cachedContentTokenCount", "promptTokensDetails", "cacheTokensDetails"}
        count = response.get("totalTokens")
        if set(response) - allowed or type(count) is not int or count <= 0:
            raise ProviderModerationError("INVALID_MODEL_OUTPUT")
        if response.get("cachedContentTokenCount", 0) != 0 or response.get("cacheTokensDetails", []):
            raise ProviderModerationError("INVALID_MODEL_OUTPUT")
        details = response.get("promptTokensDetails", [])
        if not isinstance(details, list) or len(details) > 1:
            raise ProviderModerationError("INVALID_MODEL_OUTPUT")
        if details and (
            not isinstance(details[0], dict)
            or set(details[0]) != {"modality", "tokenCount"}
            or details[0]["modality"] != "TEXT"
            or type(details[0]["tokenCount"]) is not int
            or details[0]["tokenCount"] != count
        ):
            raise ProviderModerationError("INVALID_MODEL_OUTPUT")
        bound = count + COUNT_FRAMING_MARGIN
        if bound > self.max_input_tokens:
            raise ProviderModerationError("MODEL_NOT_READY", failure_kind="INPUT_LIMIT")
        return bound

    async def _exchange(self, request: dict, *, count_only: bool = False) -> bytes | int:
        async with asyncio.timeout(self.timeout_seconds):
            async with self._client() as client:
                self.last_input_bound = await self._count_input_tokens(client, request)
                if count_only:
                    return self.last_input_bound
                return await self._post(client, INTERACTIONS_PATH, request)

    def _run(self, request: dict, *, count_only: bool = False) -> bytes | int:
        self.last_input_bound = None
        self._validate_request(request)
        try:
            # This sync API is called in the worker's inference thread.
            return asyncio.run(self._exchange(request, count_only=count_only))
        except ProviderModerationError:
            raise
        except (TimeoutError, httpx.TimeoutException):
            raise ProviderModerationError("INFERENCE_TIMEOUT", failure_kind="TIMEOUT") from None
        except Exception:
            # HTTP exceptions can contain full URLs/headers. Never propagate them.
            raise ProviderModerationError("TRANSIENT_DEPENDENCY", failure_kind="UNAVAILABLE") from None

    def respond(self, request: dict) -> bytes:
        return self._run(request)

    def count_input_tokens(self, request: dict) -> int:
        """One explicit preflight for diagnostics; respond already does this."""
        return self._run(request, count_only=True)
