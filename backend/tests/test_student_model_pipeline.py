"""Opt-in real model + HTTP + database path, without approving v0 safety."""

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import UUID

import pytest

from app.db import one
from app.profanity import ProfanityFilter
from app.worker import Runtime, run_submission

pytestmark = pytest.mark.integration


@pytest.fixture
def settings(settings, real_student_analyzer):
    return settings.model_copy(
        update={
            "model_bundle_version": real_student_analyzer.model_bundle_version,
            "inference_policy_version": real_student_analyzer.inference_policy_version,
            "analyzer": "inference.student_model:create_analyzer",
            "model_manifest_path": os.environ["MOODWALL_TEST_MODEL_MANIFEST"],
        }
    )


async def test_real_model_result_reaches_receipt_without_display_or_strike(
    clients, system, database, settings, real_student_analyzer
):
    response = await clients["student"].post(
        "/api/submissions",
        json={"content": "합격 진심으로 축하해!", "emojiId": "party"},
        headers={"Idempotency-Key": "12345678-1234-4234-8234-123456789abc"},
    )
    assert response.status_code == 202, response.text
    submission_id = UUID(response.json()["id"])
    runtime = Runtime(database, settings, system.state.moderation, real_student_analyzer, ProfanityFilter())
    await run_submission(runtime, submission_id)
    receipt = (await clients["student"].get(f"/api/submissions/{submission_id}")).json()
    assert receipt["moderationStatus"] == "NEEDS_REWRITE"
    assert "reason_codes" not in receipt and "model_bundle_version" not in receipt
    async with database.connection() as conn:
        row = await one(conn, "SELECT * FROM submissions WHERE id=%s", (submission_id,))
    assert row["safety_verdict"] == "UNCERTAIN"
    assert row["reason_codes"] == ["INSUFFICIENT_CONTEXT"]
    assert row["model_bundle_version"] == real_student_analyzer.model_bundle_version
    assert row["inference_policy_version"] == real_student_analyzer.inference_policy_version
    assert row["attempt_count"] == 1 and not row["strike_applied"]
    assert row["error_code"] is None and row["emotion"] is None and row["display_status"] == "NONE"
    usage = (await clients["student"].get("/api/me/usage")).json()
    assert usage["usedCount"] == 0 and usage["violationCount"] == 0
    assert not (await clients["display"].get("/api/display/state")).json()["bubbles"]


async def test_confirmed_lexicon_violation_skips_real_inference(
    clients, system, database, settings, real_student_analyzer
):
    response = await clients["student"].post(
        "/api/submissions",
        json={"content": "씨%발", "emojiId": "party"},
        headers={"Idempotency-Key": "22345678-1234-4234-8234-123456789abc"},
    )
    assert response.status_code == 202, response.text
    analyzer = SimpleNamespace(analyze=Mock(wraps=real_student_analyzer.analyze))
    runtime = Runtime(database, settings, system.state.moderation, analyzer, ProfanityFilter())
    submission_id = UUID(response.json()["id"])
    await run_submission(runtime, submission_id)
    analyzer.analyze.assert_not_called()
    receipt = (await clients["student"].get(f"/api/submissions/{submission_id}")).json()
    assert receipt["moderationStatus"] == "REJECTED"
    usage = (await clients["student"].get("/api/me/usage")).json()
    assert usage["violationCount"] == 1 and usage["usedCount"] == 0


@pytest.mark.skipif(sys.platform != "linux", reason="the worker supervisor requires Linux")
async def test_real_linux_worker_loads_bundle_and_consumes_the_committed_job(clients, database, settings):
    async with database.connection() as conn:
        await conn.execute("DELETE FROM worker_readiness")
    environment = dict(os.environ)
    for key, value in settings.model_dump().items():
        name = "MOODWALL_" + key.upper()
        if value is None:
            environment.pop(name, None)
        else:
            environment[name] = json.dumps(value) if isinstance(value, (dict, list, bool)) else str(value)
    worker = subprocess.Popen(
        [sys.executable, "-m", "app.worker"],
        cwd=Path(__file__).resolve().parents[1],
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + settings.worker_warmup_seconds + 10
        while True:
            assert worker.poll() is None, "real model worker exited before becoming ready"
            async with database.connection() as conn:
                ready = await one(
                    conn,
                    """SELECT generation FROM worker_readiness WHERE worker_name='moderation'
                    AND model_bundle_version=%s AND lease_until>clock_timestamp()""",
                    (settings.model_bundle_version,),
                )
            if ready:
                break
            assert time.monotonic() < deadline, "real model worker did not become ready within startup budget"
            await asyncio.sleep(0.1)
        response = await clients["student"].post(
            "/api/submissions",
            json={"content": "오늘 점심이 맛있어서 행복해", "emojiId": "party"},
            headers={"Idempotency-Key": "32345678-1234-4234-8234-123456789abc"},
        )
        assert response.status_code == 202, response.text
        submission_id = UUID(response.json()["id"])
        deadline = time.monotonic() + 30
        while True:
            assert worker.poll() is None, "real model worker exited while processing the committed job"
            async with database.connection() as conn:
                row = await one(conn, "SELECT * FROM submissions WHERE id=%s", (submission_id,))
            if row["moderation_status"] not in {"PENDING", "PROCESSING"}:
                break
            assert time.monotonic() < deadline, "committed real model job did not finish"
            await asyncio.sleep(0.1)
        assert row["moderation_status"] == "NEEDS_REWRITE"
        assert row["safety_verdict"] == "UNCERTAIN" and row["reason_codes"] == ["INSUFFICIENT_CONTEXT"]
        assert row["model_bundle_version"] == settings.model_bundle_version
        assert row["attempt_count"] == 1 and not row["strike_applied"]
        assert not (await clients["display"].get("/api/display/state")).json()["bubbles"]
    finally:
        if worker.poll() is None:
            worker.terminate()
            try:
                await asyncio.to_thread(worker.wait, 15)
            except subprocess.TimeoutExpired:
                worker.kill()
        await asyncio.to_thread(worker.wait, 5)
    assert worker.returncode == 0
    async with database.connection() as conn:
        assert await one(conn, "SELECT generation FROM worker_readiness WHERE worker_name='moderation'") is None
