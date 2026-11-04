"""`hfx transfer`: does a recipe carry over from the native runtime to a framework runner? (RUN-05, ADR 0016)

    hfx transfer run v2-langgraph-prebuilt --model scripted/replay --provider scripted --run --seeds 4
    hfx transfer list
"""

from __future__ import annotations

import asyncio

import typer
from rich.table import Table

transfer_app = typer.Typer(no_args_is_help=True,
                           help="Runner transfer check: native vs a framework runner on the same recipe, judged "
                                "with the provider-calibration rule.")


def _console():
    from harness_factory.cli.main import console
    return console


def _table(trs) -> Table:
    t = Table("id", "native", "runner", "runtime", "framework", "model", "provider", "pairs", "mean R-N",
              "95% interval", "W/L/T", "tokens R/N", "calls R/N", "verdict",
              title="runner transfer checks (runner minus native, E0)")
    for tr in trs:
        fw = ", ".join(f"{k} {v}" for k, v in tr.framework.items())
        t.add_row(tr.id, tr.native_recipe, tr.runner_recipe, tr.runtime, fw, tr.canonical_model, tr.provider,
                  str(tr.n), "-" if tr.mean is None else f"{tr.mean:+.3f}",
                  "-" if tr.ci is None else f"[{tr.ci[0]:+.3f}, {tr.ci[1]:+.3f}]", f"{tr.wins}/{tr.losses}/{tr.ties}",
                  "-" if tr.token_ratio is None else f"{tr.token_ratio:.2f}",
                  "-" if tr.call_ratio is None else f"{tr.call_ratio:.2f}", tr.verdict)
    return t


@transfer_app.command("run")
def run(runner: str = typer.Argument(..., help="recipe id with a framework runtime, e.g. v2-langgraph-prebuilt"),
        native: str | None = typer.Option(None, "--native", help="its native counterpart; default: its base"),
        model: str = typer.Option(..., "--model"),
        provider: str = typer.Option(..., "--provider"),
        margin: float = typer.Option(0.1, "--margin", help="largest E0 difference still called equivalent"),
        min_pairs: int = typer.Option(20, "--min-pairs"),
        execute: bool = typer.Option(False, "--run", help="first run the pairs the store doesn't have yet"),
        seeds: int = typer.Option(1, "--seeds"),
        tag: str | None = typer.Option(None, "--tag", help="only tasks with this tag"),
        concurrency: int = typer.Option(4, "--concurrency", "-j"),
        note: str = typer.Option("", "--note")) -> None:
    """Pair runs that differ only in the runner, judge E0's paired difference, and save the verdict."""
    from harness_factory.cli.main import _factory
    from harness_factory.results.transfer import counterpart_issues, transfer
    from harness_factory.runner.runner import plan, run_campaign
    console = _console()
    f = _factory()
    if runner not in f.recipes:
        console.print(f"[red]unknown recipe {runner!r}")
        raise typer.Exit(code=2)
    native = native or f.recipes[runner].base
    if native not in f.recipes:
        console.print(f"[red]{runner} names no native counterpart; pass --native")
        raise typer.Exit(code=2)
    issues = counterpart_issues(f.recipes[native], f.recipes[runner])
    if issues:
        for i in issues:
            console.print(f"[red]{i}")
        raise typer.Exit(code=2)
    if execute:
        task_ids = [t.id for t in f.tasks.all() if tag in t.tags] if tag else None
        specs = plan(f, [native, runner], provider, [model], seeds, task_ids=task_ids)
        result = asyncio.run(run_campaign(f, specs, concurrency, resume=True))
        console.print(f"ran {len(result.records)} runs ({len(result.skipped)} already in the store, "
                      f"{len(result.errors)} errors)")
    tr = transfer(f.store.rows(), f.providers, f.recipes[native], f.recipes[runner], model, provider,
                  margin=margin, min_pairs=min_pairs, note=note)
    f.store.save_transfer(tr)
    console.print(_table([tr]))
    console.print(f"{tr.verdict}: {tr.reason}")


@transfer_app.command("list")
def list_() -> None:
    """Saved transfer checks, oldest first."""
    from harness_factory.results.store import ResultsStore
    from harness_factory.results.transfer import Transfer
    _console().print(_table([Transfer.model_validate(r) for r in ResultsStore().transfers()]))
