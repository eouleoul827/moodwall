"""Regression coverage for pre-model failures and the Linux process boundary."""

from __future__ import annotations

import asyncio
import ctypes
import multiprocessing
import os
import subprocess
import sys
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from psycopg import OperationalError

from app.profanity import ProfanityFilter
from app.worker import Runtime, configure_child_subreaper, run_submission, terminate_process_group
from inference.fake import FakeAnalyzer


@asynccontextmanager
async def _connection():
    yield None


def _runtime(claim_results):
    analyzer = FakeAnalyzer("fake-v1", "inference-v1")
    analyzer.analyze = Mock(wraps=analyzer.analyze)
    service = SimpleNamespace(
        claim=AsyncMock(side_effect=claim_results),
        finalize=AsyncMock(),
        retry_before_model=AsyncMock(),
    )
    return Runtime(SimpleNamespace(connection=_connection), None, service, analyzer, ProfanityFilter())


async def test_transient_initial_claim_is_retried_before_model_once(monkeypatch):
    now = datetime.now(UTC)
    claim = {
        "content": "합격 축하해",
        "processing_run_token": uuid4(),
        "processing_lease_until": now + timedelta(seconds=60),
    }
    runtime = _runtime([OperationalError("synthetic rollback"), claim])
    monkeypatch.setattr("app.worker.db_time", AsyncMock(return_value=now))

    await run_submission(runtime, uuid4())

    assert runtime.service.claim.await_count == 2
    assert runtime.analyzer.analyze.call_count == 1
    runtime.service.finalize.assert_awaited_once()


async def test_uncertain_committed_claim_never_restarts_model():
    # A committed first claim is PROCESSING, so the service's second claim
    # returns None. The supervisor must not manufacture a replacement token.
    runtime = _runtime([OperationalError("synthetic lost acknowledgement"), None])

    await run_submission(runtime, uuid4())

    assert runtime.service.claim.await_count == 2
    runtime.analyzer.analyze.assert_not_called()
    runtime.service.finalize.assert_not_awaited()


async def test_initial_claim_retry_is_bounded():
    runtime = _runtime([OperationalError("first"), OperationalError("second")])

    with pytest.raises(OperationalError, match="second"):
        await run_submission(runtime, uuid4())

    assert runtime.service.claim.await_count == 2
    runtime.analyzer.analyze.assert_not_called()


def _model_leader_with_helper(control, exit_early):
    os.setsid()
    helper = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import signal,time\n"
            "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            "print('ready', flush=True)\n"
            "while True: time.sleep(0.01)\n",
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    assert helper.stdout.readline() == b"ready\n"
    control.send(helper.pid)
    control.close()
    if exit_early:
        os._exit(0)
    while True:
        time.sleep(0.01)


@pytest.mark.skipif(sys.platform != "linux", reason="Linux process groups and subreaper required")
@pytest.mark.parametrize("exit_early", [False, True])
async def test_kill_boundary_reaps_term_resistant_helper_after_leader_exits(exit_early):
    libc = ctypes.CDLL(None, use_errno=True)
    previous = ctypes.c_int()
    assert libc.prctl(37, ctypes.byref(previous), 0, 0, 0) == 0  # PR_GET_CHILD_SUBREAPER
    configure_child_subreaper()
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe(duplex=False)
    leader = context.Process(target=_model_leader_with_helper, args=(child, exit_early))
    leader.start()
    child.close()
    try:
        assert await asyncio.to_thread(parent.poll, 10), "helper did not become ready"
        helper_pid = parent.recv()
        assert Path(f"/proc/{helper_pid}").exists()
        if exit_early:
            await asyncio.to_thread(leader.join, 5)
            assert not leader.is_alive()

        await terminate_process_group(leader, grace=0.15)

        assert not leader.is_alive()
        assert not Path(f"/proc/{helper_pid}").exists(), "helper survived or was not reaped"
        with pytest.raises(ProcessLookupError):
            os.killpg(leader.pid, 0)
    finally:
        parent.close()
        try:
            await terminate_process_group(leader, grace=0)
        finally:
            libc.prctl(36, previous.value, 0, 0, 0)
