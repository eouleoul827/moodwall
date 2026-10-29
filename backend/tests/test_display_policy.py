"""Deterministic time boundaries and signed-page replay, without a model."""

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import UUID

import pytest

from app.display import BubbleCursorCodec, active_display, effective_mode
from app.errors import Problem
from app.operator import CursorCodec, OperatorService

UTC = timezone.utc
NOW = datetime(2026, 10, 29, 3, 0, tzinfo=UTC)


def board(**overrides):
    return {
        "mode": "MOODWALL",
        "display_paused": False,
        "heartbeat_lease_until": NOW + timedelta(seconds=30),
        **overrides,
    }


def test_paused_flag_has_priority_over_saved_mode():
    assert effective_mode(board(mode="CHATBOT", display_paused=True)) == "PAUSED"


@pytest.mark.parametrize(
    "state", [board(mode="CHATBOT"), board(display_paused=True), board(heartbeat_lease_until=None)]
)
def test_visibility_mode_and_lease_do_not_assign_display_positions(state):
    assert not active_display(state, NOW)


def bubble_payload(**overrides):
    return {
        "v": 1,
        "boardId": str(UUID(int=1)),
        "date": "2026-10-29",
        "generation": "7",
        "snapshotAt": NOW.isoformat(),
        "lastAt": NOW.isoformat(),
        "lastId": str(UUID(int=42)),
        "exp": int((NOW + timedelta(minutes=5)).timestamp()),
        **overrides,
    }


def bubble_board(**overrides):
    return {"id": UUID(int=1), "device_token_version": 7, **overrides}


def test_bubble_cursor_roundtrip_and_exact_expiry():
    codec = BubbleCursorCodec(b"synthetic-display-key")
    encoded = codec.encode(bubble_payload())
    assert codec.decode(encoded, NOW, bubble_board()) == bubble_payload()
    with pytest.raises(Problem) as error:
        codec.decode(encoded, NOW + timedelta(minutes=5), bubble_board())
    assert error.value.code == "INVALID_CURSOR"


@pytest.mark.parametrize(
    "change",
    [
        {"v": True},
        {"generation": "8"},
        {"boardId": str(UUID(int=2))},
        {"date": "2026-10-28"},
        {"exp": True},
        {"lastId": "invalid"},
        {"lastId": 42},
        {"snapshotAt": "2026-10-29T03:00:00"},
        {"lastAt": (NOW + timedelta(seconds=1)).isoformat()},
        {"extra": "invalid"},
    ],
)
def test_bubble_cursor_rejects_signed_but_invalid_scope_or_shape(change):
    codec = BubbleCursorCodec(b"synthetic-display-key")
    with pytest.raises(Problem) as error:
        codec.decode(codec.encode(bubble_payload(**change)), NOW, bubble_board())
    assert error.value.code == "INVALID_CURSOR"


def test_bubble_cursor_rejects_tampering_long_input_and_wrong_key():
    codec = BubbleCursorCodec(b"synthetic-display-key")
    encoded = codec.encode(bubble_payload())
    for value in ["", "x" * 2049, "A" + encoded[1:], BubbleCursorCodec(b"wrong-key").encode(bubble_payload())]:
        with pytest.raises(Problem):
            codec.decode(value, NOW, bubble_board())


def cursor_payload(**overrides):
    return {
        "v": 1,
        "kind": "submissions",
        "date": "2026-10-29",
        "status": None,
        "limit": 50,
        "snapshotAt": NOW.isoformat(),
        "lastAt": NOW.isoformat(),
        "lastId": str(UUID(int=42)),
        "exp": int((NOW + timedelta(minutes=30)).timestamp()),
        **overrides,
    }


def test_cursor_roundtrip_keeps_fixed_date_and_snapshot():
    codec = CursorCodec(b"cursor-test-key")
    payload = cursor_payload()
    assert codec.decode(codec.encode(payload), NOW) == payload


def test_cursor_tamper_and_wrong_key_are_rejected():
    codec = CursorCodec(b"cursor-test-key")
    encoded = codec.encode(cursor_payload())
    with pytest.raises(Problem):
        codec.decode(("A" if encoded[0] != "A" else "B") + encoded[1:], NOW)
    with pytest.raises(Problem):
        CursorCodec(b"other-key").decode(encoded, NOW)


def test_cursor_expiration_is_exclusive():
    codec = CursorCodec(b"cursor-test-key")
    encoded = codec.encode(cursor_payload())
    with pytest.raises(Problem):
        codec.decode(encoded, NOW + timedelta(minutes=30))


@pytest.mark.parametrize(
    "change",
    [
        {"limit": True},
        {"limit": 101},
        {"v": True},
        {"lastId": 42},
        {"kind": "unknown"},
        {"lastAt": "2026-10-29T03:00:00"},
        {"lastId": "not-an-id"},
        {"status": "MALFORMED"},
        {"extra": "not-allowed"},
    ],
)
def test_cursor_signed_but_malformed_payload_is_rejected(change):
    codec = CursorCodec(b"cursor-test-key")
    with pytest.raises(Problem):
        codec.decode(codec.encode(cursor_payload(**change)), NOW)


def test_page_filters_cannot_change_and_midnight_does_not_reset_day():
    auth = SimpleNamespace(derive_key=lambda context: b"cursor-test-key")
    service = OperatorService(None, None, auth, None)
    payload = cursor_payload(
        date="2026-10-29",
        snapshotAt="2026-10-29T14:59:00+00:00",
        exp=int(datetime(2026, 10, 29, 15, 20, tzinfo=UTC).timestamp()),
    )
    encoded = service.cursors.encode(payload)
    after_midnight = datetime(2026, 10, 29, 15, 1, tzinfo=UTC)
    page = service._page(
        kind="submissions",
        now=after_midnight,
        business_date=None,
        status=None,
        cursor=encoded,
        limit=50,
    )
    assert page["date"] == "2026-10-29"
    for changed in ({"limit": 10}, {"status": "APPROVED"}, {"business_date": date(2026, 10, 30)}):
        args = {
            "kind": "submissions",
            "now": after_midnight,
            "business_date": None,
            "status": None,
            "cursor": encoded,
            "limit": 50,
            **changed,
        }
        with pytest.raises(Problem):
            service._page(**args)
