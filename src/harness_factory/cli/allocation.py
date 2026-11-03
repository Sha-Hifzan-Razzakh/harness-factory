"""`hfx allocate`: which provider serves each cell of a campaign (P2, runner/allocation.py, ADR 0011).

    hfx allocate plan --models gpt-oss-120b,llama-3.3-70b --recipe v2-base,v3-staged --seeds 2   # decide, log, save
    hfx allocate run <id> -j 8 [--resume]                                                       # run a saved one
    hfx allocate show <id>
"""

from __future__ import annotations

import asyncio

import typer
from rich.table import Table

allocate_app = typer.Typer(no_args_is_help=True,
                           help="Allocation planner: which provider serves each cell of a campaign (MOD-04).")


def _console():
    from harness_factory.cli.main import console
    return console


def _decisions_table(alloc) -> Table:
    t = Table("model", "served by", "runs", "cells", "list $", "rule", "candidates (days / spare today / $)",
              "calibration", title=f"allocation {alloc.id if alloc.ok else '(refused)'}   strategy {alloc.strategy}")
    for d in alloc.decisions:
        cands = "; ".join(
            f"{g.label}: {g.excluded}" if g.excluded else
            f"{g.label}: {g.days}d / {'∞' if g.spare is None else int(g.spare)} / {g.usd:.2f}"
            for g in d.candidates)
        t.add_row(d.canonical_model, d.chosen or "[red]refused", ", ".join(f"{p} {n}" for p, n in d.providers.items()),
                  str(len(d.cells)), f"{d.usd:.2f}", d.refused or d.rule, cands or "-", "; ".join(d.calibration) or "-")
    return t


def _run(f, alloc, concurrency: int, resume: bool, strict: bool) -> None:
    """Run a frozen allocation: projection and preflight as `hfx smoke` does, then the campaign."""
    from harness_factory.cli.main import _print_sinks, _projection_table, _records_table, _summary_table
    from harness_factory.provision.provisioner import run_key_for
    from harness_factory.runner.allocation import preflight
    from harness_factory.runner.projection import project
    from harness_factory.runner.runner import plan_status, run_campaign
    console = _console()
    _print_sinks(f)
    specs = alloc.specs
    status = plan_status(f, specs)
    todo = status.retry + status.new if resume else specs
    console.print(f"allocation {alloc.id}: {len(specs)} runs   done {len(status.done)}   retry {len(status.retry)}   "
                  f"new {len(status.new)}")
    projection = project(f, todo)
    console.print(_projection_table(projection))
    problems = preflight(f, projection, alloc.deadline_days, alloc.budget_usd)
    if problems:
        for msg in problems:
            console.print(f"[yellow]preflight: {msg}")
        raise typer.Exit(code=2)
    result = asyncio.run(run_campaign(f, specs, concurrency, resume=resume))
    if result.records:
        console.print(_records_table(result.records))
        console.print(_summary_table(result.records))
    ok = sum(r.status.value == "SUCCESS" for r in result.records)
    console.print(f"{ok}/{len(result.records)} SUCCESS in {result.wall_s:.1f}s"
                  + (f"   skipped {len(result.skipped)} done, reran {result.reruns}" if resume else ""))
    for e in result.errors:
        console.print(f"[red]error[/] {e.spec.task_id} {e.spec.recipe_id} {e.spec.canonical_model} "
                      f"{e.spec.provider} seed {e.spec.seed}: {e.error}")
    if strict:
        latest = {r["run_key"]: r["status"] for r in f.store.rows()} if f.store else {}
        keys = [run_key_for(f.tasks.get(s.task_id), f.recipes[s.recipe_id], s.canonical_model, s.provider, s.seed,
                            declared=f.declared, providers=f.providers, fault=f.fault(s.fault)).as_str()
                for s in specs if s.fault is None]
        if result.errors or any(latest.get(k) != "SUCCESS" for k in keys):
            raise typer.Exit(code=1)
    elif result.errors:
        raise typer.Exit(code=1)


@allocate_app.command("plan")
def plan_(models: str = typer.Option(..., "--models", help="comma-separated canonical models"),
          recipe: str = typer.Option("v2-base", "--recipe", help="recipe id, or comma-separated ids"),
          providers: str | None = typer.Option(None, "--providers",
                                               help="only these providers (default: every active one)"),
          seeds: int = typer.Option(1, "--seeds"),
          tasks: str | None = typer.Option(None, "--tasks", help="comma-separated task ids (default: all)"),
          faults: str | None = typer.Option(None, "--faults", help="fault profiles; 'none' = clean"),
          strategy: str = typer.Option("capacity", "--strategy", help="capacity (finish soonest) or cost"),
          deadline_days: int | None = typer.Option(None, "--deadline-days"),
          budget_usd: float | None = typer.Option(None, "--budget-usd"),
          execute: bool = typer.Option(False, "--run", help="run the allocation right after saving it"),
          concurrency: int = typer.Option(4, "--concurrency", "-j"),
          strict: bool = typer.Option(False, "--strict", help="with --run: exit 1 unless all SUCCESS")) -> None:
    """Decide which provider serves each model's cells, log every decision, and save the allocation."""
    from harness_factory.cli.main import _factory, _projection_table
    from harness_factory.runner.allocation import STRATEGIES, allocate, log_allocation, save_allocation
    from harness_factory.runner.projection import project
    console = _console()
    if strategy not in STRATEGIES:
        raise typer.BadParameter(f"--strategy is one of {', '.join(STRATEGIES)}")
    f = _factory()
    try:
        alloc = allocate(f, recipe.split(","), models.split(","), seeds, task_ids=tasks.split(",") if tasks else None,
                         faults=[None if x == "none" else x for x in faults.split(",")] if faults else None,
                         providers=[p.strip() for p in providers.split(",")] if providers else None,
                         strategy=strategy, deadline_days=deadline_days, budget_usd=budget_usd)
    except KeyError as e:
        raise typer.BadParameter(str(e)) from e
    console.print(_decisions_table(alloc))
    logged = log_allocation(alloc)
    console.print(f"[dim]{len(logged)} decision(s) logged in {logged[0].parent if logged else '-'}[/]")
    if not alloc.ok:
        for msg in alloc.refusals:
            console.print(f"[yellow]allocation: {msg}")
        raise typer.Exit(code=2)
    console.print(_projection_table(project(f, alloc.specs)))
    console.print(f"saved allocation {alloc.id} ({len(alloc.specs)} runs) to {save_allocation(alloc)}; "
                  f"run it with `hfx allocate run {alloc.id}`")
    if execute:
        _run(f, alloc, concurrency, resume=False, strict=strict)


@allocate_app.command("run")
def run_(allocation_id: str = typer.Argument(...),
         concurrency: int = typer.Option(4, "--concurrency", "-j"),
         resume: bool = typer.Option(False, "--resume", help="skip runs that already have a scored result"),
         strict: bool = typer.Option(False, "--strict", help="exit 1 unless every run is SUCCESS")) -> None:
    """Run a saved allocation; its runs and providers are frozen, so --resume finds the same RunKeys."""
    from harness_factory.cli.main import _factory
    from harness_factory.runner.allocation import load_allocation
    try:
        alloc = load_allocation(allocation_id)
    except KeyError as e:
        raise typer.BadParameter(str(e)) from e
    _run(_factory(), alloc, concurrency, resume, strict)


@allocate_app.command("show")
def show(allocation_id: str = typer.Argument(...)) -> None:
    """A saved allocation: who serves each model, and why."""
    from harness_factory.runner.allocation import load_allocation
    try:
        alloc = load_allocation(allocation_id)
    except KeyError as e:
        raise typer.BadParameter(str(e)) from e
    _console().print(_decisions_table(alloc))
