"""Serve the React workspace (web/, built into src/harness_factory/web_dist) from the API at /app.

The app only calls the API, like the Streamlit UI. Built once with `hfx web --build` (npm), it needs no Node to run."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse

DIST = Path(__file__).resolve().parents[1] / "web_dist"


def mount(app: FastAPI, dist: Path = DIST) -> bool:
    """Serve the built app at /app (and send / there). False when it hasn't been built."""
    if not (dist / "index.html").is_file():
        return False
    from fastapi.staticfiles import StaticFiles
    app.mount("/app", StaticFiles(directory=dist, html=True), name="web")

    @app.get("/", include_in_schema=False)
    def root() -> RedirectResponse:
        return RedirectResponse("/app/")

    return True
