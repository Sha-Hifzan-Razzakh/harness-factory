"""`hfx calibration`: cross-provider equivalence (P2, results/calibration.py, ADR 0010).

    hfx calibration run gpt-oss-120b --providers groq,openrouter --run --seeds 2   # run missing pairs, then judge
    hfx calibration list                                                           # saved verdicts
    hfx calibration cells                                                          # cells, equivalent providers pooled
"""

from __future__ import annotations

import asyncio

import typer
from rich.table import Table

calibration_app = typer.Typer(no_args_is_help=True,
                              help="Cross-provider equivalence: may two providers' runs of a model share a cell?")


def _console():
    from harness_factory.cli.main import console
    return console


def _eq_table(eqs) -> Table:
    t = Table("id", "model", "A", "B", "served (A / B)", "pairs", "mean B-A", "95% interval", "W/L/T", "tokens B/A",
              "verdict", title="cross-provider calibrations (newest per pair is the one in force)")
    for eq in eqs:
        a, b = eq.providers
        t.add_row(eq.id, eq.canonical_model, a, b, f"{eq.served[a]} / {eq.served[b]}", str(eq.n),
                  "-" if eq.mean is None else f"{eq.mean:+.3f}",
                  "-" if eq.ci is None else f"[{eq.ci[0]:+.3f}, {eq.ci[1]:+.3f}]",
                  f"{eq.wins}/{eq.losses}/{eq.ties}", "-" if eq.token_ratio is None else f"{eq.token_ratio:.2f}",
                  eq.verdict)
    return t


@calibration_app.command("run")
def run(model: str = typer.Argument(..., help="canonical model both providers serve"),
        providers: str = typer.Option(..., "--providers", help="exactly two provider ids, comma-separated"),
        recipe: str = typer.Option("v2-base", "--recipe", help="recipe the pairs are run with"),
        margin: float = typer.Option(0.1, "--margin", help="largest E0 difference still called equivalent"),
        min_pairs: int = typer.Option(20, "--min-pairs"),
        execute: bool = typer.Option(False, "--run", help="first run the pairs the store doesn't have yet"),
        seeds: int = typer.Option(1, "--seeds"),
        tag: str | None = typer.Option(None, "--tag", help="only tasks with this tag"),
        concurrency: int = typer.Option(4, "--concurrency", "-j"),
        note: str = typer.Option("", "--note")) -> None:
    """Pair runs that differ only in the provider, judge E0's paired difference, and save the verdict."""
    from harness_factory.cli.main import _factory
    from harness_factory.results.calibration import calibrate
    from harness_factory.runner.runner import plan, run_campaign
    console = _console()
    ids = [p.strip() for p in providers.split(",") if p.strip()]
    if len(set(ids)) != 2:
        console.print("[red]--providers takes exactly two different provider ids")
        raise typer.Exit(code=2)
    f = _factory()
    if execute:
        specs = [s for p in ids for s in plan(f, recipe, p, [model], seeds, tag=tag)]
        result = asyncio.run(run_campaign(f, specs, concurrency, resume=True))
        console.print(f"ran {len(result.records)} runs ({len(result.skipped)} already in the store, "
                      f"{len(result.errors)} errors)")
    eq = calibrate(f.store.rows(), f.providers, model, ids, recipe_hash=f.recipes[recipe].content_hash(),
                   margin=margin, min_pairs=min_pairs, note=note)
    f.store.save_equivalence(eq)
    console.print(_eq_table([eq]))
    console.print(f"{eq.verdict}: {eq.reason}")
    if eq.verdict == "equivalent":
        console.print("`hfx calibration cells` now pools the two providers' cells for this model.")


@calibration_app.command("list")
def list_() -> None:
    """Saved calibrations, oldest first."""
    from harness_factory.results.calibration import Equivalence
    from harness_factory.results.store import ResultsStore
    _console().print(_eq_table([Equivalence.model_validate(r) for r in ResultsStore().equivalences()]))


@calibration_app.command("cells")
def cells(baseline: str | None = typer.Option(None, "--baseline", help="saved baseline id; default: live")) -> None:
    """Per-cell results like `hfx results --cells`, with calibrated-equivalent providers pooled into one cell."""
    from harness_factory import settings
    from harness_factory.cli.main import _cells_table
    from harness_factory.providers.catalog import load_provider_catalog
    from harness_factory.results.calibration import pools
    from harness_factory.results.store import ResultsStore
    console = _console()
    store = ResultsStore()
    chosen = store.get_baseline(baseline) if baseline else None
    if baseline and chosen is None:
        console.print(f"[red]no baseline {baseline!r}")
        raise typer.Exit(code=1)
    pooled = pools(store.equivalences(), load_provider_catalog(settings.PROVIDERS_FILE))
    used, result = store.aggregate(chosen, pools=pooled)
    console.print(_cells_table(used, result))
    groups = sorted({f"{m}: {label}" for (m, _, _), label in pooled.items()})
    console.print("pooled: " + (", ".join(groups) if groups else "nothing (no equivalent calibration in force)"))
