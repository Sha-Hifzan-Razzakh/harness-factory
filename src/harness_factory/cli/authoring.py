"""`hfx new` and `hfx catalog`: add, change, inspect and remove catalog items (ADR 0009).

Every command goes through AuthoringService, the same code the API and the UI use: the change is staged,
checked with the factory's own validators and written only if it passes. A change to an existing identity
(a recipe hash or a tool environment) is shown and needs --yes.

Exit codes: 0 applied or dry run, 1 rejected, 2 needs --yes.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import typer
import yaml
from rich.console import Console
from rich.table import Table

from harness_factory.authoring.models import KINDS, ChangeResult
from harness_factory.authoring.service import AuthoringError, AuthoringService

console = Console()
new_app = typer.Typer(no_args_is_help=True, help="Add a prompt, skill, recipe, tool server or fault profile.")
catalog_app = typer.Typer(no_args_is_help=True, help="List, show, check and remove catalog items.")

DRY = typer.Option(False, "--dry-run", help="check and show the impact; write nothing")
YES = typer.Option(False, "--yes", "-y", help="confirm a change to an existing recipe hash or tool environment")
UPDATE = typer.Option(False, "--update", help="replace an existing item instead of creating a new one")


def register(app: typer.Typer) -> None:
    app.add_typer(new_app, name="new")
    app.add_typer(catalog_app, name="catalog")


def _service() -> AuthoringService:
    return AuthoringService()


def _fail(msg: str) -> None:
    console.print(f"[red]{msg}")
    raise typer.Exit(code=1)


def _read(name: str | Path | None) -> str | None:
    if name is None:
        return None
    if str(name) == "-":
        return sys.stdin.read()
    path = Path(name)
    if not path.is_file():
        _fail(f"no such file: {path}")
    return path.read_text()


def _spec(path: str) -> dict[str, Any]:
    data = yaml.safe_load(_read(path) or "") or {}
    if not isinstance(data, dict):
        _fail(f"{path}: expected a YAML mapping")
    return data


def _set(fields: dict[str, Any], assignments: list[str]) -> dict[str, Any]:
    """`a.b=VALUE` assignments into nested fields; VALUE is read as YAML (so lists and numbers work)."""
    for a in assignments:
        key, sep, raw = a.partition("=")
        if not sep or not key:
            _fail(f"--set {a!r}: expected key.path=value")
        node = fields
        *path, last = key.split(".")
        for k in path:
            node = node.setdefault(k, {})
            if not isinstance(node, dict):
                _fail(f"--set {a!r}: {k} is not a mapping")
        node[last] = yaml.safe_load(raw)
    return fields


def show_result(r: ChangeResult) -> None:
    color = {"applied": "green", "dry_run": "cyan", "needs_confirm": "yellow", "rejected": "red"}[r.status]
    console.print(f"[{color}]{r.status.replace('_', ' ')}[/] {r.action} {r.kind} {r.id}")
    for i in r.issues:
        console.print(f"  [red]✗[/] {i.where}: {i.detail}")
    if r.status == "rejected":
        console.print("  nothing was written")
        return
    for f in r.files:
        console.print(f"  {f.action:<6} {f.path}")
    for n in r.notes:
        console.print(f"  [dim]note:[/] {n}")
    imp = r.impact
    if imp.recipes_added:
        console.print(f"  new recipes: {', '.join(imp.recipes_added)}")
    if imp.recipes_changed:
        console.print(f"  [yellow]recipe hash changes:[/] {', '.join(imp.recipes_changed)}")
    if imp.recipes_removed:
        console.print(f"  [yellow]recipes removed:[/] {', '.join(imp.recipes_removed)}")
    for sid, rs in imp.env_changed.items():
        console.print(f"  [yellow]tool environment of {sid} changes[/] for: {', '.join(rs) or '(no recipe)'}")
    if imp.touches_existing and imp.stale_results:
        console.print(f"  [yellow]{imp.stale_results} stored run(s) belong to the old identities[/] (kept as history; "
                      f"new runs start new cells)")
    if r.status == "needs_confirm":
        console.print("  [yellow]this changes existing identities: run again with --yes to write it")
    if r.status == "applied" and r.files:
        console.print("  [dim]files are written, not committed: review with git diff[/]")


def _run(kind: str, payload: dict[str, Any], update: bool, dry_run: bool, yes: bool) -> None:
    svc = _service()
    try:
        r = (svc.update if update else svc.create)(kind, payload, dry_run=dry_run, confirm=yes)
    except AuthoringError as e:
        _fail(str(e))
    show_result(r)
    raise typer.Exit(code={"applied": 0, "dry_run": 0, "rejected": 1, "needs_confirm": 2}[r.status])


def _edit(template: str, what: str) -> str:
    if not sys.stdin.isatty():
        _fail(f"give the {what} with a flag or a file (no terminal to open an editor in)")
    text = typer.edit(template)
    if not text or not text.strip() or text == template:
        _fail(f"no {what} given")
    return text


# ---- hfx new ---------------------------------------------------------------------------------------------------

@new_app.command("prompt")
def new_prompt(ref: str = typer.Argument(..., help="<layer>/<name>, e.g. system/terse"),
               file: str | None = typer.Option(None, "--file", "-f", help="template file ('-' = stdin)"),
               text: str | None = typer.Option(None, "--text", help="template text"),
               update: bool = UPDATE, dry_run: bool = DRY, yes: bool = YES) -> None:
    """A Jinja2 prompt template. System prompts get `tools` and `recipe`; task prompts get `task`."""
    body = text or _read(file) or _edit("You are an automation agent...\n", "prompt text")
    _run("prompt", {"ref": ref, "text": body}, update, dry_run, yes)


@new_app.command("skill")
def new_skill(skill_id: str = typer.Argument(..., metavar="ID"),
              name: str | None = typer.Option(None, "--name"),
              description: str | None = typer.Option(None, "--description", help="one line: when it applies"),
              body_file: str | None = typer.Option(None, "--body-file", "-f", help="the how-to ('-' = stdin)"),
              update: bool = UPDATE, dry_run: bool = DRY, yes: bool = YES) -> None:
    """A general how-to a recipe can give the model (never a task's answer)."""
    name = name or typer.prompt("Name (a title for the how-to)")
    description = description or typer.prompt("Description (one line: when it applies)")
    body = _read(body_file) or _edit("1. ...\n2. ...\n", "skill body")
    _run("skill", {"id": skill_id, "name": name, "description": description, "body": body}, update, dry_run, yes)


@new_app.command("recipe")
def new_recipe(recipe_id: str = typer.Argument(..., metavar="ID"),
               base: str = typer.Option("v2-base", "--base", help="recipe to start from; 'none' for a base recipe"),
               sets: list[str] = typer.Option([], "--set", help="field.path=value (YAML value); repeatable"),  # noqa: B008
               file: str | None = typer.Option(None, "--file", "-f", help="YAML mapping of the fields to set"),
               note: str = typer.Option("", "--note", help="what this recipe changes and why (file comment)"),
               update: bool = UPDATE, dry_run: bool = DRY, yes: bool = YES) -> None:
    """A recipe. With a base it is a matrix recipe: the base plus the one dimension you change, e.g.
    `hfx new recipe v2-terse --set prompts.system=system/terse`."""
    fields = _set(_spec(file) if file else {}, sets)
    _run("recipe", {"id": recipe_id, "base": None if base.lower() == "none" else base, "fields": fields,
                    "note": note}, update, dry_run, yes)


@new_app.command("tool")
def new_tool(server_id: str | None = typer.Argument(None, metavar="SERVER", help="new server id (a Python name)"),
             spec: str | None = typer.Option(None, "--spec", help="YAML: id, instructions, tools[], optional code"),
             code: str | None = typer.Option(None, "--code", help="the whole server module (else a stub)"),
             tool: str | None = typer.Option(None, "--tool", help="one tool's name (quick form, no --spec)"),
             side_effect: str = typer.Option("READ", "--side-effect", help="READ | REVERSIBLE | IRREVERSIBLE"),
             risk: str = typer.Option("R0", "--risk", help="R0..R3"),
             description: str = typer.Option("", "--description"),
             params: list[str] = typer.Option([], "--param",  # noqa: B008
                                       help="name:type for the stub (str|int|float|bool)"),
             update: bool = UPDATE, dry_run: bool = DRY, yes: bool = YES) -> None:
    """A local tool server (runs in the offline sandbox; imports only mcp and the standard library).
    Quick form: `hfx new tool calc --tool add --param a:float --param b:float` writes a stub to fill in."""
    if spec:
        payload = _spec(spec)
        if payload.get("code_file") and not code:
            code = str(Path(spec).parent / payload.pop("code_file"))
    elif server_id and tool:
        ps = []
        for p in params:
            n, _, t = p.partition(":")
            ps.append({"name": n, "type": t or "str"})
        payload = {"id": server_id, "instructions": description,
                   "tools": [{"name": tool, "description": description, "side_effect": side_effect.upper(),
                              "risk": risk.upper(), "params": ps}]}
    else:
        _fail("give --spec FILE, or SERVER with --tool NAME")
    if server_id:
        payload["id"] = server_id
    if code:
        payload["code"] = _read(code)
    payload.setdefault("transport", "stdio")
    _run("server", payload, update, dry_run, yes)


@new_app.command("remote-tool")
def new_remote_tool(spec: str = typer.Option(..., "--spec", help="YAML: id, url, version, auth_env, tools[]"),
                    update: bool = UPDATE, dry_run: bool = DRY, yes: bool = YES) -> None:
    """An existing MCP server reached over streamable HTTP. auth_env names the env var with the token."""
    payload = _spec(spec)
    payload["transport"] = "streamable_http"
    _run("server", payload, update, dry_run, yes)


@new_app.command("fault")
def new_fault(spec: str = typer.Option(..., "--spec", help="YAML: id, description, rules[]"),
              update: bool = UPDATE, dry_run: bool = DRY, yes: bool = YES) -> None:
    """A fault profile: tools that fail on purpose, to measure robustness."""
    _run("fault", _spec(spec), update, dry_run, yes)


# ---- hfx catalog -----------------------------------------------------------------------------------------------

@catalog_app.command("list")
def catalog_list(kind: str | None = typer.Argument(None, help=f"one of {', '.join(KINDS)}")) -> None:
    """Every item, with what uses it."""
    try:
        items = _service().list(kind)
    except AuthoringError as e:
        _fail(str(e))
    t = Table("kind", "id", "summary", "used by", "hash")
    for it in items:
        mark = " [dim](built-in)[/]" if it.builtin else ""
        bad = " [red]✗[/]" if it.issues else ""
        used = ", ".join(it.used_by[:3]) + (f" +{len(it.used_by) - 3}" if len(it.used_by) > 3 else "")
        t.add_row(it.kind, it.id + mark + bad, it.summary, used or "-", it.hash or "")
    console.print(t)


@catalog_app.command("show")
def catalog_show(kind: str, item_id: str = typer.Argument(..., metavar="ID")) -> None:
    """One item: its file, what uses it and any issue."""
    try:
        it = _service().get(kind, item_id)
    except AuthoringError as e:
        _fail(str(e))
    console.print(f"[bold]{it.kind} {it.id}[/]  {it.path or ''}  {it.hash or ''}")
    console.print(f"used by: {', '.join(it.used_by) or '-'}")
    for i in it.issues:
        console.print(f"[red]✗[/] {i}")
    if it.text:
        console.rule()
        console.print(it.text, markup=False, highlight=False)
    if it.detail.get("code"):
        console.rule(it.path or "")
        console.print(it.detail["code"], markup=False, highlight=False)


@catalog_app.command("check")
def catalog_check() -> None:
    """Everything wrong with the catalog as it is: load errors and recipe issues."""
    issues = _service().errors()
    for i in issues:
        console.print(f"[red]✗[/] {i.where}: {i.detail}")
    console.print("catalog ok" if not issues else f"{len(issues)} issue(s)")
    raise typer.Exit(code=1 if issues else 0)


@catalog_app.command("rm")
def catalog_rm(kind: str, item_id: str = typer.Argument(..., metavar="ID"), dry_run: bool = DRY,
               yes: bool = YES) -> None:
    """Remove an item nothing uses any more."""
    try:
        r = _service().delete(kind, item_id, dry_run=dry_run, confirm=yes)
    except AuthoringError as e:
        _fail(str(e))
    show_result(r)
    raise typer.Exit(code={"applied": 0, "dry_run": 0, "rejected": 1, "needs_confirm": 2}[r.status])
