"""hfx catalog history | diff | restore | purge | tag | activity | sync | archived | results, and hfx new model:
the catalog history (authoring/history.py) from the terminal."""

from __future__ import annotations

import typer
from rich.console import Console
from rich.syntax import Syntax
from rich.table import Table

console = Console()
STATUS_EXIT = {"applied": 0, "dry_run": 0, "rejected": 1, "needs_confirm": 2}
TAGS = typer.Argument(..., help="e.g. baseline best; -best removes 'best'")


def register(new_app: typer.Typer, catalog_app: typer.Typer) -> None:
    from harness_factory.cli.authoring import DRY, UPDATE, WHY, YES, _fail, _run, _service, show_result

    def svc():
        return _service()

    def guard(fn, *a, **kw):
        from harness_factory.authoring.service import AuthoringError
        try:
            return fn(*a, **kw)
        except (AuthoringError, KeyError) as e:
            _fail(str(e).strip("'\""))

    @new_app.command("model")
    def new_model(item: str = typer.Argument(..., metavar="PROVIDER/MODEL", help="e.g. groq/gpt-oss-120b"),
                  provider_model_id: str = typer.Option(..., "--id", help="the provider's own model id"),
                  price_in: float = typer.Option(0.0, "--price-in", help="$ per million input tokens"),
                  price_out: float = typer.Option(0.0, "--price-out", help="$ per million output tokens"),
                  free: bool = typer.Option(False, "--free"),
                  context_len: int = typer.Option(32_768, "--context"),
                  tool_calling: str = typer.Option("native", "--tool-calling", help="native, emulated or none"),
                  update: bool = UPDATE, dry_run: bool = DRY, yes: bool = YES, why: str = WHY) -> None:
        """A model a provider serves (catalog/providers.yaml; only its own line changes, comments stay)."""
        provider, _, canonical = item.partition("/")
        if not canonical:
            _fail("give PROVIDER/MODEL, e.g. groq/gpt-oss-120b")
        _run("model", {"provider": provider, "canonical": canonical, "provider_model_id": provider_model_id,
                       "price_in": price_in, "price_out": price_out, "free": free,
                       "caps": {"context_len": context_len, "tool_calling": tool_calling}}, update, dry_run, yes, why)

    @catalog_app.command("history")
    def history(kind: str, item_id: str = typer.Argument(..., metavar="ID")) -> None:
        """Every version of an item, newest first: who, when, why, and what it did to recipe identities."""
        h = guard(svc().history, kind, item_id)
        state = "archived" if h["archived"] else ("live" if h["live"] else "gone")
        console.print(f"[bold]{h['kind']} {h['id']}[/]  {state}  current hash {h['current_hash'] or '-'}")
        if h["changed_outside"]:
            console.print("[yellow]changed outside authoring since the last version: run `hfx catalog sync`")
        t = Table("v", "when (UTC)", "who", "action", "hash", "why", "recipes re-hashed", "tags")
        for v in h["versions"]:
            changed = v["impact"].get("recipes_changed", []) if v["impact"] else []
            t.add_row(str(v["version"]), v["at"].replace("T", " ").rstrip("Z"), v["author"], v["action"],
                      v["hash_after"] or "-", v["note"] or (v.get("cause") or ""), ", ".join(changed) or "-",
                      ", ".join(v["tags"]))
        console.print(t if h["versions"] else "no history yet: `hfx catalog sync` records the current state as v1")

    @catalog_app.command("diff")
    def diff(kind: str, item_id: str = typer.Argument(..., metavar="ID"),
             a: str = typer.Argument(..., help="version number"),
             b: str = typer.Argument("current", help="version number, or current")) -> None:
        """What changed between two versions of an item."""
        text = guard(svc().diff_versions, kind, item_id, a, b)
        console.print(Syntax(text, "diff") if text else "no difference")

    @catalog_app.command("restore")
    def restore(kind: str, item_id: str = typer.Argument(..., metavar="ID"),
                version: int = typer.Argument(...), dry_run: bool = DRY, yes: bool = YES, why: str = WHY) -> None:
        """Bring back an earlier version (or an archived item) as a new version, checked like any change."""
        r = guard(svc().restore, kind, item_id, version, why, dry_run, yes)
        if r.diff and r.status != "rejected":
            console.print(Syntax(r.diff, "diff"))
        show_result(r)
        raise typer.Exit(code=STATUS_EXIT[r.status])

    @catalog_app.command("purge")
    def purge(kind: str, item_id: str = typer.Argument(..., metavar="ID")) -> None:
        """Remove an archived item's history for good (refused if any stored run used it)."""
        r = guard(svc().purge, kind, item_id)
        show_result(r)
        raise typer.Exit(code=STATUS_EXIT[r.status])

    @catalog_app.command("tag")
    def tag(kind: str, item_id: str = typer.Argument(..., metavar="ID"), version: int = typer.Argument(...),
            tags: list[str] = TAGS) -> None:
        """Label a version (baseline, best, ...)."""
        guard(svc().tag, kind, item_id, version, tags)
        console.print(f"tagged {kind} {item_id} v{version}: {' '.join(tags)}")

    @catalog_app.command("activity")
    def activity(limit: int = typer.Option(40, "--limit"), kind: str | None = typer.Option(None, "--kind")) -> None:
        """Every recorded change, newest first."""
        t = Table("#", "when (UTC)", "who", "item", "action", "v", "why")
        for e in guard(svc().activity, limit, kind):
            t.add_row(str(e["seq"]), e["at"].replace("T", " ").rstrip("Z"), e["author"], f"{e['kind']} {e['id']}",
                      e["action"] + (" " + " ".join(e["tags"]) if e["tags"] else ""), str(e["version"] or ""),
                      e["note"] or (e.get("cause") or ""))
        console.print(t)

    @catalog_app.command("sync")
    def sync() -> None:
        """Record changes made outside authoring (editor, git pull) since the last version, and a first version of
        every item that has no history yet."""
        made = guard(svc().sync)
        first = [m for m in made if m["action"] == "baseline"]
        other = [m for m in made if m["action"] != "baseline"]
        if first:
            console.print(f"history started for {len(first)} item(s) (their current state is v1)")
        for m in other:
            console.print(f"[yellow]{m['kind']} {m['id']}[/] v{m['version']}: {m['note']}")
        if not made:
            console.print("nothing changed outside authoring")

    @catalog_app.command("archived")
    def archived() -> None:
        """Archived items: out of the catalog, history kept, restorable."""
        t = Table("item", "archived (UTC)", "who", "why", "restore with")
        for e in guard(svc().archived_items):
            t.add_row(f"{e['kind']} {e['id']}", e["at"].replace("T", " ").rstrip("Z"), e["author"], e["note"],
                      f"hfx catalog restore {e['kind']} {e['id']} {max(1, (e['version'] or 2) - 1)}")
        console.print(t)

    @catalog_app.command("results")
    def results(kind: str, item_id: str = typer.Argument(..., metavar="ID")) -> None:
        """Scored runs per version of an item: did the change help?"""
        r = guard(svc().results_by_version, kind, item_id)
        t = Table("version", "runs", "mean E0", "SUCCESS", "mean tokens", f"by {r['grouped_by']}")
        for v in r["versions"]:
            by = "; ".join(f"{k} {x['mean_e0']:.3f} (n={x['n']})" for k, x in v["by"].items())
            t.add_row(str(v["version"]), str(v["n"]), f"{v['mean_e0']:.3f}", f"{v['success']:.0%}",
                      f"{v['tokens']:,}", by)
        console.print(t if r["versions"] else "no scored runs of any version yet")
