"""Durable moderation admission and one-dispatch accounting.

This module has no HTTP client. Transport stays in the worker's analyzer.
All money in the mock is synthetic micro-USD.
Provider control is always the first lock in a transaction that also owns any
board, participant or submission lock. No external work runs inside transactions.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

from app.db import all_rows, db_time, one
from app.domain import business_day
from app.errors import Problem

PACIFIC = ZoneInfo("America/Los_Angeles")
FINAL = {"COMPLETED", "UNKNOWN", "CANCELLED"}


def token_cost(settings, input_tokens: int, output_tokens: int) -> int:
    """Ceil once after summing both integer tariff products."""
    numerator = (
        input_tokens * settings.provider_input_micro_usd_per_million
        + output_tokens * settings.provider_output_micro_usd_per_million
    )
    return (numerator + 999999) // 1000000


class ProviderUnavailable(Exception):
    """Safe worker failure; text, payloads and exception bodies are never stored."""


class ProviderProbeTimeout(Exception):
    """The supervisor must kill a timed-out probe's native thread."""


class ProviderControlService:
    def __init__(self, db, settings):
        self.db, self.settings = db, settings

    @property
    def enabled(self):
        return self.settings.moderation_profile != "legacy"

    async def lock_control(self, conn):
        if not self.enabled:
            return None
        await conn.execute("INSERT INTO moderation_provider_control(id) VALUES('gemini') ON CONFLICT DO NOTHING")
        return await one(conn, "SELECT * FROM moderation_provider_control WHERE id='gemini' FOR UPDATE")

    def _require_transport(self, now=None):
        if self.settings.moderation_profile == "gemini_mock":
            return
        if self.settings.moderation_profile != "gemini" or self.settings.gemini_api_key is None:
            raise ProviderUnavailable("MODEL_NOT_READY")
        if not self.settings.gemini_api_key.get_secret_value().strip():
            raise ProviderUnavailable("MODEL_NOT_READY")
        moment = (now or datetime.now(UTC)).astimezone(UTC)
        today = moment.date()
        if not self.settings.provider_tariff_valid_from <= today <= self.settings.provider_tariff_valid_until:
            raise ProviderUnavailable("PROVIDER_TARIFF_EXPIRED")
        if (
            moment + timedelta(seconds=self.settings.provider_wrapper_timeout_seconds + 2)
        ).date() > self.settings.provider_tariff_valid_until:
            raise ProviderUnavailable("PROVIDER_TARIFF_EXPIRED")

    async def _capacity(self, conn, now, *, exclude=None, rate=True):
        settings = self.settings
        cost_day, rate_day = business_day(now), now.astimezone(PACIFIC).date()
        totals = await one(
            conn,
            """SELECT COALESCE((SELECT charged_micro_usd FROM moderation_provider_daily WHERE date=%s),0)
            + COALESCE(sum(reserved_micro_usd) FILTER(WHERE cost_day=%s AND status IN ('RESERVED','STARTED')),0) AS cost,
            COALESCE(sum(CASE WHEN profile='gemini' THEN 2 ELSE 1 END) FILTER(WHERE rate_day=%s AND status<>'CANCELLED'),0) AS calls,
            COALESCE(sum(CASE WHEN profile='gemini' THEN 2 ELSE 1 END) FILTER(WHERE status='RESERVED' OR started_at>%s-interval '60 seconds'),0) AS rpm,
            COALESCE(sum(input_token_reservation * CASE WHEN profile='gemini' THEN 2 ELSE 1 END) FILTER(WHERE status='RESERVED'
                OR started_at>%s-interval '60 seconds'),0) AS tpm
            FROM moderation_provider_attempts WHERE submission_id<>%s""",
            (cost_day, cost_day, rate_day, now, now, exclude or uuid4()),
        )
        reservation = token_cost(settings, settings.provider_max_input_tokens, settings.provider_max_output_tokens)
        if totals["cost"] + reservation > settings.provider_daily_budget_micro_usd:
            raise ProviderUnavailable("PROVIDER_BUDGET_EXHAUSTED")
        http_calls = 2 if settings.moderation_profile == "gemini" else 1
        if totals["calls"] + http_calls > settings.provider_rpd:
            raise ProviderUnavailable("PROVIDER_QUOTA_EXHAUSTED")
        if rate and (
            totals["rpm"] + http_calls > settings.provider_rpm
            or totals["tpm"] + settings.provider_max_input_tokens * http_calls > settings.provider_input_tpm
        ):
            raise ProviderUnavailable("PROVIDER_QUOTA_EXHAUSTED")
        return cost_day, rate_day, reservation

    async def reserve_submission(self, conn, submission_id):
        """Reserve in the caller's intake transaction; rollback undoes all costs."""
        if not self.enabled:
            return
        control = await self.lock_control(conn)
        try:
            self._require_transport()
            if control["mode"] != "OPEN":
                raise ProviderUnavailable("PROVIDER_UNAVAILABLE")
            if await one(
                conn, "SELECT submission_id FROM moderation_provider_attempts WHERE submission_id=%s", (submission_id,)
            ):
                raise ProviderUnavailable("PROVIDER_DUPLICATE_ATTEMPT")
            now = await db_time(conn)
            cost_day, rate_day, reservation = await self._capacity(conn, now)
        except ProviderUnavailable:
            raise Problem(503, "MODEL_NOT_READY", retry_after=60) from None
        await self._insert_reservation(conn, submission_id, now, cost_day, rate_day, reservation)

    async def _insert_reservation(self, conn, submission_id, now, cost_day, rate_day, reservation):
        await conn.execute(
            """INSERT INTO moderation_provider_attempts(submission_id,release_id,profile,status,
            input_token_reservation,output_token_reservation,reserved_micro_usd,reserved_at,cost_day,rate_day)
            VALUES(%s,%s,%s,'RESERVED',%s,%s,%s,%s,%s,%s)""",
            (
                submission_id,
                self.settings.moderation_policy_version,
                self.settings.moderation_profile,
                self.settings.provider_max_input_tokens,
                self.settings.provider_max_output_tokens,
                reservation,
                now,
                cost_day,
                rate_day,
            ),
        )

    async def begin_dispatch(self, submission_id, run_token, release_id):
        """Commit STARTED before execution. A lost acknowledgement never permits retry."""
        async with self.db.connection() as conn, conn.transaction():
            control = await self.lock_control(conn)
            self._require_transport()
            if control["mode"] != "OPEN":
                raise ProviderUnavailable("PROVIDER_UNAVAILABLE")
            row = await one(
                conn, "SELECT * FROM moderation_provider_attempts WHERE submission_id=%s FOR UPDATE", (submission_id,)
            )
            if (
                row is None
                or row["status"] != "RESERVED"
                or row["release_id"] != release_id
                or release_id != self.settings.moderation_policy_version
                or row["profile"] != self.settings.moderation_profile
            ):
                raise ProviderUnavailable("PROVIDER_DISPATCH_FORBIDDEN")
            source = await one(
                conn,
                "SELECT moderation_status,processing_run_token,processing_lease_until,expires_at FROM submissions WHERE id=%s",
                (submission_id,),
            )
            now = await db_time(conn)
            if (
                not source
                or source["moderation_status"] != "PROCESSING"
                or source["processing_run_token"] != run_token
                or min(source["processing_lease_until"], source["expires_at"])
                <= now + timedelta(seconds=self.settings.provider_wrapper_timeout_seconds + 2)
            ):
                raise ProviderUnavailable("PROVIDER_DISPATCH_FORBIDDEN")
            cost_day, rate_day, _ = await self._capacity(conn, now, exclude=submission_id)
            await conn.execute(
                """UPDATE moderation_provider_attempts SET status='STARTED',run_token=%s,
                started_at=%s,cost_day=%s,rate_day=%s WHERE submission_id=%s""",
                (run_token, now, cost_day, rate_day, submission_id),
            )

    async def _settle(self, conn, row, now, usage, success, error_code, failure_kind=None, retry_after_seconds=None):
        valid = (
            isinstance(usage, dict)
            and set(usage) == {"input_tokens", "output_tokens"}
            and all(type(value) is int and value >= 0 for value in usage.values())
        )
        overrun = valid and (
            usage["input_tokens"] > row["input_token_reservation"]
            or usage["output_tokens"] > row["output_token_reservation"]
        )
        charge = token_cost(self.settings, **usage) if valid else row["reserved_micro_usd"]
        status = "COMPLETED" if valid else "UNKNOWN"
        code = (
            "OK"
            if success
            else (
                error_code
                if error_code
                in {
                    "MODEL_NOT_READY",
                    "INVALID_MODEL_OUTPUT",
                    "TRANSIENT_DEPENDENCY",
                    "INFERENCE_TIMEOUT",
                    "INVALID_PROVIDER_OUTPUT",
                    "WORKER_LOST",
                    "PROVIDER_CAPACITY_UNAVAILABLE",
                }
                else "INTERNAL_ERROR"
            )
        )
        provider_codes = {
            "AUTH": "PROVIDER_AUTH_ERROR",
            "INVALID_OUTPUT": "INVALID_PROVIDER_OUTPUT",
            "RATE_LIMIT": "PROVIDER_RATE_LIMITED",
            "UNAVAILABLE": "PROVIDER_UNAVAILABLE",
            "SAFETY": "PROVIDER_SAFETY_BLOCK",
            "TIMEOUT": "PROVIDER_TIMEOUT",
            "INPUT_LIMIT": "PROVIDER_INPUT_LIMIT",
        }
        if not success and failure_kind in provider_codes:
            code = provider_codes[failure_kind]
        await conn.execute(
            """UPDATE moderation_provider_attempts SET status=%s,charged_micro_usd=%s,
            actual_input_tokens=%s,actual_output_tokens=%s,completed_at=%s,outcome_code=%s
            WHERE submission_id=%s""",
            (
                status,
                charge,
                usage["input_tokens"] if valid else None,
                usage["output_tokens"] if valid else None,
                now,
                code,
                row["submission_id"],
            ),
        )
        await conn.execute(
            """INSERT INTO moderation_provider_daily(date,charged_micro_usd,calls,input_tokens,output_tokens)
            VALUES(%s,%s,%s,%s,%s) ON CONFLICT(date) DO UPDATE SET
            charged_micro_usd=moderation_provider_daily.charged_micro_usd+excluded.charged_micro_usd,
            calls=moderation_provider_daily.calls+excluded.calls,input_tokens=moderation_provider_daily.input_tokens+excluded.input_tokens,
            output_tokens=moderation_provider_daily.output_tokens+excluded.output_tokens,updated_at=clock_timestamp()""",
            (
                row["cost_day"],
                charge,
                2 if row["profile"] == "gemini" else 1,
                usage["input_tokens"] if valid else row["input_token_reservation"],
                usage["output_tokens"] if valid else row["output_token_reservation"],
            ),
        )
        control = await one(conn, "SELECT * FROM moderation_provider_control WHERE id='gemini'")
        failures = 0 if success else control["consecutive_failures"] + (failure_kind != "SAFETY")
        recent = await one(
            conn,
            """SELECT count(*) FILTER(WHERE outcome_code NOT IN ('OK','PROVIDER_SAFETY_BLOCK')) AS failures FROM
            (SELECT outcome_code FROM moderation_provider_attempts WHERE completed_at IS NOT NULL
             AND completed_at>=%s AND status IN ('COMPLETED','UNKNOWN')
             ORDER BY completed_at DESC LIMIT 20) recent""",
            (control["closed_at"],),
        )
        invalid_cooldown = retry_after_seconds is not None and (
            type(retry_after_seconds) is not int or not 0 <= retry_after_seconds <= 2147483647
        )
        manual_fault = (
            overrun
            or code == "MODEL_NOT_READY"
            or failure_kind in {"AUTH", "INVALID_OUTPUT", "INPUT_LIMIT"}
            or invalid_cooldown
        )
        should_open = manual_fault or failure_kind == "RATE_LIMIT" or failures >= 3 or recent["failures"] >= 5
        cooldown = max(60, retry_after_seconds or 0) if not invalid_cooldown else 60
        await conn.execute(
            """UPDATE moderation_provider_control SET consecutive_failures=%s,
            mode=CASE WHEN mode='PAUSED' OR %s THEN 'PAUSED' WHEN %s THEN 'OPEN_CIRCUIT' ELSE mode END,
            opened_until=CASE WHEN %s THEN %s+(%s * interval '1 second') ELSE opened_until END,
            probe_successes=CASE WHEN %s THEN 0 ELSE probe_successes END,
            generation=generation+1,updated_at=%s WHERE id='gemini'""",
            (failures, manual_fault, should_open, should_open, now, cooldown, should_open, now),
        )
        return valid and not overrun

    async def finish_dispatch(
        self,
        submission_id,
        run_token,
        *,
        usage=None,
        success=False,
        error_code=None,
        failure_kind=None,
        retry_after_seconds=None,
    ):
        async with self.db.connection() as conn, conn.transaction():
            await self.lock_control(conn)
            row = await one(
                conn, "SELECT * FROM moderation_provider_attempts WHERE submission_id=%s FOR UPDATE", (submission_id,)
            )
            if row is None or row["status"] != "STARTED" or row["run_token"] != run_token:
                return
            await self._settle(
                conn, row, await db_time(conn), usage, success, error_code, failure_kind, retry_after_seconds
            )

    async def set_paused(self, paused: bool, actor=None, auth=None):
        if not self.enabled:
            return {"enabled": False, "mode": "LEGACY"}
        async with self.db.connection() as conn, conn.transaction():
            control = await self.lock_control(conn)
            current = None
            if actor is not None:
                if auth is None:
                    raise Problem(403, "FORBIDDEN")
                current = await auth.require_operator(actor, ("ADMIN",), conn=conn, write=True)
            changed = False
            if paused and control["mode"] != "PAUSED":
                await conn.execute("""UPDATE moderation_provider_control SET mode='PAUSED',
                    generation=generation+1,probe_successes=0,updated_at=clock_timestamp() WHERE id='gemini'""")
                changed = True
            elif not paused and control["mode"] == "PAUSED":
                # Explicit resume permits probes, never bypasses health checks.
                await conn.execute("""UPDATE moderation_provider_control SET mode='OPEN_CIRCUIT',
                    opened_until=clock_timestamp(),generation=generation+1,probe_successes=0,
                    last_probe_at=NULL,updated_at=clock_timestamp() WHERE id='gemini'""")
                changed = True
            if changed and current is not None:
                await conn.execute(
                    """INSERT INTO operator_audit_logs(operator_id,action,board_id,reason_code)
                    VALUES(%s,%s,%s,'MAINTENANCE')""",
                    (current.id, "PAUSE_PROVIDER" if paused else "RESUME_PROVIDER", self.settings.board_id),
                )
        return await self.summary()

    async def summary(self):
        if not self.enabled:
            return {"enabled": False, "mode": "LEGACY"}
        async with self.db.connection() as conn, conn.transaction():
            control = await self.lock_control(conn)
            now = await db_time(conn)
            budget = await one(
                conn,
                """SELECT COALESCE((SELECT charged_micro_usd FROM moderation_provider_daily WHERE date=%s),0) AS charged,
                COALESCE(sum(reserved_micro_usd) FILTER(WHERE status IN ('RESERVED','STARTED')),0) AS reserved,
                count(*) FILTER(WHERE status='UNKNOWN') AS unknown FROM moderation_provider_attempts WHERE cost_day=%s""",
                (business_day(now), business_day(now)),
            )
            last_failure = await one(
                conn,
                """SELECT outcome_code FROM moderation_provider_attempts
                WHERE outcome_code IN ('PROVIDER_AUTH_ERROR','PROVIDER_RATE_LIMITED','INVALID_PROVIDER_OUTPUT',
                'PROVIDER_TIMEOUT','PROVIDER_SAFETY_BLOCK','PROVIDER_UNAVAILABLE','PROVIDER_INPUT_LIMIT',
                'PROVIDER_TARIFF_EXPIRED','INFERENCE_TIMEOUT','WORKER_LOST','INTERNAL_ERROR',
                'INVALID_MODEL_OUTPUT','TRANSIENT_DEPENDENCY','MODEL_NOT_READY')
                ORDER BY completed_at DESC NULLS LAST LIMIT 1""",
            )
            last_failure_code = last_failure["outcome_code"] if last_failure else None
            latest = await one(
                conn,
                """SELECT outcome_code FROM moderation_provider_attempts
                WHERE status IN ('COMPLETED','UNKNOWN') ORDER BY completed_at DESC LIMIT 1""",
            )
            if latest and latest["outcome_code"] == "OK" and control["mode"] != "PAUSED":
                last_failure_code = None
            worker = await one(
                conn,
                """SELECT EXISTS(SELECT 1 FROM worker_readiness WHERE
                worker_name='moderation' AND model_bundle_version=%s AND lease_until>clock_timestamp()) AS ready""",
                (self.settings.model_bundle_version,),
            )
            if self.settings.moderation_profile == "gemini":
                try:
                    self._require_transport(now)
                except ProviderUnavailable as error:
                    if str(error) in {"PROVIDER_TARIFF_EXPIRED", "MODEL_NOT_READY"}:
                        last_failure_code = str(error)
            return {
                "enabled": True,
                "profile": self.settings.moderation_profile,
                "transportConfigured": self.settings.moderation_profile == "gemini"
                and self.settings.gemini_api_key is not None,
                "syntheticAccounting": self.settings.moderation_profile == "gemini_mock",
                "lastFailureCode": last_failure_code,
                "workerReady": worker["ready"],
                "mode": control["mode"],
                "releaseId": self.settings.moderation_policy_version,
                "consecutiveFailures": control["consecutive_failures"],
                "budgetDay": str(business_day(now)),
                "chargedMicroUsd": budget["charged"],
                "reservedMicroUsd": budget["reserved"],
                "dailyBudgetMicroUsd": self.settings.provider_daily_budget_micro_usd,
                "unknownAttempts": budget["unknown"],
                "probeSuccesses": control["probe_successes"],
            }

    async def recover_stale(self):
        """Cancel unstarted expired reservations; unknown started calls stay charged."""
        if not self.enabled:
            return 0
        async with self.db.connection() as conn, conn.transaction():
            await self.lock_control(conn)
            now = await db_time(conn)
            rows = await all_rows(
                conn,
                """SELECT a.* FROM moderation_provider_attempts a
                LEFT JOIN submissions s ON s.id=a.submission_id WHERE a.status IN ('RESERVED','STARTED')
                AND ((s.id IS NULL AND a.status='RESERVED') OR s.moderation_status NOT IN ('PENDING','PROCESSING') OR s.expires_at<=%s
                    OR (a.status='STARTED' AND a.started_at<=%s-interval '15 seconds'))
                ORDER BY a.reserved_at LIMIT 100 FOR UPDATE OF a""",
                (now, now),
            )
            for row in rows:
                if row["status"] == "STARTED":
                    await self._settle(conn, row, now, None, False, "WORKER_LOST")
                else:
                    await conn.execute(
                        """UPDATE moderation_provider_attempts SET status='CANCELLED',
                        charged_micro_usd=0,completed_at=%s,outcome_code='NOT_DISPATCHED' WHERE submission_id=%s""",
                        (now, row["submission_id"]),
                    )
            # Daily aggregates have already recorded every settled/unknown charge.
            await conn.execute(
                "DELETE FROM moderation_provider_attempts WHERE completed_at<%s-interval '7 days'", (now,)
            )
            await conn.execute("DELETE FROM moderation_provider_daily WHERE date<%s-90", (business_day(now),))
            return len(rows)

    async def probe(self, analyzer, *, startup=False):
        """Use synthetic text, the actual analyzer and the normal durable budget."""
        if self.settings.moderation_profile not in {"gemini_mock", "gemini"}:
            return False
        async with self.db.connection() as conn, conn.transaction():
            control = await self.lock_control(conn)
            now = await db_time(conn)
            self._require_transport(now)
            healthy_startup = startup and control["mode"] == "OPEN"
            if not healthy_startup and (
                control["mode"] not in {"OPEN_CIRCUIT", "HALF_OPEN"}
                or (control["opened_until"] and control["opened_until"] > now)
            ):
                return False
            if (
                not healthy_startup
                and control["last_probe_at"]
                and control["last_probe_at"] > now - timedelta(seconds=30)
            ):
                return False
            try:
                cost_day, rate_day, reservation = await self._capacity(conn, now)
            except ProviderUnavailable:
                return False
            probe_id, token = uuid4(), uuid4()
            await self._insert_reservation(conn, probe_id, now, cost_day, rate_day, reservation)
            await conn.execute(
                "UPDATE moderation_provider_attempts SET status='STARTED',started_at=%s,run_token=%s WHERE submission_id=%s",
                (now, token, probe_id),
            )
            generation = control["generation"] + 1
            await conn.execute(
                """UPDATE moderation_provider_control SET mode='HALF_OPEN',last_probe_at=%s,
                generation=%s,updated_at=%s WHERE id='gemini'""",
                (now, generation, now),
            )
        from inference.contracts import AnalysisInput, AnalysisResult
        from inference.provider_moderation import ProviderModerationError

        timed_out = False
        failure_kind, retry_after_seconds = None, None
        try:
            import asyncio

            result = await asyncio.wait_for(
                asyncio.to_thread(analyzer.analyze_moderated, AnalysisInput(text="오늘 너무 우울해")),
                timeout=self.settings.provider_timeout_seconds,
            )
            parsed = AnalysisResult.model_validate(result)
            success = (
                parsed.safety.verdict == "ALLOW"
                and parsed.model_bundle_version == self.settings.model_bundle_version
                and parsed.inference_policy_version == self.settings.inference_policy_version
            )
            if not success:
                failure_kind = "INVALID_OUTPUT"
        except TimeoutError:
            timed_out, success = True, False
            failure_kind = "TIMEOUT"
        except ProviderModerationError as error:
            success = False
            failure_kind = getattr(error, "failure_kind", None)
            retry_after_seconds = getattr(error, "retry_after_seconds", None)
        except Exception:
            success = False
            failure_kind = "INVALID_OUTPUT"
        async with self.db.connection() as conn, conn.transaction():
            control = await self.lock_control(conn)
            row = await one(
                conn, "SELECT * FROM moderation_provider_attempts WHERE submission_id=%s FOR UPDATE", (probe_id,)
            )
            if row["status"] == "STARTED":
                settled = await self._settle(
                    conn,
                    row,
                    await db_time(conn),
                    None if timed_out else getattr(analyzer, "last_usage", None),
                    success,
                    "INVALID_PROVIDER_OUTPUT",
                    failure_kind,
                    retry_after_seconds,
                )
                success = success and settled
            else:
                success = False
            # _settle increments generation; reject any intervening control action.
            settled_control = await one(conn, "SELECT mode FROM moderation_provider_control WHERE id='gemini'")
            if (
                control["generation"] == generation
                and control["mode"] == "HALF_OPEN"
                and settled_control["mode"] != "PAUSED"
            ):
                successes = (2 if healthy_startup else control["probe_successes"] + 1) if success else 0
                failures = control["consecutive_failures"] if success else control["consecutive_failures"] + 1
                backoff = min(900, 60 * 2 ** min(4, max(0, failures - 3)))
                if failure_kind == "RATE_LIMIT" and type(retry_after_seconds) is int:
                    backoff = max(backoff, retry_after_seconds)
                await conn.execute(
                    """UPDATE moderation_provider_control SET mode=%s,probe_successes=%s,
                    consecutive_failures=%s,
                    opened_until=CASE WHEN %s THEN NULL ELSE clock_timestamp()+(%s * interval '1 second') END,
                    closed_at=CASE WHEN %s THEN clock_timestamp() ELSE closed_at END,
                    generation=generation+1,updated_at=clock_timestamp() WHERE id='gemini'""",
                    (
                        "OPEN" if successes >= 2 else ("HALF_OPEN" if success else "OPEN_CIRCUIT"),
                        successes,
                        0 if successes >= 2 else failures,
                        success,
                        backoff,
                        successes >= 2,
                    ),
                )
        if timed_out:
            raise ProviderProbeTimeout("INFERENCE_TIMEOUT")
        return success
