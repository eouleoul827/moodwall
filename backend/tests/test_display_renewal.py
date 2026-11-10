"""A paired wall keeps its credential while it is used; only idle or revoked walls re-pair."""

from datetime import timedelta
from uuid import uuid4

import pytest

from app.auth import DISPLAY_LIFETIME, DISPLAY_RENEW_EVERY
from app.db import one

pytestmark = pytest.mark.integration


async def expiry(database, settings):
    async with database.connection() as conn:
        row = await one(
            conn,
            "SELECT device_token_expires_at AS expires, clock_timestamp() AS now FROM board_runtime WHERE id=%s",
            (settings.board_id,),
        )
    return row["expires"], row["now"]


async def set_remaining(database, settings, remaining):
    async with database.connection() as conn:
        await conn.execute(
            """UPDATE board_runtime SET paired_at=clock_timestamp()-interval '400 days',
               device_token_expires_at=clock_timestamp()+%s WHERE id=%s""",
            (remaining, settings.board_id),
        )


async def heartbeat(clients, protocol="2"):
    body = {"clientInstanceId": str(uuid4())}
    if protocol:
        body["displayProtocolVersion"] = protocol
    return await clients["display"].post("/api/display/heartbeat", json=body)


def test_lifetime_constants_cover_a_vacation_and_renew_daily():
    assert DISPLAY_LIFETIME == timedelta(days=180)
    assert DISPLAY_RENEW_EVERY == timedelta(days=1)


async def test_fresh_pairing_lasts_the_full_lifetime_and_is_not_rewritten_every_heartbeat(clients, database, settings):
    before, _ = await expiry(database, settings)
    assert (await heartbeat(clients)).status_code == 200
    after, now = await expiry(database, settings)
    assert after == before
    assert after - now > DISPLAY_LIFETIME - DISPLAY_RENEW_EVERY - timedelta(minutes=5)


@pytest.mark.parametrize("protocol", ["2", None])
async def test_heartbeat_slides_an_ageing_credential(clients, database, settings, protocol):
    await set_remaining(database, settings, timedelta(days=10))
    response = await heartbeat(clients, protocol)
    assert response.status_code == 200, response.text
    expires, now = await expiry(database, settings)
    assert DISPLAY_LIFETIME - timedelta(minutes=5) < expires - now <= DISPLAY_LIFETIME


async def test_expired_credential_is_not_revived(clients, database, settings):
    await set_remaining(database, settings, timedelta(seconds=-1))
    response = await heartbeat(clients)
    assert response.status_code == 401 and response.json()["code"] == "SESSION_EXPIRED"
    expires, now = await expiry(database, settings)
    assert expires < now


async def test_session_check_reissues_the_same_cookie_for_the_lifetime(clients, settings):
    token = clients["display"].cookies.get(settings.cookie_name("display"))
    response = await clients["display"].get("/api/display/session")
    assert response.status_code == 200, response.text
    cookie = response.headers["set-cookie"]
    assert cookie.startswith(settings.cookie_name("display") + "=" + token + ";")
    assert f"max-age={int(DISPLAY_LIFETIME.total_seconds())}" in cookie.lower()
    assert "httponly" in cookie.lower()
