"""`hfx perturb`: (re)materialise perturbation variants from catalog/perturbations (TSK-04, tasks/perturb.py)."""

from __future__ import annotations

import typer
from rich.console import Console

console = Console()
TASKS = typer.Option(None, "--task", help="a base or variant id (repeatable); default every spec")


def register(app: typer.Typer) -> None:
    @app.command("perturb")
    def perturb(task: list[str] | None = TASKS,
                check: bool = typer.Option(False, "--check",
                                           help="change nothing; exit 1 if the committed variants drift from "
                                                "their specs (CI)")) -> None:
        """Generate perturbed variants of base tasks from catalog/perturbations/<base>.yaml: one task folder per
        variant (catalog/tasks/<base>--<type>-<n>), deterministically. Never edit those folders by hand."""
        from harness_factory import settings
        from harness_factory.env.mcp.servers import load_tool_catalog
        from harness_factory.tasks.perturb import PerturbError, materialise

        try:
            out = materialise(settings.TASKS_DIR, settings.CATALOG / "perturbations", only=task or None, check=check,
                              declared=load_tool_catalog(settings.TOOLS_FILE))
        except PerturbError as e:
            console.print(f"[red]{e}")
            raise typer.Exit(code=1) from None
        if check:
            for line in out.drift:
                console.print(f"[red]drift[/] {line}")
            console.print(f"{out.variants} variant(s): " + (f"[red]{len(out.drift)} difference(s); run `hfx perturb`"
                                                             if out.drift else "[green]in sync with their specs"))
            raise typer.Exit(code=1 if out.drift else 0)
        for v in out.written:
            console.print(f"[green]wrote[/] catalog/tasks/{v}")
        for v in out.removed:
            console.print(f"[yellow]removed[/] catalog/tasks/{v} (no spec asks for it)")
        console.print(f"{out.variants} variant(s); {len(out.written)} written, {len(out.removed)} removed. "
                      f"Next: `hfx reference`.")
