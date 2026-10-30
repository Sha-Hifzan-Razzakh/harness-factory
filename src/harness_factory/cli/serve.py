"""`hfx serve`: the API (optional extra `api`)."""

from __future__ import annotations

import ipaddress
import os

import typer
from rich.console import Console

console = Console()


def _loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def serve(host: str = typer.Option("127.0.0.1", "--host", help="bind address; anything but loopback needs a token"),
          port: int = typer.Option(8765, "--port", envvar="HF_API_PORT"),
          log_level: str = typer.Option("info", "--log-level")) -> None:
    """The HTTP API: catalog authoring (same rules as `hfx new`) and read access to tasks, models and results.
    Docs at http://<host>:<port>/docs. Set HF_API_TOKEN to require `Authorization: Bearer <token>`."""
    try:
        import uvicorn
    except ImportError:
        console.print("[red]the API needs the `api` extra: uv sync --extra api")
        raise typer.Exit(code=1) from None
    from harness_factory.api.app import create_app

    token = os.environ.get("HF_API_TOKEN") or None
    if not _loopback(host) and token is None:
        # Authoring runs pasted tool code during discovery: never expose that without a token.
        console.print(f"[red]refusing to listen on {host} without HF_API_TOKEN (pasted tool code is executed)")
        raise typer.Exit(code=1)
    console.print(f"API on http://{host}:{port}  (docs /docs){'  token required' if token else ''}")
    uvicorn.run(create_app(token=token), host=host, port=port, log_level=log_level)


def ui(port: int = typer.Option(8501, "--port", envvar="HF_UI_PORT"),
       api_url: str = typer.Option("http://127.0.0.1:8765", "--api-url", envvar="HF_API_URL"),
       with_api: bool = typer.Option(False, "--serve", help="also start the API (hfx serve) on --api-url's port")) -> None:
    """The UI (optional extra `ui`): catalog, new experiments, campaigns and results, all through the API.
    Listens on localhost only. Start the API first (`hfx serve`) or pass --serve."""
    import subprocess
    import sys
    from pathlib import Path
    from urllib.parse import urlparse

    try:
        import streamlit  # noqa: F401
    except ImportError:
        console.print("[red]the UI needs the `ui` extra: uv sync --extra ui (and --extra api for --serve)")
        raise typer.Exit(code=1) from None
    env = {**os.environ, "HF_API_URL": api_url}
    api_proc = None
    if with_api:
        u = urlparse(api_url)
        api_proc = subprocess.Popen([sys.executable, "-c", "from harness_factory.cli.main import app; app()",
                                     "serve", "--host", u.hostname or "127.0.0.1", "--port", str(u.port or 8765)])
    script = Path(__file__).resolve().parents[1] / "ui" / "app.py"
    console.print(f"UI on http://127.0.0.1:{port}  (API {api_url})")
    try:
        subprocess.run([sys.executable, "-m", "streamlit", "run", str(script), "--server.address", "127.0.0.1",
                        "--server.port", str(port), "--server.headless", "true",
                        "--browser.gatherUsageStats", "false"], env=env, check=False)
    finally:
        if api_proc is not None:
            api_proc.terminate()
            api_proc.wait(timeout=30)
