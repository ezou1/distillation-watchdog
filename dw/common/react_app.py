"""Serve the React build (frontend/dist) at /app. The HTML pages keep their paths (docs/REACT_STAGE.md rules 6 and 7)."""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from dw.common.config import ROOT

DEFAULT_DIST = ROOT / "frontend" / "dist"


def mount_react_app(app: FastAPI, dist: Path) -> bool:
    """Mount `dist` at /app when it holds a build. Without a build, /app answers 404 and nothing else changes.

    The build uses relative asset paths ("./assets/..."), so /app redirects to /app/: from /app the browser would
    look for the assets at /assets/. The app routes with the URL hash, so no fallback to index.html is needed.
    """
    if not (dist / "index.html").is_file():
        return False

    @app.get("/app", include_in_schema=False)
    async def app_root() -> RedirectResponse:
        return RedirectResponse("/app/")

    app.mount("/app", StaticFiles(directory=dist, html=True), name="react-app")
    return True
