"""Bounded security telemetry without request payloads or credentials.

The process keeps only fixed-category suppression counters. Detailed events are
limited globally; the next observed request after a window emits one aggregate
for suppressed events. This is telemetry, not the transactional operator audit.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone
from uuid import UUID, uuid4

LOGGER_NAME = "moodwall.security"
OPERATIONS = frozenset(
    {
        "liveness",
        "readiness",
        "emojiOptions",
        "exchangeAdmission",
        "participantSession",
        "participantUsage",
        "submitMention",
        "getOwnSubmission",
        "pairDisplay",
        "getDisplaySession",
        "getCurrentQr",
        "getDisplayState",
        "displayEvents",
        "displayHeartbeat",
        "setDeviceMode",
        "operatorLogin",
        "getOperatorSession",
        "operatorLogout",
        "createPairingCode",
        "revokeDisplay",
        "setOperatorMode",
        "listOperatorSubmissions",
        "hideSubmission",
        "getOperatorParticipantUsage",
        "restoreDailyRestriction",
        "listAudit",
        "setIntakePause",
    }
)
AUTHENTICATION_OPERATIONS = frozenset({"exchangeAdmission", "pairDisplay", "operatorLogin", "operatorLogout"})
CODES = frozenset(
    {
        "SESSION_REQUIRED",
        "SESSION_EXPIRED",
        "INVALID_CREDENTIALS",
        "FORBIDDEN",
        "CSRF_REJECTED",
        "ORIGIN_REJECTED",
        "RATE_LIMITED",
        "ADMISSION_EXPIRED",
        "PARTICIPANT_SUSPENDED",
        "SUBMISSION_COOLDOWN",
        "QUOTA_EXCEEDED",
    }
)
CATEGORIES = (
    "authentication.success",
    "authentication.failure",
    "authorization.failure",
    "security_control.failure",
    "anti_automation.failure",
)
MAX_COUNTER = 2**63 - 1


def configure_security_logging() -> None:
    """Install stdout JSON logging once, preserving an existing handler setup."""
    import sys

    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)


def _operation(request) -> str:
    # Never derive labels from raw paths, URL parameters, headers or query text.
    value = getattr(request.scope.get("route"), "operation_id", None)
    return value if type(value) is str and value in OPERATIONS else "unrecognized"


def _code(request) -> str:
    value = request.scope.get("state", {}).get("security_code")
    return value if type(value) is str and value in CODES else "UNSPECIFIED"


def _request_id(request) -> str:
    value = request.scope.get("state", {}).get("request_id")
    if type(value) is str and len(value) == 36:
        try:
            parsed = UUID(value)
            if parsed.version == 4 and str(parsed) == value:
                return value
        except ValueError:
            pass
    # A defensive fallback never copies a caller-supplied request-id header.
    return str(uuid4())


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class SecurityEvents:
    def __init__(self, *, max_events=120, window_seconds=60, clock=time.monotonic, logger=None):
        if type(max_events) is not int or not 1 <= max_events <= 10_000:
            raise ValueError("max_events must be in 1..10000")
        if type(window_seconds) is not int or not 1 <= window_seconds <= 3600:
            raise ValueError("window_seconds must be in 1..3600")
        self.logger = logger or logging.getLogger(LOGGER_NAME)
        self.max_events, self.window_seconds, self.clock = max_events, window_seconds, clock
        self._window_start = clock()
        self._emitted = 0
        self._suppressed = dict.fromkeys(CATEGORIES, 0)
        self._lock = threading.Lock()

    @staticmethod
    def _classification(operation, status, code):
        if status == 401:
            return "authentication", "failure"
        if status == 403:
            return ("security_control" if code in {"CSRF_REJECTED", "ORIGIN_REJECTED"} else "authorization"), "failure"
        if status == 429:
            return "anti_automation", "failure"
        if 200 <= status < 300 and operation in AUTHENTICATION_OPERATIONS:
            return "authentication", "success"
        return None

    def observe(self, request, status) -> None:
        if type(status) is not int or not 100 <= status <= 599:
            return
        operation, code = _operation(request), _code(request)
        classification = self._classification(operation, status, code)
        records = []
        with self._lock:
            now = self.clock()
            if now - self._window_start >= self.window_seconds:
                suppressed = {key: value for key, value in self._suppressed.items() if value}
                if suppressed:
                    records.append(
                        {
                            "timestamp": _timestamp(),
                            "requestId": _request_id(request),
                            "event": "security_events_suppressed",
                            "outcome": "summary",
                            "windowSeconds": self.window_seconds,
                            "counts": suppressed,
                        }
                    )
                self._window_start, self._emitted = now, 0
                self._suppressed = dict.fromkeys(CATEGORIES, 0)
            if classification is not None:
                event, outcome = classification
                if self._emitted < self.max_events:
                    records.append(
                        {
                            "timestamp": _timestamp(),
                            "requestId": _request_id(request),
                            "event": event,
                            "outcome": outcome,
                            "operationId": operation,
                            "status": status,
                            "code": "OK" if outcome == "success" else code,
                        }
                    )
                    self._emitted += 1
                else:
                    category = event + "." + outcome
                    self._suppressed[category] = min(MAX_COUNTER, self._suppressed[category] + 1)
        for record in records:
            try:
                self.logger.info(json.dumps(record, ensure_ascii=True, separators=(",", ":"), sort_keys=True))
            except Exception:
                # An unavailable log destination must not turn denied traffic
                # into an unhandled request failure or expose its input.
                pass
