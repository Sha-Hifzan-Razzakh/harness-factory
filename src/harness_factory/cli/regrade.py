"""hfx regrade: the current graders over stored runs' end states (runner/regrade.py)."""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

console = Console()
RUN_IDS = typer.Option(None, "--run-id", help="stored run id (hfx results); repeatable")


def regrade(run_id: list[int] = RUN_IDS,
            task: str | None = typer.Option(None, "--task", help="task id or id@version: its latest runs"),
            all_runs: bool = typer.Option(False, "--all", help="the latest run of every RunKey"),
            changed_only: bool = typer.Option(False, "--changed", help="list only runs whose grade changed")) -> None:
    """Re-grade stored runs with the current graders and task checks. Each new grade is stored as its own row
    (the run's row is kept as it was); the table shows which runs changed."""
    from harness_factory.runner.regrade import regrade as do_regrade
    from harness_factory.runner.runner import Factory
    f = Factory.load()
    try:
        outcomes = do_regrade(f, run_ids=run_id or None, task=task, all_runs=all_runs)
    except (ValueError, KeyError) as e:
        console.print(f"[red]{e}")
        raise typer.Exit(code=1) from e
    t = Table("run", "task", "status", "O", "Q", "E0", "note")
    for o in outcomes:
        if changed_only and not o.changed:
            continue
        if o.after is None:
            t.add_row(str(o.run_id), o.task_version, o.before.status, "", "", "", f"[yellow]skipped: {o.skipped}")
            continue
        b, a = o.before, o.after

        def cell(x, y) -> str:
            return f"{x}" if x == y else f"[bold]{x} -> {y}[/]"
        t.add_row(str(o.run_id), o.task_version, cell(b.status, a.status), cell(b.outcome, a.outcome),
                  cell(b.trajectory, a.trajectory), cell(b.composite, a.composite),
                  ("changed" if o.changed else "same") + ("; task edited since the run" if o.task_edited else ""))
    console.print(t)
    done = [o for o in outcomes if o.after is not None]
    console.print(f"{len(done)} re-graded, {sum(o.changed for o in done)} changed, "
                  f"{len(outcomes) - len(done)} skipped")


def register(app: typer.Typer) -> None:
    app.command("regrade")(regrade)
