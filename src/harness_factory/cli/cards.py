"""hfx card: spec cards for models, harnesses, tasks, tools and fault profiles, and the final spec card of one
experiment (cards/build.py). `hfx card spec ... --run` shows the card, asks, runs it and saves the card with its result."""

from __future__ import annotations

import json

import typer
from rich.console import Console
from rich.markdown import Markdown

console = Console()
card_app = typer.Typer(no_args_is_help=True, help="Spec cards: model, harness, task, tool, fault, and the final "
                                                  "spec card of one experiment (task x harness x model).")

JSON = typer.Option(False, "--json", help="print the card as JSON (what the API and UI use)")
SAVE = typer.Option(False, "--save", help="also write it to var/cards/<kind>/ as .md and .json")


def _emit(card, as_json: bool, save: bool) -> None:
    from harness_factory.cards.render import save as save_card
    from harness_factory.cards.render import to_markdown
    if as_json:
        print(json.dumps(card.model_dump(mode="json"), indent=2))
    else:
        console.print(Markdown(to_markdown(card)))
    if save:
        console.print(f"[dim]saved {save_card(card)}")


def _factory():
    from harness_factory.runner.runner import Factory
    return Factory.load()


def _build(fn, *args, **kw):
    try:
        return fn(*args, **kw)
    except KeyError as e:
        console.print(f"[red]{str(e).strip(chr(39))}")
        raise typer.Exit(code=2) from None


@card_app.command("model")
def model(canonical: str = typer.Argument(..., help="canonical model, e.g. gpt-oss-120b or scripted/replay"),
          as_json: bool = JSON, save: bool = SAVE) -> None:
    """Every provider serving a model: provider id, caps, context window, prices, limits, measured runs."""
    from harness_factory.cards.build import model_card
    _emit(_build(model_card, _factory(), canonical), as_json, save)


@card_app.command("harness")
def harness(recipe: str = typer.Argument(..., help="recipe id, e.g. v2-base"),
            as_json: bool = JSON, save: bool = SAVE) -> None:
    """A recipe fully resolved: shape, what it changes vs its base, prompts, tools, permissions, budgets."""
    from harness_factory.cards.build import harness_card
    _emit(_build(harness_card, _factory(), recipe), as_json, save)


@card_app.command("task")
def task(task_id: str = typer.Argument(...), as_json: bool = JSON, save: bool = SAVE) -> None:
    """A task: instruction, class, seed, allowed tools, checks with weights, reference plan, measured runs."""
    from harness_factory.cards.build import task_card
    _emit(_build(task_card, _factory(), task_id), as_json, save)


@card_app.command("tool")
def tool(server: str = typer.Argument(..., help="MCP server id from catalog/tools.yaml, e.g. fs"),
         as_json: bool = JSON, save: bool = SAVE) -> None:
    """An MCP server: transport, every tool's side effect, risk, timeout, grants, frozen schema."""
    from harness_factory.cards.build import tool_card
    _emit(_build(tool_card, _factory(), server), as_json, save)


@card_app.command("fault")
def fault(fault_id: str = typer.Argument(...), as_json: bool = JSON, save: bool = SAVE) -> None:
    """A fault profile's rules."""
    from harness_factory.cards.build import fault_card
    _emit(_build(fault_card, _factory(), fault_id), as_json, save)


@card_app.command("spec")
def spec(task_id: str = typer.Option(..., "--task"), recipe: str = typer.Option(..., "--recipe"),
         model: str = typer.Option(..., "--model"), provider: str = typer.Option(..., "--provider"),
         seed: int = typer.Option(0, "--seed"), fault: str | None = typer.Option(None, "--fault"),
         prompts: bool = typer.Option(True, "--prompts/--no-prompts", help="include the rendered prompts"),
         run: bool = typer.Option(False, "--run", help="after showing the card, run the experiment"),
         yes: bool = typer.Option(False, "--yes", "-y", help="with --run: don't ask first"),
         as_json: bool = JSON, save: bool = SAVE) -> None:
    """The final spec card: everything one run will use (RunKey, the exact tools and prompts the model sees,
    budgets, validation, cost estimate, earlier results) and a READY / BLOCKED verdict. Nothing runs unless --run."""
    import asyncio

    from harness_factory.cards.build import attach_result, spec_card
    from harness_factory.cards.render import save as save_card
    f = _factory()
    card = _build(spec_card, f, task_id, recipe, model, provider, seed, fault, prompts)
    _emit(card, as_json, save and not run)
    if not run:
        raise typer.Exit(code=0 if card.verdict == "READY" else 1)
    if card.verdict != "READY":
        console.print("[red]BLOCKED: fix the issues above first; nothing was run.")
        raise typer.Exit(code=1)
    if not yes and not typer.confirm(f"Run {card.summary}?", default=False):
        raise typer.Exit(code=0)
    from harness_factory.runner.runner import run_one
    rec = asyncio.run(run_one(f, f.tasks.get(task_id), f.recipes[recipe], model, provider, seed, fault=fault))
    final = attach_result(card, rec)
    res = final.result
    console.rule("Result")
    console.print(f"status [bold]{res['status']}[/]   E0 {res['E0']}   outcome {res['outcome']}   "
                  f"Q {res['trajectory']}   calls {res['model_calls']}/{res['tool_calls']}   tokens {res['tokens']:,}")
    if res["failed_checks"]:
        console.print(f"[yellow]failed checks: {', '.join(res['failed_checks'])}")
    console.print(f"[dim]transcript {res['transcript']}")
    console.print(f"final spec card saved: {save_card(final)}")


def register(app: typer.Typer) -> None:
    app.add_typer(card_app, name="card")
