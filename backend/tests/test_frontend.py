"""The React build must never hide API errors or expose source/private files."""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from app.frontend import CONTENT_SECURITY_POLICY, PAGE_PATHS, install_frontend


@pytest.fixture
def dist(tmp_path):
    build = tmp_path / "dist"
    (build / "assets").mkdir(parents=True)
    (build / "index.html").write_text("<!doctype html><html><body>synthetic React build</body></html>")
    (build / "assets" / "app-synthetic.js").write_text("export const ready = true;")
    (tmp_path / "private.txt").write_text("synthetic private fixture")
    return build


def application(directory=None):
    app = FastAPI()

    @app.exception_handler(HTTPException)
    async def missing(request: Request, error):
        return JSONResponse({"code": "NOT_FOUND"}, status_code=error.status_code)

    @app.get("/api/health/live")
    async def live():
        return {"status": "ok"}

    install_frontend(app, str(directory) if directory is not None else None)
    return app


async def test_known_application_pages_share_build_and_csp(dist):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application(dist)), base_url="http://test"
    ) as client:
        for path in PAGE_PATHS:
            response = await client.get(path)
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/html")
            assert "synthetic React build" in response.text
            assert response.headers["cache-control"] == "no-store"
            assert response.headers["content-security-policy"] == CONTENT_SECURITY_POLICY
        response = await client.head("/join/")
        assert response.status_code == 200
        assert response.content == b""


async def test_assets_are_served_but_missing_api_and_source_stay_json_404(dist):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application(dist)), base_url="http://test"
    ) as client:
        script = await client.get("/assets/app-synthetic.js")
        assert script.status_code == 200
        assert "export const ready" in script.text
        assert (await client.get("/api/health/live")).json() == {"status": "ok"}
        for path in ("/api/misspelled", "/api", "/not-a-page", "/.env", "/package.json", "/src/App.tsx"):
            response = await client.get(path)
            assert response.status_code == 404
            assert response.json() == {"code": "NOT_FOUND"}
        for path in ("/assets/../private.txt", "/assets/%2e%2e/private.txt", "/assets/%2e%2e%2fprivate.txt"):
            response = await client.get(path)
            assert response.status_code == 404
            assert "synthetic private fixture" not in response.text


async def test_no_build_keeps_api_only_and_schema_is_unchanged(dist):
    app = application()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get("/join")).status_code == 404
        assert (await client.get("/api/health/live")).status_code == 200
    mounted = application(dist)
    assert mounted.openapi()["paths"] == app.openapi()["paths"]


async def test_real_api_middleware_and_contract_survive_frontend_install(dist, monkeypatch):
    # ASGI transport deliberately does not enter the DB-backed application lifespan.
    from app.config import Settings

    values = {
        "MOODWALL_DATABASE_URL": "postgresql://synthetic:synthetic@127.0.0.1/moodwall_static_test",
        "MOODWALL_SECRET_KEY": "synthetic-static-session-key-01234567890123456789",
        "MOODWALL_QR_KEY": "synthetic-static-qr-key-01234567890123456789012345",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    from app.main import create_app

    settings = Settings(
        _env_file=None,
        database_url=values["MOODWALL_DATABASE_URL"],
        secret_key=values["MOODWALL_SECRET_KEY"],
        qr_key=values["MOODWALL_QR_KEY"],
        frontend_dist=str(dist),
    )
    app = create_app(settings)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        page = await client.get("/join")
        assert page.status_code == 200
        assert page.headers["x-frame-options"] == "DENY"
        assert page.headers["content-security-policy"] == CONTENT_SECURITY_POLICY
        assert page.headers["cache-control"] == "no-store"
        missing = await client.get("/api/misspelled")
        assert missing.status_code == 404
        assert missing.json()["code"] == "NOT_FOUND"
        assert missing.headers["content-type"].startswith("application/problem+json")
        contract = (await client.get("/openapi.json")).json()
        assert not set(PAGE_PATHS).intersection(contract["paths"])


def test_explicit_missing_or_unbuilt_directory_fails_fast(tmp_path):
    with pytest.raises(ValueError, match="FRONTEND_DIST"):
        application(tmp_path)
    (tmp_path / "index.html").write_text("synthetic incomplete build")
    with pytest.raises(ValueError, match="FRONTEND_DIST"):
        application(tmp_path)


async def test_assets_do_not_follow_external_symlinks(dist, tmp_path):
    target = tmp_path / "private.txt"
    link = dist / "assets" / "linked.txt"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("This Windows account cannot create symbolic links")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application(dist)), base_url="http://test"
    ) as client:
        response = await client.get("/assets/linked.txt")
        assert response.status_code == 404
        assert "synthetic private fixture" not in response.text
