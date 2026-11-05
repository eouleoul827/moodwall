from __future__ import annotations

import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from psycopg_pool import PoolTimeout
from starlette.exceptions import HTTPException

from app import dto
from app.auth import AuthService
from app.config import get_settings
from app.db import Database, db_time, one, schema_ready
from app.diagnostics import DisplayDiagnosticsService
from app.display import DisplayService
from app.domain import EMOJI, business_day
from app.errors import Problem, problem_response
from app.frontend import install_frontend
from app.http import body, decode_json, get_origin_check, origin_check
from app.maintenance import RetentionService
from app.moderation import ModerationService
from app.operator import OperatorService
from app.playback import PlaybackService
from app.provider_control import ProviderControlService
from app.queue import cleanup_finished, create_queue, enqueue
from app.reports import ReportService
from app.request_limits import bootstrap_limit, limit, read_limit
from app.security import normalize_username
from app.security_events import SecurityEvents
from app.submissions import SubmissionService

logger = logging.getLogger("moodwall")


def respond(value, status=200, headers=None):
    return JSONResponse(jsonable_encoder(value), status_code=status, headers=headers)


class GuardMiddleware:
    """Bounded JSON ingress, deadline and safe response headers, including errors."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        request = Request(scope, receive)
        scope.setdefault("state", {})["request_id"] = str(uuid4())
        started = False
        deadline = asyncio.get_running_loop().time() + 8
        is_sse = scope["path"] == "/api/display/events" and scope["method"] == "GET"

        async def guarded_send(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                security_headers = {}
                security_headers[b"x-request-id"] = request.state.request_id.encode()
                security_headers[b"cache-control"] = b"no-store"
                security_headers[b"x-content-type-options"] = b"nosniff"
                security_headers[b"referrer-policy"] = b"no-referrer"
                security_headers[b"x-frame-options"] = b"DENY"
                if request.app.state.settings.secure_cookies:
                    security_headers[b"strict-transport-security"] = b"max-age=31536000"
                message["headers"] = [
                    (key, value) for key, value in message.get("headers", []) if key.lower() not in security_headers
                ] + list(security_headers.items())
                observer = getattr(request.app.state, "security_events", None)
                if observer is not None:
                    observer.observe(request, message["status"])
            if is_sse:
                await asyncio.wait_for(send(message), timeout=5)
            else:
                await send(message)

        try:
            if scope["method"] == "POST":
                origin_check(request)
                content_type = request.headers.get("content-type", "").lower()
                media, *parameters = content_type.split(";")
                if (
                    media.strip() != "application/json"
                    or any(
                        part.strip().startswith("charset=") and part.strip().split("=", 1)[1].strip('"') != "utf-8"
                        for part in parameters
                    )
                    or request.headers.get("content-encoding", "identity").lower() != "identity"
                ):
                    raise Problem(415, "UNSUPPORTED_MEDIA_TYPE")
                chunks, size = [], 0
                async with asyncio.timeout_at(deadline):
                    while True:
                        message = await receive()
                        if message["type"] == "http.disconnect":
                            return
                        data = message.get("body", b"")
                        size += len(data)
                        if size > 4096:
                            raise Problem(413, "PAYLOAD_TOO_LARGE")
                        chunks.append(data)
                        if not message.get("more_body", False):
                            break
                raw = b"".join(chunks)
                value = decode_json(raw)
                if not isinstance(value, dict):
                    raise Problem(422, "VALIDATION_ERROR")
                scope["state"]["raw_body"] = raw

                async def replay():
                    return {"type": "http.request", "body": raw, "more_body": False}

                # Routes consume the validated state body; downstream parsing remains safe.
                route_receive = replay
            else:
                route_receive = receive
            if is_sse:
                await self.app(scope, route_receive, guarded_send)
            else:
                async with asyncio.timeout_at(deadline):
                    await self.app(scope, route_receive, guarded_send)
        except Problem as error:
            if not started:
                await problem_response(request, error)(scope, receive, guarded_send)
        except (psycopg.Error, PoolTimeout):
            if not started:
                await problem_response(request, Problem(503, "DB_UNAVAILABLE", retry_after=1))(
                    scope, receive, guarded_send
                )
        except TimeoutError:
            if not started:
                await problem_response(request, Problem(503, "SERVICE_UNAVAILABLE", retry_after=1))(
                    scope, receive, guarded_send
                )
        except Exception as error:
            logger.error("request_failed type=%s request_id=%s", type(error).__name__, request.state.request_id)
            if not started:
                await problem_response(request, Problem(500, "INTERNAL_ERROR"))(scope, receive, guarded_send)


async def actor(request, role, write=False):
    auth, settings = request.app.state.auth, request.app.state.settings
    current = await auth.authenticate(role, request.cookies.get(settings.cookie_name(role)))
    if write:
        auth.check_csrf(current, request.headers.get("x-csrf-token"))
        await limit(request, "write:" + role, str(current.id), settings.write_per_minute)
    else:
        get_origin_check(request)
        await limit(request, "read:" + role, str(current.id), read_limit(settings, role))
    return current


async def bootstrap(request, role):
    await limit(
        request,
        "bootstrap:" + role,
        request.client.host if request.client else "unknown",
        bootstrap_limit(request.app.state.settings, role),
    )
    return await request.app.state.auth.bootstrap_guard(
        role, request.cookies.get(request.app.state.settings.cookie_name(role)), request.headers.get("x-csrf-token")
    )


def cookie_response(request, value, token, role):
    response = respond(value)
    seconds = 8 * 3600 if role == "operator" else 30 * 86400
    response.set_cookie(
        request.app.state.settings.cookie_name(role),
        token,
        max_age=seconds,
        httponly=True,
        secure=request.app.state.settings.secure_cookies,
        samesite="lax",
        path="/",
    )
    return response


def key_header(request):
    try:
        return UUID(request.headers["idempotency-key"])
    except (KeyError, ValueError):
        raise Problem(422, "VALIDATION_ERROR", "Idempotency-Key UUID가 필요합니다.") from None


def create_app(settings=None):
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app):
        db = Database(settings)
        q = None
        tasks = []
        try:
            await db.open()
            q = create_queue(settings)
            await q.open_async()
            auth = AuthService(db, settings)
            display = DisplayService(db, settings, auth)
            moderation = ModerationService(db, settings)
            retention = RetentionService(db, settings)
            app.state.db, app.state.auth = db, auth
            app.state.display, app.state.moderation = display, moderation

            def intake_ready():
                return not settings.maintenance_enabled or all(
                    time.monotonic() - app.state.loops.get(name, 0) <= budget
                    for name, budget in (
                        ("display", 10),
                        ("playback", 10),
                        ("playback_retention", 10),
                        ("expiry", 15),
                        ("stalled", 30),
                        ("content_retention", 15),
                    )
                )

            app.state.submissions = SubmissionService(db, settings, auth, enqueue, intake_ready)
            app.state.playback = PlaybackService(db, settings, auth, enqueue, intake_ready)
            app.state.operator = OperatorService(db, settings, auth, display)
            app.state.reports = ReportService(db, settings, auth)
            app.state.provider = ProviderControlService(db, settings)
            app.state.diagnostics = DisplayDiagnosticsService(db, settings, auth)
            app.state.loops = {}

            async def loop(name, interval, function):
                while True:
                    try:
                        await function()
                        app.state.loops[name] = time.monotonic()
                    except asyncio.CancelledError:
                        raise
                    except Exception as error:
                        logger.error("maintenance_failed loop=%s type=%s", name, type(error).__name__)
                    await asyncio.sleep(interval)

            if settings.maintenance_enabled:

                async def cleanup():
                    await retention.cleanup()
                    await app.state.reports.cleanup()
                    await app.state.provider.recover_stale()
                    await app.state.diagnostics.cleanup()
                    await cleanup_finished(settings)

                for name, interval, function in (
                    ("display", 2, display.reconcile),
                    ("playback", 1, app.state.playback.reconcile),
                    ("playback_retention", 1, app.state.playback.cleanup),
                    ("expiry", 5, moderation.expire_due),
                    ("stalled", 10, moderation.recover_stalled),
                    ("content_retention", 5, retention.cleanup_submissions),
                    ("retention", 3600, cleanup),
                ):
                    tasks.append(asyncio.create_task(loop(name, interval, function)))
            yield
        finally:
            try:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
            finally:
                try:
                    if q is not None:
                        await q.close_async()
                finally:
                    await db.close()

    app = FastAPI(
        title="Moodwall API",
        version="0.11.0",
        lifespan=lifespan,
        docs_url="/docs" if settings.environment != "production" else None,
        redoc_url=None,
    )
    app.state.settings = settings
    app.state.security_events = SecurityEvents()
    # The reviewed contract also documents manually parsed strict request bodies.
    app.openapi = lambda: json.loads(
        (Path(__file__).resolve().parent / "resources/openapi.json").read_text(encoding="utf-8")
    )
    app.add_middleware(GuardMiddleware)

    @app.exception_handler(Problem)
    async def problem_handler(request, error):
        return problem_response(request, error)

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request, error):
        return problem_response(request, Problem(422, "VALIDATION_ERROR"))

    @app.exception_handler(HTTPException)
    async def http_handler(request, error):
        codes = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}
        return problem_response(
            request, Problem(error.status_code, codes.get(error.status_code, "HTTP_ERROR")), error.headers
        )

    @app.get("/api/health/live", operation_id="liveness")
    async def live():
        return {"status": "ok"}

    @app.get("/api/health/ready", operation_id="readiness")
    async def ready(request: Request):
        async with request.app.state.db.connection() as conn:
            if not await schema_ready(conn, settings.board_id):
                raise Problem(503, "SERVICE_UNAVAILABLE")
        if settings.maintenance_enabled and any(
            time.monotonic() - request.app.state.loops.get(name, 0) > budget
            for name, budget in (
                ("display", 10),
                ("playback", 10),
                ("playback_retention", 10),
                ("expiry", 15),
                ("stalled", 30),
                ("content_retention", 15),
            )
        ):
            raise Problem(503, "SERVICE_UNAVAILABLE")
        return {"status": "ok"}

    @app.get("/api/emoji-options", operation_id="emojiOptions")
    async def emojis():
        return {"items": [{"id": key, **value} for key, value in EMOJI.items()]}

    @app.post("/api/admissions/exchange", operation_id="exchangeAdmission")
    async def exchange(request: Request):
        existing = await bootstrap(request, "student")
        data = await body(request, dto.Admission)
        value, token = await request.app.state.auth.exchange(data.ticket, existing)
        return cookie_response(request, value, token, "student")

    @app.get("/api/session", operation_id="participantSession")
    async def participant_session(request: Request):
        return respond(await request.app.state.auth.session(await actor(request, "student")))

    @app.get("/api/me/usage", operation_id="participantUsage")
    async def participant_usage(request: Request):
        return respond(await request.app.state.submissions.usage(await actor(request, "student")))

    @app.post("/api/submissions", operation_id="submitMention")
    async def submit(request: Request):
        current = await actor(request, "student", True)
        data = await body(request, dto.Mention)
        value, new = await request.app.state.submissions.submit(
            current, key_header(request), data.content, data.emojiId
        )
        return respond(
            value,
            202 if new else 200,
            {"Location": "/api/submissions/" + value["id"], "Retry-After": "1"} if new else None,
        )

    @app.get("/api/submissions/{submission_id}", operation_id="getOwnSubmission")
    async def own(request: Request, submission_id: UUID):
        return respond(await request.app.state.submissions.get(await actor(request, "student"), submission_id))

    @app.post("/api/v2/submissions", operation_id="submitMentionV2")
    async def submit_v2(request: Request):
        current = await actor(request, "student", True)
        data = await body(request, dto.MentionV2)
        value, new = await request.app.state.playback.submit(
            current, key_header(request), data.content, request.headers.get("X-Moodwall-Client-Features", "")
        )
        return respond(
            value,
            202 if new else 200,
            {"Location": "/api/v2/submissions/" + value["submissionId"], "Retry-After": "2"} if new else None,
        )

    @app.get("/api/v2/submissions/{submission_id}", operation_id="getOwnSubmissionV2")
    async def own_v2(request: Request, submission_id: UUID):
        return respond(
            await request.app.state.playback.get_receipt(
                await actor(request, "student"), submission_id, request.headers.get("X-Moodwall-Client-Features", "")
            )
        )

    @app.post("/api/v2/submission-receipts/lookup", operation_id="lookupSubmissionReceipt")
    async def lookup_receipt(request: Request):
        current = await actor(request, "student", True)
        data = await body(request, dto.ReceiptLookup)
        return respond(
            await request.app.state.playback.lookup_receipt(
                current, data.idempotencyKey, request.headers.get("X-Moodwall-Client-Features", "")
            )
        )

    @app.post("/api/v2/reports", operation_id="submitContentReport")
    async def submit_report(request: Request):
        current = await actor(request, "student", True)
        data = await body(request, dto.ContentReport)
        value, new = await request.app.state.reports.submit(
            current, key_header(request), data.category, data.submissionId
        )
        return respond(value, 202 if new else 200)

    @app.post("/api/display/pair", operation_id="pairDisplay")
    async def pair(request: Request):
        existing = await bootstrap(request, "display")
        data = await body(request, dto.Pair)
        value, token = await request.app.state.auth.pair(data.code, existing)
        return cookie_response(request, value, token, "display")

    @app.get("/api/display/session", operation_id="getDisplaySession")
    async def display_session(request: Request):
        return respond(await request.app.state.auth.session(await actor(request, "display")))

    @app.post("/api/display/diagnostics", operation_id="recordDisplayDiagnostic")
    async def display_diagnostic(request: Request):
        # Diagnostics have an independent budget and never consume playback's
        # write budget or refresh a heartbeat/ownership lease.
        current = await request.app.state.auth.authenticate(
            "display", request.cookies.get(settings.cookie_name("display"))
        )
        request.app.state.auth.check_csrf(current, request.headers.get("x-csrf-token"))
        await limit(request, "diagnostics:display", str(current.id) + ":" + str(current.extra["device_token_version"]), 10)
        report = await body(request, dto.DisplayDiagnostic)
        return respond(await request.app.state.diagnostics.record(current, report))

    @app.get("/api/operator/display/diagnostics", operation_id="getDisplayDiagnostics")
    async def operator_display_diagnostics(request: Request):
        current = await actor(request, "operator")
        return respond(await request.app.state.diagnostics.latest(current))

    @app.get("/api/display/qr", operation_id="getCurrentQr")
    async def qr(request: Request):
        return respond(await request.app.state.display.current_qr(await actor(request, "display")))

    @app.get("/api/display/state", operation_id="getDisplayState")
    async def state(request: Request, cursor: str | None = None):
        current = await actor(request, "display")
        if cursor is not None and not 1 <= len(cursor) <= 2048:
            raise Problem(422, "INVALID_CURSOR")
        return respond(await request.app.state.display.state(current, cursor=cursor))

    @app.post("/api/display/heartbeat", operation_id="displayHeartbeat")
    async def heartbeat(request: Request):
        current = await actor(request, "display", True)
        data = await body(request, dto.Heartbeat)
        if data.displayProtocolVersion == "2":
            return respond(await request.app.state.playback.heartbeat(current, data.clientInstanceId))
        return respond(await request.app.state.display.heartbeat(current, data.clientInstanceId))

    @app.get("/api/display/playback", operation_id="getDisplayPlayback")
    async def playback_state(request: Request, clientInstanceId: UUID):
        return respond(await request.app.state.playback.state(await actor(request, "display"), clientInstanceId))

    @app.get("/api/v2/display/state", operation_id="getDisplayStateV2")
    async def playback_bubbles(request: Request, cursor: str | None = None):
        if cursor is not None and not 1 <= len(cursor) <= 2048:
            raise Problem(422, "INVALID_CURSOR")
        return respond(await request.app.state.playback.v2snapshot(await actor(request, "display"), cursor=cursor))

    @app.post("/api/display/playback/claim", operation_id="claimDisplayPlayback")
    async def playback_claim(request: Request):
        current = await actor(request, "display", True)
        data = await body(request, dto.PlaybackClaim)
        value = await request.app.state.playback.claim(
            current, key_header(request), data.model_dump(mode="json", exclude_none=True)
        )
        if value is None:
            from starlette.responses import Response

            return Response(status_code=204)
        return respond(value)

    async def playback_command(request, attempt_id, action, model=dto.PlaybackCommand):
        current = await actor(request, "display", True)
        data = await body(request, model)
        return respond(
            await request.app.state.playback.command(
                current, attempt_id, action, key_header(request), data.model_dump(mode="json")
            )
        )

    @app.post("/api/display/playback/{attempt_id}/start", operation_id="startDisplayPlayback")
    async def playback_start(request: Request, attempt_id: UUID):
        return await playback_command(request, attempt_id, "start")

    @app.post("/api/display/playback/{attempt_id}/frame", operation_id="frameDisplayPlayback")
    async def playback_frame(request: Request, attempt_id: UUID):
        return await playback_command(request, attempt_id, "frame")

    @app.post("/api/display/playback/{attempt_id}/complete", operation_id="completeDisplayPlayback")
    async def playback_complete(request: Request, attempt_id: UUID):
        return await playback_command(request, attempt_id, "complete")

    @app.post("/api/display/playback/{attempt_id}/interrupt", operation_id="interruptDisplayPlayback")
    async def playback_interrupt(request: Request, attempt_id: UUID):
        return await playback_command(request, attempt_id, "interrupt", dto.PlaybackInterrupt)

    @app.post("/api/display/mode", operation_id="setDeviceMode")
    async def device_mode(request: Request):
        current = await actor(request, "display", True)
        data = await body(request, dto.DeviceMode)
        return respond(await request.app.state.display.set_mode(current, data.mode))

    @app.get("/api/display/events", operation_id="displayEvents")
    async def events(request: Request):
        current = await actor(request, "display")

        async def stream():
            start, heartbeat, last_revision, last_bucket = time.monotonic(), 0, None, None
            try:
                while time.monotonic() - start < 300 and not await request.is_disconnected():
                    async with request.app.state.db.connection() as conn:
                        await request.app.state.auth.recheck(current, conn)
                        row = await one(
                            conn, "SELECT state_version FROM board_runtime WHERE id=%s", (settings.board_id,)
                        )
                        now = await db_time(conn)
                    payload = json.dumps(
                        {"revision": row["state_version"], "businessDate": str(business_day(now))},
                        separators=(",", ":"),
                    )
                    if row["state_version"] != last_revision:
                        yield "event: state.changed\ndata: " + payload + "\n\n"
                        last_revision = row["state_version"]
                    bucket = int(now.timestamp()) // 300
                    if bucket != last_bucket:
                        yield "event: qr.changed\ndata: " + payload + "\n\n"
                        last_bucket = bucket
                    if time.monotonic() - heartbeat >= 15:
                        yield ": keepalive\n\n"
                        heartbeat = time.monotonic()
                    await asyncio.sleep(2)
            except (Problem, psycopg.Error, PoolTimeout):
                return

        return StreamingResponse(stream(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"})

    @app.post("/api/operator/login", operation_id="operatorLogin")
    async def login(request: Request):
        existing = await bootstrap(request, "operator")
        data = await body(request, dto.Login)
        try:
            username = normalize_username(data.username)
        except ValueError:
            raise Problem(401, "INVALID_CREDENTIALS") from None
        await limit(request, "login:account", username, 10, 300)
        value, token = await request.app.state.auth.login(data.username, data.password, existing)
        return cookie_response(request, value, token, "operator")

    @app.get("/api/operator/session", operation_id="getOperatorSession")
    async def operator_session(request: Request):
        return respond(await request.app.state.auth.session(await actor(request, "operator")))

    @app.post("/api/operator/logout", operation_id="operatorLogout")
    async def logout(request: Request):
        current = await actor(request, "operator", True)
        await body(request, dto.Empty)
        await request.app.state.auth.logout(current)
        response = respond({"ok": True, "serverNow": datetime.now(timezone.utc)})
        response.delete_cookie(
            settings.cookie_name("operator"), path="/", secure=settings.secure_cookies, httponly=True, samesite="lax"
        )
        return response

    @app.post("/api/operator/display/pairing-codes", operation_id="createPairingCode")
    async def pairing_code(request: Request):
        current = await actor(request, "operator", True)
        data = await body(request, dto.Reason)
        return respond(await request.app.state.operator.create_pairing_code(current, data.reasonCode))

    @app.post("/api/operator/display/revoke", operation_id="revokeDisplay")
    async def revoke(request: Request):
        current = await actor(request, "operator", True)
        data = await body(request, dto.Reason)
        return respond(await request.app.state.operator.revoke_display(current, data.reasonCode))

    @app.post("/api/operator/board/mode", operation_id="setOperatorMode")
    async def operator_mode(request: Request):
        current = await actor(request, "operator", True)
        data = await body(request, dto.OperatorMode)
        return respond(await request.app.state.operator.set_mode(current, data.mode, data.reasonCode))

    @app.post("/api/operator/board/intake", operation_id="setIntakePause")
    async def intake(request: Request):
        current = await actor(request, "operator", True)
        data = await body(request, dto.Intake)
        return respond(await request.app.state.operator.intake(current, data.paused, data.reasonCode))

    @app.get("/api/operator/submissions", operation_id="listOperatorSubmissions")
    async def list_submissions(
        request: Request,
        businessDate: date | None = None,
        status: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ):
        return respond(
            await request.app.state.operator.list_submissions(
                await actor(request, "operator"), business_date=businessDate, status=status, cursor=cursor, limit=limit
            )
        )

    @app.get("/api/operator/reports", operation_id="listContentReports")
    async def list_reports(request: Request, cursor: str | None = None):
        return respond(await request.app.state.reports.list(await actor(request, "operator"), cursor))

    @app.post("/api/operator/reports/{report_id}/resolve", operation_id="resolveContentReport")
    async def resolve_report(request: Request, report_id: UUID):
        current = await actor(request, "operator", True)
        data = await body(request, dto.ResolveReport)
        return respond(
            await request.app.state.reports.resolve(current, report_id, key_header(request), data.resolution)
        )

    @app.get("/api/operator/moderation/provider", operation_id="getModerationProvider")
    async def get_provider(request: Request):
        current = await actor(request, "operator")
        await request.app.state.auth.require_operator(current, ("ADMIN", "MODERATOR"))
        return respond(await request.app.state.provider.summary())

    @app.post("/api/operator/moderation/provider", operation_id="setModerationProviderPause")
    async def pause_provider(request: Request):
        current = await actor(request, "operator", True)
        data = await body(request, dto.ProviderPause)
        await request.app.state.auth.require_operator(current, ("ADMIN",))
        return respond(
            await request.app.state.provider.set_paused(data.paused, actor=current, auth=request.app.state.auth)
        )

    @app.post("/api/operator/submissions/{submission_id}/hide", operation_id="hideSubmission")
    async def hide(request: Request, submission_id: UUID):
        current = await actor(request, "operator", True)
        data = await body(request, dto.Reason)
        result = await request.app.state.operator.hide(current, submission_id, data.reasonCode)
        await request.app.state.playback.sync_submission(submission_id)
        return respond(result)

    @app.get("/api/operator/participants/{participant_id}/usage", operation_id="getOperatorParticipantUsage")
    async def operator_usage(request: Request, participant_id: UUID):
        return respond(await request.app.state.operator.usage(await actor(request, "operator"), participant_id))

    @app.post(
        "/api/operator/daily-usage/{participant_id}/{business_date}/restore", operation_id="restoreDailyRestriction"
    )
    async def restore(request: Request, participant_id: UUID, business_date: date):
        current = await actor(request, "operator", True)
        data = await body(request, dto.Reason)
        return respond(
            await request.app.state.operator.restore(
                current, participant_id, business_date, key_header(request), data.reasonCode
            )
        )

    @app.get("/api/operator/audit", operation_id="listAudit")
    async def audit(request: Request, businessDate: date | None = None, cursor: str | None = None, limit: int = 50):
        return respond(
            await request.app.state.operator.audit(
                await actor(request, "operator"), business_date=businessDate, cursor=cursor, limit=limit
            )
        )

    install_frontend(app, settings.frontend_dist)
    return app


app = create_app()
