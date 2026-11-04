"""hfx corpus: recorded web corpora (ADR 0018). `list` shows each corpus's version; `freeze` writes it."""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

corpus_app = typer.Typer(no_args_is_help=True, help="Recorded web corpora (catalog/corpora, ADR 0018).")
console = Console()


@corpus_app.command("list")
def corpus_list() -> None:
    """Every corpus: id, version, pages, redirects, recorded 404s, and whether its manifest is frozen."""
    from harness_factory.env.corpus import CorpusError, all_corpora, load_corpus
    t = Table("corpus", "version", "pages", "redirects", "gone", "index id", "manifest")
    for root in all_corpora():
        try:
            c = load_corpus(root)
        except CorpusError as e:
            t.add_row(root.name, "-", "-", "-", "-", "-", f"[red]{e}")
            continue
        info = c.info()
        t.add_row(c.id, c.version, str(info["pages"]), str(info["redirects"]), str(info["gone"]), c.index_id,
                  "[green]frozen" if c.stale() is None else "[red]stale")
    console.print(t)


@corpus_app.command("freeze")
def corpus_freeze(check: bool = typer.Option(False, "--check", help="exit 1 if a manifest is out of date")) -> None:
    """Write each corpus's content address (version) and page count into its corpus.yaml. A changed corpus is a
    new version: every task that names it gets a new hash."""
    from harness_factory.env.corpus import CorpusError, all_corpora, freeze, load_corpus
    bad = 0
    for root in all_corpora():
        try:
            if check:
                c = load_corpus(root)
                stale = c.stale()
                console.print(f"[red]stale[/] {stale}" if stale else f"[green]ok[/] {c.id} {c.version}")
                bad += stale is not None
            else:
                c, changed = freeze(root)
                console.print(f"{'wrote' if changed else 'unchanged'} {c.id} {c.version} ({len(c.pages)} pages)")
        except CorpusError as e:
            console.print(f"[red]FAIL[/] {e}")
            bad += 1
    raise typer.Exit(code=1 if bad else 0)


def validate_corpora(tasks) -> int:
    """For `hfx validate`: every corpus loads and is frozen, and every task's corpus exists. Returns the failures."""
    from harness_factory.env.corpus import CorpusError, all_corpora, load_corpus
    bad = 0
    for root in all_corpora():
        try:
            c = load_corpus(root)
            stale = c.stale()
        except CorpusError as e:
            c, stale = None, str(e)
        console.print(f"[red]FAIL[/] {stale}" if stale else f"[green]ok[/] corpus {c.id} ({c.version})")
        bad += stale is not None
    for task in tasks:
        if task.env_seed.corpus:
            try:
                task.env_seed.web_corpus(task.source_dir)
            except CorpusError as e:
                console.print(f"[red]FAIL[/] task {task.key()}: {e}")
                bad += 1
    return bad


def register(app: typer.Typer) -> None:
    app.add_typer(corpus_app, name="corpus")
