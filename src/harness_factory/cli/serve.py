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
