"""Obsolete duration settings must not break startup after the fixed-time update."""

import pytest

from app.config import Settings


@pytest.mark.parametrize(
    "values",
    [("8", "5", "0"), ("5", "15", "0"), ("obsolete", "obsolete", "obsolete")],
)
def test_old_duration_environment_is_ignored(monkeypatch, values):
    names = ("PLAYBACK_MAX_SECONDS", "PLAYBACK_MIN_SECONDS", "PLAYBACK_RELAXED_BACKLOG")
    for name, value in zip(names, values, strict=True):
        monkeypatch.setenv(f"MOODWALL_{name}", value)
    settings = Settings(
        _env_file=None,
        database_url="postgresql://unused@localhost/unused_test",
        secret_key="synthetic-test-session-key-01234567890123456789",
        qr_key="synthetic-test-qr-key-01234567890123456789012345",
    )
    assert not any(name.lower() in settings.model_dump() for name in names)
