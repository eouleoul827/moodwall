"""Actual Linux process-boundary test against the isolated integration DB."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from app.db import db_time, one
from app.domain import business_day, midnight_after
from app.moderation import ModerationService
from app.queue import enqueue

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(sys.platform != "linux", reason="Linux process groups and /proc required"),
]


def _environment(settings):
    result = dict(os.environ)
    for key, value in settings.model_dump().items():
        name = "MOODWALL_" + key.upper()
        if value is None:
            result.pop(name, None)
        elif isinstance(value, (dict, list, bool)):
            result[name] = json.dumps(value)
        else:
            result[name] = str(value)
    return result


def _start(settings):
    return subprocess.Popen(
        [sys.executable, "-m", "app.worker"],
        cwd=Path(__file__).resolve().parents[1],
        env=_environment(settings),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _model_children(parent_pid):
    children = Path(f"/proc/{parent_pid}/task/{parent_pid}/children").read_text().split()
    found = []
    for child in children:
        try:
            command = Path(f"/proc/{child}/cmdline").read_bytes()
        except FileNotFoundError:
            continue
        if b"spawn_main" in command:
            found.append(int(child))
    return found


async def _until(check, *, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = await check()
        if value:
            return value
        await asyncio.sleep(0.05)
    raise AssertionError("worker integration condition timed out")


async def _stop(process):
    if process is None:
        return
    if process.poll() is None:
        process.terminate()
        try:
            await asyncio.to_thread(process.wait, 12)
        except subprocess.TimeoutExpired:
            process.kill()
    await asyncio.to_thread(process.wait, 5)


async def _insert_job(database, settings, content):
    participant, submission = uuid4(), uuid4()
    async with database.connection() as conn:
        async with conn.transaction():
            now = await db_time(conn)
            await conn.execute("INSERT INTO participants(id) VALUES(%s)", (participant,))
            await conn.execute(
                "INSERT INTO daily_usage(participant_id,business_date) VALUES(%s,%s)", (participant, business_day(now))
            )
            await conn.execute(
                """INSERT INTO submissions(id,participant_id,business_date,board_id,idempotency_key,
                request_hash,content,emoji_id,accepted_at,expires_at,moderation_policy_version,lexicon_version)
                VALUES(%s,%s,%s,%s,%s,%s,%s,'party',%s,%s,%s,%s)""",
                (
                    submission,
                    participant,
                    business_day(now),
                    settings.board_id,
                    uuid4(),
                    bytes(32),
                    content,
                    now,
                    min(now + timedelta(seconds=120), midnight_after(now)),
                    settings.moderation_policy_version,
                    settings.lexicon_version,
                ),
            )
            job_id = await enqueue(conn, submission)
            await conn.execute("UPDATE submissions SET queue_job_id=%s WHERE id=%s", (job_id, submission))
    return submission


async def test_supervisor_kills_hung_model_reaps_child_and_next_worker_processes(database, settings, system):
    settings = settings.model_copy(update={"inference_timeout_seconds": 3, "worker_warmup_seconds": 30})
    async with database.connection() as conn:
        await conn.execute("DELETE FROM worker_readiness")
    first = second = None

    async def ready(process):
        assert process.poll() is None, "worker supervisor exited before READY"
        async with database.connection() as conn:
            return await one(
                conn,
                """SELECT generation FROM worker_readiness WHERE worker_name='moderation'
                AND model_bundle_version=%s AND lease_until>clock_timestamp()""",
                (settings.model_bundle_version,),
            )

    async def state(submission_id, status):
        async with database.connection() as conn:
            return await one(
                conn, "SELECT * FROM submissions WHERE id=%s AND moderation_status=%s", (submission_id, status)
            )

    try:
        first = _start(settings)
        first_readiness = await _until(lambda: ready(first), timeout=30)
        model_pids = _model_children(first.pid)
        assert len(model_pids) == 1, "expected exactly one independent model child"
        hung_id = await _insert_job(database, settings, "[fixture:hang]")
        await _until(lambda: state(hung_id, "PROCESSING"))

        # This observes a real supervisor process exit and /proc disappearance,
        # not an asyncio timeout or a cancelled thread pretending to terminate.
        exit_code = await asyncio.to_thread(first.wait, 15)
        assert exit_code == 1
        assert all(not Path(f"/proc/{pid}").exists() for pid in model_pids), "model child was not reaped"
        await ModerationService(database, settings).expire_due()
        async with database.connection() as conn:
            hung = await one(conn, "SELECT moderation_status,strike_applied FROM submissions WHERE id=%s", (hung_id,))
            readiness = await one(conn, "SELECT generation FROM worker_readiness WHERE worker_name='moderation'")
        assert hung["moderation_status"] in {"FAILED", "EXPIRED"}
        assert not hung["strike_applied"]
        assert readiness is None

        second = _start(settings)
        second_readiness = await _until(lambda: ready(second), timeout=30)
        assert first_readiness["generation"] != second_readiness["generation"]
        normal_id = await _insert_job(database, settings, "합격 축하해!")
        approved = await _until(lambda: state(normal_id, "APPROVED"))
        assert approved["display_status"] == "FLOATING"
        assert approved["attempt_count"] == 1 and not approved["strike_applied"]
        assert approved["emotion"] == "CELEBRATION"
    finally:
        await _stop(first)
        await _stop(second)
