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


def _summary_table(recs) -> Table:
    """One row per recipe x model: the comparison the factory exists to make."""
    groups: dict[tuple[str, str], list] = {}
    for r in recs:
        groups.setdefault((r.recipe_id, r.run_key.canonical_model), []).append(r)
    t = Table("recipe", "model", "success", "mean E", "model calls", "tokens")
    for (rid, m), rs in groups.items():
        ok = sum(r.status.value == "SUCCESS" for r in rs)
        t.add_row(rid, m, f"{ok}/{len(rs)}", f"{sum(r.composite for r in rs) / len(rs):.3f}",
                  str(sum(r.cost.model_calls for r in rs)), str(sum(r.cost.tokens_in + r.cost.tokens_out for r in rs)))
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
def results(limit: int = typer.Option(20, "--limit"), cells: bool = typer.Option(False, "--cells")) -> None:
    """Show recent runs, or per-cell aggregates with --cells."""
    from harness_factory.results.store import ResultsStore
    store = ResultsStore()
    if cells:
        t = Table("recipe", "model", "task class", "n", "mean E", "ci95", "pass rate")
        for rid, m, a in store.aggregate():
            t.add_row(rid, m, a.cell_id.rsplit("|", 2)[0], str(a.n), f"{a.mean:.3f}",
                      f"[{a.ci95[0]:.3f}, {a.ci95[1]:.3f}]", f"{a.pass_rate:.0%}")
        console.print(t)
        return
    t = Table("id", "task", "model", "status", "E", "tools", "created")
    for r in store.recent(limit):
        t.add_row(str(r["id"]), r["task_version"], r["canonical_model"], r["status"], f"{r['composite']:.3f}",
                  str(r["tool_calls"]), r["created_at"].strftime("%H:%M:%S"))
    console.print(t)


if __name__ == "__main__":
    app()
