"""`hfx analyze`: shape × model by task class and crossover by step depth (ANL-01), from the results store.

Needs the `analysis` extra (polars, scipy): `uv sync --extra analysis` (included in `dev`).
"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console

console = Console()


def register(app: typer.Typer) -> None:
    app.command("analyze")(analyze)


def analyze(out: str | None = typer.Option(None, "--out", help="directory for analysis.md and the CSVs "
                                                               "(default: print the markdown)"),
            models: str | None = typer.Option(None, "--models", help="comma-separated models (default: all)"),
            recipes: str | None = typer.Option(None, "--recipes",
                                               help="comma-separated recipe ids to compare (default: every "
                                                    "shape recipe, i.e. recipes without `base:`)"),
            baseline: str | None = typer.Option(None, "--baseline", help="frozen cost baseline id"),
            views: bool = typer.Option(False, "--views", help="also write the anl_* tables and v_* views to the "
                                                              "results DB (for Superset)"),
            title: str = typer.Option("Shape × model analysis", "--title")) -> None:
    """Which harness shape wins per model and task class, and where the best shape changes with step depth:
    mean E with 95% bootstrap intervals, paired winner verdicts, crossovers."""
    try:
        from harness_factory.results import analysis as anl
    except ImportError as e:                                   # pragma: no cover - extra not installed
        console.print(f"[red]hfx analyze needs the analysis extra ({e.name}): uv sync --extra analysis")
        raise typer.Exit(code=1) from e
    from harness_factory.cli import main
    f = main._factory()
    rows, b = main._evidence(f, baseline)          # the same evidence as `hfx report` and `hfx select`
    try:
        arms = anl.arms_of(f, recipes.split(",") if recipes else None)
    except KeyError as e:
        raise typer.BadParameter(str(e.args[0])) from e
    a = anl.analyze(anl.current_rows(f, rows), b, arms, models.split(",") if models else None)
    if out:
        for p in anl.write_outputs(a, Path(out), title):
            console.print(f"wrote {p}")
    else:
        print(anl.to_markdown(a, title))
    if views:
        names = anl.materialize(f.store.engine, a)
        console.print(f"views: {', '.join(names)} (baseline {a.baseline_id})")
