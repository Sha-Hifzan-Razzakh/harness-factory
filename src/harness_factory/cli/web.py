"""`hfx web`: the React workspace (web/) served by the API at /app. Builds it with npm the first time (or with
--build); after that no Node is needed to run it."""

from __future__ import annotations

import shutil
import subprocess

import typer
from rich.console import Console

from harness_factory import settings

console = Console()


def _build() -> None:
    src = settings.ROOT / "web"
    npm = shutil.which("npm")
    if npm is None:
        console.print("[red]building the workspace needs Node.js and npm (https://nodejs.org); or run `hfx ui` (Streamlit)")
        raise typer.Exit(code=1)
    for args in (["install", "--no-audit", "--no-fund"], ["run", "build"]):
        console.print(f"[dim]npm {' '.join(args)}[/]")
        if subprocess.run([npm, *args], cwd=src, check=False).returncode:
            raise typer.Exit(code=1)


def web(host: str = typer.Option("127.0.0.1", "--host", help="bind address; anything but loopback needs a token"),
        port: int = typer.Option(8765, "--port", envvar="HF_API_PORT"),
        build: bool = typer.Option(False, "--build", help="rebuild the app first (after pulling web/ changes)"),
        sync: bool = typer.Option(True, "--sync/--no-sync", help="record changes made outside the workspace first"),
        log_level: str = typer.Option("warning", "--log-level")) -> None:
    """The workspace: versioned catalog editing, activity, experiment builder, results, cards. One process: the API
    serves the app at http://<host>:<port>/app."""
    from harness_factory.api.web import DIST
    from harness_factory.cli.serve import serve
    if build or not (DIST / "index.html").is_file():
        _build()
    if sync:
        from harness_factory.authoring.service import AuthoringService
        made = AuthoringService().sync()
        ext = [m for m in made if m["action"] != "baseline"]
        if made:
            console.print(f"history: {len(made) - len(ext)} item(s) started, {len(ext)} outside change(s) recorded")
    console.print(f"[bold]Workspace on http://{host}:{port}/app/[/]")
    serve(host=host, port=port, log_level=log_level)
