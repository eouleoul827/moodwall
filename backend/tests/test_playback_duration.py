"""Server-chosen full-screen duration: pure policy and configuration bounds."""

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.domain import playback_duration_ms


@pytest.mark.parametrize(
    ("waiting", "expected"),
    [(0, 15000), (1, 15000), (2, 15000), (3, 14000), (5, 12000), (9, 8000), (10, 8000), (500, 8000)],
)
def test_short_queue_keeps_fifteen_seconds_and_bursts_shorten_to_the_floor(waiting, expected):
    assert playback_duration_ms(waiting) == expected


def test_configured_bounds_are_respected():
    assert playback_duration_ms(0, maximum=12, minimum=6, relaxed=0) == 12000
    assert playback_duration_ms(100, maximum=12, minimum=6, relaxed=0) == 6000
    assert playback_duration_ms(4, maximum=10, minimum=10, relaxed=0) == 10000


def _settings(**overrides):
    return Settings(
        _env_file=None,
        database_url="postgresql://unused@localhost/unused_test",
        secret_key="synthetic-test-session-key-01234567890123456789",
        qr_key="synthetic-test-qr-key-01234567890123456789012345",
        **overrides,
    )


def test_settings_default_and_reject_inverted_or_out_of_contract_bounds():
    settings = _settings()
    assert (settings.playback_max_seconds, settings.playback_min_seconds, settings.playback_relaxed_backlog) == (
        15,
        8,
        2,
    )
    with pytest.raises(ValidationError):
        _settings(playback_min_seconds=12, playback_max_seconds=10)
    with pytest.raises(ValidationError):
        _settings(playback_max_seconds=16)
    with pytest.raises(ValidationError):
        _settings(playback_min_seconds=4)
