"""Linux worker supervisor with a real process kill boundary around inference.

Run from backend/: python -m app.worker
The API process never imports or loads the configured model.
"""

from __future__ import annotations

import asyncio
import ctypes
import logging
import multiprocessing
import os
import signal
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from psycopg import OperationalError
from pydantic import ValidationError

from app.config import Settings
from app.db import Database, db_time, one
from app.moderation import ModerationService
from app.profanity import ProfanityFilter
from app.provider_control import ProviderControlService, ProviderProbeTimeout, ProviderUnavailable
from app.queue import configure_processor, create_queue
from inference.contracts import AnalysisInput, AnalysisResult, ModelAnalyzed, RuleRejected, RunFailed
from inference.loader import load_analyzer
from inference.provider_moderation import ProviderModerationError

log = logging.getLogger("moodwall.worker")


@dataclass
class Runtime:
    db: Database
    settings: Settings
    service: ModerationService
    analyzer: object
    filter: ProfanityFilter
    control: object | None = None
    provider: object | None = None
    inference_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    poisoned: bool = False


_runtime: Runtime | None = None


async def run_submission(runtime: Runtime, submission_id):
    if runtime.poisoned:
        return
    # A failed claim transaction has not started inference. Retry once, but
    # always claim through the service again: an uncertain committed claim is
    # already PROCESSING and must never authorize a second model execution.
    for attempt in range(2):
        try:
            claim = await runtime.service.claim(submission_id)
            break
        except OperationalError:
            if attempt:
                raise
            await asyncio.sleep(0.1)
    if claim is None:
        return
    run_token = str(claim["processing_run_token"])
    inference_started = False
    control_started = False
    fatal_filter = False
    fatal_provider = False
    provider_started = False
    provider_usage = None
    provider_failure_kind, provider_retry_after = None, None
    provider_mode = getattr(runtime.settings, "moderation_profile", "legacy") != "legacy"
    try:
        async with runtime.db.connection() as conn:
            remaining = (claim["processing_lease_until"] - await db_time(conn)).total_seconds()
        if runtime.control:
            deadline = time.monotonic() + max(0, remaining)
            runtime.control.send(
                {"type": "START", "submission_id": str(submission_id), "run_token": run_token, "deadline": deadline}
            )
            control_started = True
            acknowledgement = await asyncio.to_thread(runtime.control.recv)
            if acknowledgement != {"type": "ACK", "run_token": run_token}:
                raise RuntimeError("supervisor acknowledgement missing")
        try:
            scan = await asyncio.to_thread(runtime.filter.scan, claim["content"])
        except Exception:
            fatal_filter = True
            raise
        if provider_mode:
            if runtime.provider is None:
                runtime.provider = ProviderControlService(runtime.db, runtime.settings)
            # This flag also forbids pre-model replay after an uncertain ledger
            # acknowledgement. STARTED is committed before calling any analyzer.
            inference_started = True
            async with runtime.inference_lock:
                if remaining < runtime.settings.provider_wrapper_timeout_seconds + 2:
                    raise ProviderUnavailable("PROVIDER_DISPATCH_FORBIDDEN")
                await runtime.provider.begin_dispatch(
                    submission_id, claim["processing_run_token"], claim["moderation_policy_version"]
                )
                provider_started = True
                try:
                    result = await asyncio.wait_for(
                        asyncio.to_thread(
                            runtime.analyzer.analyze_moderated,
                            AnalysisInput(text=claim["content"]),
                            confirmed_profanity=scan.decision == "CONFIRMED",
                        ),
                        # The transport owns the shorter HTTP deadline. Leave
                        # the rest of the wrapper for optional local emotion.
                        timeout=runtime.settings.provider_wrapper_timeout_seconds,
                    )
                except TimeoutError:
                    # wait_for cannot kill a native thread. Withdraw READY and
                    # let the supervisor kill the process after finalization.
                    fatal_provider = True
                    runtime.poisoned = True
                    if runtime.control:
                        runtime.control.send({"type": "FAULT", "code": "INFERENCE_TIMEOUT"})
                    raise
                finally:
                    # A subsequent probe shares this analyzer. Snapshot this
                    # invocation before releasing its lock, even on errors.
                    usage = None if fatal_provider else getattr(runtime.analyzer, "last_usage", None)
                    provider_usage = usage.copy() if isinstance(usage, dict) else None
                outcome = ModelAnalyzed(analysis=AnalysisResult.model_validate(result))
        elif scan.decision == "CONFIRMED":
            outcome = RuleRejected(lexicon_version=scan.lexicon_version, matches=list(scan.matches))
        else:
            inference_started = True
            result = await asyncio.to_thread(runtime.analyzer.analyze, AnalysisInput(text=claim["content"]))
            outcome = ModelAnalyzed(analysis=AnalysisResult.model_validate(result))
    except OperationalError:
        if not inference_started and not control_started and await runtime.service.retry_before_model(claim):
            await asyncio.sleep(0.1)
            return await run_submission(runtime, submission_id)
        outcome = RunFailed(error_code="TRANSIENT_DEPENDENCY")
    except ValidationError:
        outcome = RunFailed(error_code="INVALID_MODEL_OUTPUT")
        provider_failure_kind = "INVALID_OUTPUT" if provider_mode else None
    except ProviderModerationError as error:
        outcome = RunFailed(error_code=error.error_code)
        provider_failure_kind = getattr(error, "failure_kind", None)
        provider_retry_after = getattr(error, "retry_after_seconds", None)
    except ProviderUnavailable:
        outcome = RunFailed(error_code="MODEL_NOT_READY")
    except TimeoutError:
        outcome = RunFailed(error_code="INFERENCE_TIMEOUT")
        provider_failure_kind = "TIMEOUT"
    except (EOFError, BrokenPipeError):
        # Parent died: no permission to continue inference. The parent-death
        # signal below is the final process boundary, independent of this task.
        raise
    except Exception:
        # Never dump exception bodies, input text, tokens or artifact paths.
        log.error("moderation_run_failed", extra={"submission_id": str(submission_id)})
        outcome = RunFailed(error_code="LEXICON_ERROR" if fatal_filter else "INTERNAL_ERROR")

    if provider_started:
        for attempt in range(2):
            try:
                await runtime.provider.finish_dispatch(
                    submission_id,
                    claim["processing_run_token"],
                    usage=provider_usage,
                    success=isinstance(outcome, ModelAnalyzed),
                    error_code=outcome.error_code if isinstance(outcome, RunFailed) else None,
                    failure_kind=provider_failure_kind,
                    retry_after_seconds=provider_retry_after,
                )
                break
            except Exception:
                if attempt:
                    log.error("provider_settlement_failed", extra={"submission_id": str(submission_id)})
                    outcome = RunFailed(error_code="TRANSIENT_DEPENDENCY")

    # Retry only storing this same result; inference is never rerun after an
    # uncertain DB acknowledgement. The DB token/lease still fences each retry.
    for attempt in range(2):
        try:
            await runtime.service.finalize(claim, outcome)
            break
        except Exception:
            if attempt:
                raise
            await asyncio.sleep(0.1)
    if runtime.control:
        runtime.control.send(
            {"type": "FAULT", "code": "LEXICON_ERROR" if fatal_filter else "INFERENCE_TIMEOUT"}
            if fatal_filter or fatal_provider
            else {"type": "DONE", "run_token": run_token}
        )


async def process_submission(submission_id):
    if _runtime is None:
        raise RuntimeError("worker runtime was not initialized")
    await run_submission(_runtime, submission_id)


async def _provider_maintenance(runtime):
    while not runtime.poisoned:
        try:
            await runtime.provider.recover_stale()
            # Analyzer usage telemetry belongs to this exact invocation. A
            # probe must never race the actual single-concurrency worker.
            if not runtime.inference_lock.locked():
                async with runtime.inference_lock:
                    await runtime.provider.probe(runtime.analyzer)
        except asyncio.CancelledError:
            raise
        except ProviderProbeTimeout:
            runtime.poisoned = True
            if runtime.control:
                runtime.control.send({"type": "FAULT", "code": "INFERENCE_TIMEOUT"})
            return
        except Exception:
            log.error("provider_maintenance_failed")
        await asyncio.sleep(5)


async def _bootstrap_provider(runtime):
    """Real worker READY is earned by a budgeted valid synthetic ALLOW."""
    if runtime.settings.moderation_profile != "gemini":
        return
    deadline = time.monotonic() + runtime.settings.worker_warmup_seconds
    while time.monotonic() < deadline:
        await runtime.provider.recover_stale()
        try:
            valid = await runtime.provider.probe(runtime.analyzer, startup=True)
        except ProviderProbeTimeout:
            runtime.poisoned = True
            if runtime.control:
                runtime.control.send({"type": "FAULT", "code": "INFERENCE_TIMEOUT"})
            raise
        state = await runtime.provider.summary()
        if valid and state["mode"] == "OPEN":
            return
        if state["mode"] == "PAUSED":
            raise ProviderUnavailable("MODEL_NOT_READY")
        await asyncio.sleep(min(5, max(0, deadline - time.monotonic())))
    raise ProviderUnavailable("MODEL_NOT_READY")


async def _child(settings: Settings, control):
    global _runtime
    # Loading and warmup are covered by the parent's separate startup deadline.
    analyzer = await asyncio.to_thread(load_analyzer, settings)
    path = Path(settings.lexicon_path) if getattr(settings, "lexicon_path", None) else None
    profanity = ProfanityFilter(
        path,
        expected_version=settings.lexicon_version,
        production=settings.environment == "production" and settings.moderation_profile == "legacy",
    )
    db = Database(settings.model_copy(update={"db_pool_size": 2}))
    await db.open()
    app = create_queue(settings)
    maintenance = None
    opened = False
    try:
        await app.open_async()
        opened = True
        _runtime = Runtime(db, settings, ModerationService(db, settings), analyzer, profanity, control)
        if settings.moderation_profile != "legacy":
            _runtime.provider = ProviderControlService(db, settings)
            await _bootstrap_provider(_runtime)
            maintenance = asyncio.create_task(_provider_maintenance(_runtime))
        configure_processor(lambda submission_id: run_submission(_runtime, submission_id))
        control.send({"type": "READY", "bundle": settings.model_bundle_version})
        await app.run_worker_async(
            queues=["moderation"], concurrency=1, update_heartbeat_interval=10, stalled_worker_timeout=30
        )
    finally:
        if maintenance:
            maintenance.cancel()
            await asyncio.gather(maintenance, return_exceptions=True)
        if opened:
            await app.close_async()
        await db.close()
        close_analyzer = getattr(analyzer, "close", None)
        if callable(close_analyzer) and not (_runtime and _runtime.poisoned):
            await asyncio.to_thread(close_analyzer)
        control.close()


def _child_entry(settings: Settings, control, expected_parent: int):
    if sys.platform == "linux":
        os.setsid()
        # Linux PR_SET_PDEATHSIG: an abrupt supervisor death must not leave
        # a native inference thread consuming CPU as an orphan process.
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(1, signal.SIGKILL, 0, 0, 0) != 0:
            raise OSError(ctypes.get_errno(), "parent-death signal setup failed")
        if os.getppid() != expected_parent:
            os.kill(os.getpid(), signal.SIGKILL)
    asyncio.run(_child(settings, control))


def configure_child_subreaper():
    """Adopt orphaned model helpers so the supervisor can reap its whole group."""
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(36, 1, 0, 0, 0) != 0:  # Linux PR_SET_CHILD_SUBREAPER
        raise OSError(ctypes.get_errno(), "child subreaper setup failed")


def _process_group_alive(pgid):
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    return True


def _reap_group_helpers(pgid):
    while True:
        try:
            pid, _ = os.waitpid(-pgid, os.WNOHANG)
        except ChildProcessError:
            return
        if pid == 0:
            return


async def terminate_process_group(process, grace: float = 5):
    """TERM, bounded grace, then KILL the group and reap leader and helpers."""
    if sys.platform != "linux":
        if process.is_alive():
            process.terminate()
            await asyncio.to_thread(process.join, grace)
        if process.is_alive():
            process.kill()
        await asyncio.to_thread(process.join, 5)
        if process.is_alive():
            raise RuntimeError("worker process could not be reaped")
        return

    pgid = process.pid
    deadline = time.monotonic() + grace
    try:
        # The leader may already have exited while its helpers are still alive.
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        # A signal can race the child's setsid() during very early startup.
        if process.is_alive():
            process.terminate()
    await asyncio.to_thread(process.join, grace)
    while True:
        if not process.is_alive():
            process.join()
            _reap_group_helpers(pgid)
        if not _process_group_alive(pgid) or time.monotonic() >= deadline:
            break
        await asyncio.sleep(min(0.05, max(0, deadline - time.monotonic())))
    # Escalation depends on group existence, not just leader existence. A helper
    # may ignore TERM even after the model leader has exited successfully.
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        if process.is_alive():
            process.kill()
    await asyncio.to_thread(process.join, 5)
    reap_deadline = time.monotonic() + 5
    while not process.is_alive():
        process.join()
        _reap_group_helpers(pgid)
        if not _process_group_alive(pgid):
            return
        if time.monotonic() >= reap_deadline:
            break
        await asyncio.sleep(0.05)
    raise RuntimeError("worker process group could not be reaped")


async def _publish_readiness(db, settings, generation, ready, stopping, broken, lock_conn):
    registered = False
    while not stopping.is_set():
        if ready.is_set():
            try:
                # This session owns the singleton advisory lock. If it is
                # disconnected, another supervisor may acquire that lock, so
                # the old child must stop rather than keep advertising READY.
                await lock_conn.execute("SELECT 1")
            except Exception:
                ready.clear()
                broken.set()
                stopping.set()
                log.error("worker_singleton_lock_lost")
                return
            try:
                async with db.connection() as conn:
                    if not registered:
                        await conn.execute(
                            """INSERT INTO worker_readiness(worker_name,generation,model_bundle_version,ready_at,heartbeat_at,lease_until)
                            VALUES('moderation',%s,%s,clock_timestamp(),clock_timestamp(),clock_timestamp()+interval '15 seconds')
                            ON CONFLICT(worker_name) DO UPDATE SET generation=excluded.generation,
                            model_bundle_version=excluded.model_bundle_version,ready_at=excluded.ready_at,
                            heartbeat_at=excluded.heartbeat_at,lease_until=excluded.lease_until""",
                            (generation, settings.model_bundle_version),
                        )
                        registered = True
                    else:
                        result = await conn.execute(
                            """UPDATE worker_readiness SET heartbeat_at=clock_timestamp(),
                            lease_until=clock_timestamp()+interval '15 seconds' WHERE worker_name='moderation' AND generation=%s""",
                            (generation,),
                        )
                        if result.rowcount != 1:
                            ready.clear()
                            broken.set()
                            stopping.set()
                            raise RuntimeError("worker readiness generation was replaced")
            except Exception:
                log.error("worker_readiness_refresh_failed")
        try:
            await asyncio.wait_for(stopping.wait(), timeout=5 if ready.is_set() else 0.1)
        except TimeoutError:
            pass


async def supervise(settings: Settings) -> int:
    if sys.platform != "linux":
        raise RuntimeError("the production process-group supervisor requires Linux (use Docker)")
    # Validate unsafe development choices before spawning a model process.
    if settings.analyzer == "fake" and settings.environment not in {"development", "test"}:
        raise ValueError("fake analyzer forbidden in production")
    configure_child_subreaper()
    db = Database(settings.model_copy(update={"db_pool_size": 2}))
    await db.open()
    stopping, ready, broken = asyncio.Event(), asyncio.Event(), asyncio.Event()
    generation = uuid4()
    context = multiprocessing.get_context("spawn")
    parent_control, child_control = context.Pipe()
    child = context.Process(
        target=_child_entry, args=(settings, child_control, os.getpid()), name="moodwall-model-worker"
    )
    heartbeat = None
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stopping.set)
    try:
        async with db.connection() as lock_conn:
            lock = await one(
                lock_conn, "SELECT pg_try_advisory_lock(hashtext('moodwall:moderation:supervisor')) AS acquired"
            )
            if not lock["acquired"]:
                raise RuntimeError("another moderation supervisor is active")
            child.start()
            child_control.close()
            heartbeat = asyncio.create_task(
                _publish_readiness(db, settings, generation, ready, stopping, broken, lock_conn)
            )
            startup_deadline = time.monotonic() + settings.worker_warmup_seconds
            active = None
            failure = False
            try:
                while not stopping.is_set():
                    now = time.monotonic()
                    if not child.is_alive():
                        failure = True
                        break
                    if (not ready.is_set() and now >= startup_deadline) or (active and now >= active["deadline"]):
                        ready.clear()
                        log.error("worker_hard_timeout")
                        failure = True
                        break
                    if parent_control.poll():
                        try:
                            message = parent_control.recv()
                        except EOFError:
                            failure = True
                            break
                        kind = message.get("type")
                        if (
                            kind == "READY"
                            and message.get("bundle") == settings.model_bundle_version
                            and not ready.is_set()
                        ):
                            ready.set()
                        elif kind == "START" and ready.is_set() and active is None:
                            deadline = message.get("deadline", 0)
                            if not isinstance(deadline, (int, float)) or not now < deadline <= now + 60:
                                failure = True
                                break
                            active = message
                            parent_control.send({"type": "ACK", "run_token": message["run_token"]})
                        elif kind == "DONE" and active and message.get("run_token") == active["run_token"]:
                            active = None
                        else:
                            failure = True
                            break
                    await asyncio.sleep(0.05)
            finally:
                ready.clear()
                stopping.set()
                # Start TERM immediately while withdrawing readiness; waiting
                # through the entire five-second kill grace must not leave a
                # fresh admission lease advertised for a known-bad process.
                termination = asyncio.create_task(terminate_process_group(child))
                if heartbeat:
                    await heartbeat
                try:
                    async with db.connection() as conn:
                        await conn.execute(
                            "DELETE FROM worker_readiness WHERE worker_name='moderation' AND generation=%s",
                            (generation,),
                        )
                except Exception:
                    log.error("worker_readiness_cleanup_failed")
                await termination
                await lock_conn.execute("SELECT pg_advisory_unlock(hashtext('moodwall:moderation:supervisor'))")
            return 1 if failure or broken.is_set() else 0
    finally:
        parent_control.close()
        child_control.close()
        await db.close()


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    raise SystemExit(asyncio.run(supervise(Settings())))


if __name__ == "__main__":
    main()
