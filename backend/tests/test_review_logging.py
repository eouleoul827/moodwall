"""Security logs stay finite and cannot copy request secrets into telemetry."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import UUID, uuid4

import pytest

from app.security_events import CATEGORIES, LOGGER_NAME, SecurityEvents, configure_security_logging


def _request(operation="operatorLogin", code=None, request_id=None):
    return SimpleNamespace(
        scope={
            "route": SimpleNamespace(operation_id=operation),
            "state": {"request_id": request_id or str(uuid4()), "security_code": code},
            "path": "/raw-secret-PATH",
            "query_string": b"password=raw-secret-QUERY",
            "headers": [(b"cookie", b"raw-secret-COOKIE"), (b"x-csrf-token", b"raw-secret-CSRF")],
            "client": ("raw-secret-IP", 1234),
        }
    )


def _records(logger):
    return [json.loads(call.args[0]) for call in logger.info.call_args_list]


@pytest.mark.parametrize("operation", ["operatorLogin", "operatorLogout", "exchangeAdmission", "pairDisplay"])
def test_successful_authentication_changes_are_structured_without_request_secrets(operation):
    logger = Mock()
    request = _request(operation)
    SecurityEvents(logger=logger).observe(request, 200)
    event = _records(logger)[0]
    assert event["event"] == "authentication" and event["outcome"] == "success"
    assert event["operationId"] == operation and event["status"] == 200 and event["code"] == "OK"
    assert event["requestId"] == request.scope["state"]["request_id"]
    assert datetime.fromisoformat(event["timestamp"]).utcoffset().total_seconds() == 0
    assert "raw-secret" not in logger.info.call_args.args[0]


@pytest.mark.parametrize(
    "status,code,event",
    [
        (401, "INVALID_CREDENTIALS", "authentication"),
        (401, "SESSION_EXPIRED", "authentication"),
        (403, "FORBIDDEN", "authorization"),
        (403, "CSRF_REJECTED", "security_control"),
        (403, "ORIGIN_REJECTED", "security_control"),
        (429, "RATE_LIMITED", "anti_automation"),
        (429, "QUOTA_EXCEEDED", "anti_automation"),
    ],
)
def test_expected_security_failures_are_recorded(status, code, event):
    logger = Mock()
    SecurityEvents(logger=logger).observe(_request("submitMention", code), status)
    result = _records(logger)[0]
    assert result["event"] == event and result["outcome"] == "failure"
    assert result["code"] == code and result["status"] == status


@pytest.mark.parametrize("status", [200, 202, 400, 404, 422, 500, None, "401", True])
def test_other_requests_do_not_create_security_log_noise(status):
    logger = Mock()
    SecurityEvents(logger=logger).observe(_request("getDisplayState"), status)
    logger.info.assert_not_called()


@pytest.mark.parametrize("value", ["injected\nsecret", "x" * 100_000, [], {}, None])
def test_unrecognized_labels_and_request_ids_are_not_logged_verbatim(value):
    logger = Mock()
    request = _request()
    request.scope["route"].operation_id = value
    request.scope["state"].update(security_code=value, request_id=value)
    SecurityEvents(logger=logger).observe(request, 403)
    result = _records(logger)[0]
    assert result["operationId"] == "unrecognized" and result["code"] == "UNSPECIFIED"
    assert UUID(result["requestId"]).version == 4
    assert len(logger.info.call_args.args[0]) < 400
    assert "secret" not in logger.info.call_args.args[0]


def test_global_volume_cap_uses_fixed_bounded_categories_and_one_window_summary():
    logger = Mock()
    clock = [0.0]
    events = SecurityEvents(max_events=2, window_seconds=60, clock=lambda: clock[0], logger=logger)
    for index in range(10_000):
        events.observe(_request("attacker-generated-" + str(index), "untrusted-" + str(index)), 401)
    assert len(_records(logger)) == 2
    assert set(events._suppressed) == set(CATEGORIES)
    assert events._suppressed["authentication.failure"] == 9998
    clock[0] = 60.0
    # Even an ordinary request flushes the elapsed window's aggregate once.
    events.observe(_request("getDisplayState"), 200)
    summary = _records(logger)[-1]
    assert summary["event"] == "security_events_suppressed"
    assert summary["counts"] == {"authentication.failure": 9998}
    assert "attacker-generated" not in json.dumps(_records(logger))
    events.observe(_request("operatorLogin", "INVALID_CREDENTIALS"), 401)
    events.observe(_request("operatorLogin", "INVALID_CREDENTIALS"), 401)
    events.observe(_request("operatorLogin", "INVALID_CREDENTIALS"), 401)
    assert len(_records(logger)) == 5
    assert events._suppressed["authentication.failure"] == 1


def test_logging_destination_failure_does_not_break_the_request():
    logger = Mock()
    logger.info.side_effect = OSError("synthetic unavailable log sink")
    SecurityEvents(logger=logger).observe(_request(), 200)


def test_security_stdout_configuration_is_idempotent_and_preserves_existing_handlers():
    logger = logging.getLogger(LOGGER_NAME)
    previous = (logger.handlers[:], logger.level, logger.propagate)
    logger.handlers = []
    created = []
    try:
        configure_security_logging()
        created = logger.handlers[:]
        configure_security_logging()
        assert len(created) == len(logger.handlers) == 1
        assert logger.handlers[0] is created[0]
        assert logger.level == logging.INFO and logger.propagate is False
        existing = logging.NullHandler()
        logger.handlers = [existing]
        configure_security_logging()
        assert logger.handlers == [existing]
    finally:
        for handler in created:
            handler.close()
        logger.handlers, logger.level, logger.propagate = previous
