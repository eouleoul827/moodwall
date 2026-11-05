"""Optional, same-origin serving for the built React application.

Only known application URLs receive index.html. In particular, an unknown API
URL must retain the API's JSON 404 instead of accidentally becoming a SPA page.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from starlette.staticfiles import StaticFiles

PAGE_PATHS = ("/", "/join", "/student", "/display", "/display/setup", "/operator")
CONTENT_SECURITY_POLICY = "; ".join(
    (
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' data: blob:",
        "font-src 'self' data:",
        "connect-src 'self'",
        "object-src 'none'",
        "base-uri 'none'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    )
)


def install_frontend(app: FastAPI, configured_dist: str | None) -> None:
    """Register a trusted build directory without exposing the repository tree."""
    if configured_dist is None:
        return
    dist = Path(configured_dist).expanduser().resolve()
    index = dist / "index.html"
    assets = dist / "assets"
    if not index.is_file() or not assets.is_dir():
        raise ValueError("FRONTEND_DIST must contain a built index.html and assets directory")

    async def page():
        return FileResponse(
            index,
            media_type="text/html",
            headers={
                "Cache-Control": "no-store",
                "Content-Security-Policy": CONTENT_SECURITY_POLICY,
            },
        )

    for path in PAGE_PATHS:
        app.add_api_route(path, page, methods=["GET", "HEAD"], include_in_schema=False)
        if path != "/":
            app.add_api_route(path + "/", page, methods=["GET", "HEAD"], include_in_schema=False)
    app.mount("/assets", StaticFiles(directory=assets, follow_symlink=False), name="frontend-assets")
