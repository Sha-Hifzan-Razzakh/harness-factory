"""hfx: command line for the factory. (Not "hf": huggingface_hub, a LiteLLM dependency, owns that name.)"""

from __future__ import annotations

import asyncio

import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer(no_args_is_help=True, add_completion=False, help="Harness Factory")
console = Console()


def _factory(store: bool = True):
    from harness_factory.runner.runner import Factory
    return Factory.load(store=store)


def _records_table(recs) -> Table:
    t = Table("task", "class", "recipe", "model", "status", "O", "Q", "E", "calls", "tools", "tokens")
    for r in recs:
        colour = {"SUCCESS": "green", "PARTIAL": "yellow"}.get(r.status.value, "red")
        t.add_row(r.run_key.task_version, r.task_class, r.recipe_id, r.run_key.canonical_model,
                  f"[{colour}]{r.status.value}", f"{r.scores.outcome:.2f}", f"{r.scores.trajectory:.2f}",
                  f"{r.composite:.3f}", str(r.cost.model_calls), str(r.cost.tool_calls),
                  str(r.cost.tokens_in + r.cost.tokens_out))
    return t


def _cells_table(baseline, cells, by_class: bool = True) -> Table:
    cols = (["task class"] if by_class else []) + ["recipe", "model", "n", "success", "E0", "C", "E", "ci95 (E)",
                                                     "tokens"]
    t = Table(*cols, title=f"cost baseline {baseline.id} ({baseline.basis}, {baseline.note or 'saved'})")
    for c in cells:
        row = ([c.task_class] if by_class else []) + [
            c.recipe_id, c.canonical_model, str(c.e.n), f"{c.e.pass_rate:.0%}", f"{c.mean_e0:.3f}",
            f"{c.mean_c:.2f}", f"{c.e.mean:.3f}", f"[{c.e.ci95[0]:.3f}, {c.e.ci95[1]:.3f}]", str(c.tokens)]
        t.add_row(*row)
    return t


def _summary_table(recs) -> Table:
    """One row per recipe x model, cost-adjusted against these runs: the comparison the factory exists to make.

    Each run is scored against its own task's median (and its own class's weights), then pooled across tasks.
    """
    from harness_factory.results.cost import CostBaseline
    from harness_factory.results.store import record_row

    rows = [record_row(r) for r in recs]
    baseline = CostBaseline.from_rows(rows, note="these runs")
    groups: dict[tuple[str, str], list[dict]] = {}
    for r in rows:
        groups.setdefault((r["recipe_id"], r["canonical_model"]), []).append(r)
    t = Table("recipe", "model", "success", "E0", "C", "E", "model calls", "tokens",
              title=f"cost-adjusted against these runs ({baseline.basis})")
    for (rid, m), rs in groups.items():
        n = len(rs)
        t.add_row(rid, m, f"{sum(r['status'] == 'SUCCESS' for r in rs)}/{n}",
                  f"{sum(r['composite'] for r in rs) / n:.3f}",
                  f"{sum(baseline.c(r) or 0.0 for r in rs) / n:.2f}",
                  f"{sum(baseline.adjusted(r) for r in rs) / n:.3f}",
                  str(sum(r["model_calls"] for r in rs)), str(sum(r["tokens_in"] + r["tokens_out"] for r in rs)))
    return t


@app.command()
def tasks() -> None:
    """List tasks with their class keys."""
    from harness_factory.provision.provisioner import task_class
    f = _factory(store=False)
    t = Table("task", "domain", "class", "split", "status", "allowed tools")
    for task in f.tasks.all(include_held_out=True):
        t.add_row(task.key(), task.domain, task_class(task, f.declared), task.split.value, task.status,
                  ", ".join(task.allowed_tools))
    console.print(t)


@app.command()
def validate() -> None:
    """Validate every recipe against the tool catalog, prompts and providers."""
    from harness_factory.recipes.validate import validate_recipe
    f = _factory(store=False)
    bad = 0
    for rid, recipe in f.recipes.items():
        issues = validate_recipe(recipe, f.declared)
        console.print(f"{'[green]ok' if not issues else '[red]FAIL'}[/] {rid} ({recipe.content_hash()})")
        for i in issues:
            console.print(f"    {i.rule}: {i.detail}")
        bad += bool(issues)
    raise typer.Exit(code=1 if bad else 0)


@app.command()
def reference(task_id: str | None = typer.Argument(None)) -> None:
    """Run reference solutions against their own oracles (TSK-04)."""
    from harness_factory.runner.runner import run_reference
    f = _factory(store=False)
    todo = [f.tasks.get(task_id)] if task_id else f.tasks.all(include_held_out=True)
    failed = 0
    for task in todo:
        g = asyncio.run(run_reference(f, task))
        console.print(f"{'[green]pass' if g.passed else '[red]FAIL'}[/] {task.key()} score={g.score:.2f}  {g.detail}")
        for c in g.checks:
            if not c.passed:
                console.print(f"    {c.id}: {c.detail}")
        failed += not g.passed
    raise typer.Exit(code=1 if failed else 0)


@app.command()
def run(task: str = typer.Option(..., "--task"), recipe: str = typer.Option("v2-base", "--recipe"),
        model: str = typer.Option(..., "--model"), provider: str = typer.Option(..., "--provider"),
        seed: int = typer.Option(0, "--seed")) -> None:
    """Run one task with one recipe on one model."""
    from harness_factory.runner.runner import run_one
    f = _factory()
    rec = asyncio.run(run_one(f, f.tasks.get(task), f.recipes[recipe], model, provider, seed))
    console.print(_records_table([rec]))


@app.command()
def smoke(recipe: str = typer.Option("v2-base", "--recipe", help="recipe id, or comma-separated ids to compare"), provider: str = typer.Option("scripted", "--provider"),
          models: str | None = typer.Option(None, "--models", help="comma-separated canonical models"),
          seeds: int = typer.Option(1, "--seeds"),
          strict: bool = typer.Option(False, "--strict", help="exit 1 unless every run is SUCCESS (CI gate)")) -> None:
    """Every task x recipe x model on one provider. One recipe is the P0 gate; several compare shapes."""
    from harness_factory.runner.runner import smoke as run_smoke
    f = _factory()
    recipe_ids = recipe.split(",")
    # Budget preflight (MOD-06): worst case = every run uses every step. Refuse if it cannot fit today's quota.
    n_models = len(models.split(",")) if models else len(f.providers.get(provider).models)
    worst = len(f.tasks.all()) * n_models * seeds * sum(f.recipes[r].config.max_steps for r in recipe_ids)
    left = f.ledger.remaining(provider)["rpd"]
    if left is not None and worst > left:
        console.print(f"[yellow]preflight: worst case {worst} requests > {left} left today on {provider}; "
                      f"lower --seeds, pass fewer --models, or lower max_steps")
        raise typer.Exit(code=2)
    recs = asyncio.run(run_smoke(f, recipe_ids, provider, models.split(",") if models else None, seeds))
    console.print(_records_table(recs))
    if len(recipe_ids) > 1 or not models or "," in models:
        console.print(_summary_table(recs))
    ok = sum(r.status.value == "SUCCESS" for r in recs)
    console.print(f"{ok}/{len(recs)} SUCCESS")
    if strict and ok != len(recs):
        raise typer.Exit(code=1)


@app.command()
def results(limit: int = typer.Option(20, "--limit"), cells: bool = typer.Option(False, "--cells"),
            baseline: str | None = typer.Option(None, "--baseline",
                                                help="saved baseline id, or 'latest'; default: live")) -> None:
    """Show recent runs, or per-cell cost-adjusted aggregates with --cells."""
    from harness_factory.results.store import ResultsStore
    store = ResultsStore()
    if cells:
        chosen = None
        if baseline == "latest":
            saved = store.list_baselines()
            if not saved:
                console.print("[red]no saved baseline; run `hfx baseline save` first")
                raise typer.Exit(code=1)
            chosen = saved[-1]
        elif baseline:
            chosen = store.get_baseline(baseline)
            if chosen is None:
                console.print(f"[red]no baseline {baseline!r}")
                raise typer.Exit(code=1)
        used, result = store.aggregate(chosen)
        console.print(_cells_table(used, result))
        missing = sorted({r["task_version"] for r in store.rows()} - set(used.medians))
        if missing:
            console.print(f"[yellow]not in this baseline (scored with C = 0): {', '.join(missing)}")
        return
    t = Table("id", "task", "recipe", "model", "status", "E0", "tools", "created")
    for r in store.recent(limit):
        t.add_row(str(r["id"]), r["task_version"], r["recipe_id"], r["canonical_model"], r["status"],
                  f"{r['composite']:.3f}", str(r["tool_calls"]), r["created_at"].strftime("%H:%M:%S"))
    console.print(t)


baseline_app = typer.Typer(no_args_is_help=True, help="Cost baselines: the per-task medians that C is relative to.")
app.add_typer(baseline_app, name="baseline")


@baseline_app.command("save")
def baseline_save(note: str = typer.Option("", "--note")) -> None:
    """Freeze the live baseline (current per-task medians) so later runs are scored against fixed numbers."""
    from harness_factory.results.store import ResultsStore
    store = ResultsStore()
    b = store.live_baseline()
    b.note = note
    if not b.medians:
        console.print("[red]no runs in the store yet")
        raise typer.Exit(code=1)
    console.print(f"saved baseline {store.save_baseline(b)} ({b.basis}, {len(b.medians)} tasks)")


@baseline_app.command("list")
def baseline_list() -> None:
    """Saved baselines, oldest first."""
    from harness_factory.results.store import ResultsStore
    t = Table("id", "basis", "tasks", "note", "created")
    for b in ResultsStore().list_baselines():
        t.add_row(b.id, b.basis, str(len(b.medians)), b.note, b.created_at.strftime("%Y-%m-%d %H:%M"))
    console.print(t)


if __name__ == "__main__":
    app()
