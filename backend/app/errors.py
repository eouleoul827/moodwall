from __future__ import annotations

from http import HTTPStatus
from uuid import uuid4

from fastapi import Request
from fastapi.responses import JSONResponse

BOOTSTRAP_PATHS = {
    "student": "/api/admissions/exchange",
    "operator": "/api/operator/login",
    "display": "/api/display/pair",
}


class Problem(Exception):
    def __init__(
        self, status: int, code: str, detail: str = "", retry_after: int | None = None, cookie_role: str | None = None
    ):
        super().__init__(code)
        self.status, self.code = status, code
        self.detail = detail or "요청을 처리할 수 없습니다."
        self.retry_after, self.cookie_role = retry_after, cookie_role


def problem_response(request: Request, error: Problem, headers=None):
    request.state.security_code = error.code
    request_id = getattr(request.state, "request_id", str(uuid4()))
    response = JSONResponse(
        status_code=error.status,
        media_type="application/problem+json",
        content={
            "type": "/problems/" + error.code.lower().replace("_", "-"),
            "title": HTTPStatus(error.status).phrase,
            "status": error.status,
            "detail": error.detail,
            "instance": "urn:uuid:" + request_id,
            "code": error.code,
            "requestId": request_id,
            "retryAfterSeconds": error.retry_after,
            "errors": [],
        },
        headers={"X-Request-ID": request_id, "Cache-Control": "no-store", **(headers or {})},
    )
    if error.retry_after is not None:
        response.headers["Retry-After"] = str(error.retry_after)
    # An old request can finish after a replacement credential was issued.
    # Deleting the shared cookie on its 401 would delete the new credential,
    # even when JavaScript discards that response by authentication epoch.
    # Only the client's serialized bootstrap recovery may clear a stale cookie.
    if (
        error.status == 401
        and error.code == "SESSION_EXPIRED"
        and request.method == "POST"
        and request.scope["path"] == BOOTSTRAP_PATHS.get(error.cookie_role)
    ):
        settings = request.app.state.settings
        response.delete_cookie(
            settings.cookie_name(error.cookie_role),
            path="/",
            secure=settings.secure_cookies,
            httponly=True,
            samesite="lax",
        )
    return response
