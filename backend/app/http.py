from __future__ import annotations

import json
import math
import unicodedata

from pydantic import ValidationError

from app.errors import Problem


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _finite(value):
    raise ValueError("nonfinite JSON number")


def _unicode(value):
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("nonfinite JSON number")
    if isinstance(value, str):
        for character in value:
            code = ord(character)
            if unicodedata.category(character) == "Cs" or 0xFDD0 <= code <= 0xFDEF or code & 0xFFFF in (0xFFFE, 0xFFFF):
                raise ValueError("invalid Unicode scalar")
    elif isinstance(value, dict):
        for key, item in value.items():
            _unicode(key)
            _unicode(item)
    elif isinstance(value, list):
        for item in value:
            _unicode(item)


def decode_json(raw: bytes):
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_object, parse_constant=_finite)
        _unicode(value)
        return value
    except (ValueError, UnicodeError, RecursionError):
        raise Problem(400, "MALFORMED_JSON") from None


async def body(request, model):
    # Middleware validates JSON and limits the actual ASGI body stream before routing.
    try:
        # JSON mode preserves strict primitives while parsing UUID/date strings correctly.
        return model.model_validate_json(request.state.raw_body)
    except ValidationError:
        raise Problem(422, "VALIDATION_ERROR") from None


def origin_check(request):
    if request.headers.get("origin") != request.app.state.settings.public_origin:
        raise Problem(403, "ORIGIN_REJECTED")


def get_origin_check(request):
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise Problem(403, "ORIGIN_REJECTED")
    if request.headers.get("origin") is not None:
        origin_check(request)
