"""hfx traces: stored JSONL traces. `replay` sends them over OTLP (the collector fans out to Phoenix, Langfuse and
MLflow) with their original timestamps, so runs made before a backend was connected show up without re-running."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

traces_app = typer.Typer(no_args_is_help=True, help="Stored JSONL traces (var/traces).")
console = Console()


@traces_app.command("replay")
def traces_replay(paths: Annotated[list[Path] | None, typer.Argument(
        help="JSONL files or directories (default: var/traces)")] = None) -> None:
    """Send stored runs to OTEL_EXPORTER_OTLP_ENDPOINT as new traces with their original times (hf.replayed)."""
    from harness_factory import settings
    from harness_factory.observability.replay import replay
    try:
        runs, spans = replay(paths or [settings.TRACES_DIR])
    except (RuntimeError, ValueError, OSError) as e:
        console.print(f"[red]{e}", highlight=False)
        raise typer.Exit(code=2) from None
    console.print(f"replayed {runs} run(s), {spans} span(s) to {settings.OTLP_ENDPOINT}")


def register(app: typer.Typer) -> None:
    app.add_typer(traces_app, name="traces")
