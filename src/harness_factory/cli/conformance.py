"""`hfx conformance`: the MCP conformance suite over every declared server (MCP-11, mcp/conformance.py)."""

from __future__ import annotations

import asyncio

import typer
from rich.console import Console
from rich.table import Table

console = Console()
SERVERS = typer.Option(None, "--server", help="a declared server id (repeatable); default every server")


def register(app: typer.Typer) -> None:
    @app.command("conformance")
    def conformance(server: list[str] | None = SERVERS,
                    sandbox: str | None = typer.Option(None, "--sandbox",
                                                       help="local or docker; default HF_SANDBOX"),
                    concurrency: int = typer.Option(4, "--concurrency", "-j")) -> None:
        """Check every MCP server in tools.yaml against the protocol and its declaration: initialize, list_tools
        (frozen hashes), schemas, a happy-path call per tool, unknown tools, invalid arguments, documented refusals,
        recovery after errors and a clean shutdown. Each server needs catalog/conformance/<id>.yaml. Exit 1 on any
        failure; a remote server is checked only with HF_ALLOW_REMOTE=1 (otherwise reported as skipped)."""
        from harness_factory import settings
        from harness_factory.env.mcp.servers import load_tool_catalog
        from harness_factory.mcp import conformance as conf

        declared = load_tool_catalog(settings.TOOLS_FILE)
        unknown = [s for s in server or [] if s not in {x.id for x in declared.servers}]
        if unknown:
            console.print(f"[red]not declared in tools.yaml: {', '.join(unknown)}")
            raise typer.Exit(code=1)
        reports = asyncio.run(conf.check_all(declared, server or None, sandbox_kind=sandbox, concurrency=concurrency))
        t = Table("server", "transport", "sandbox", "result", "failing rules", title="MCP conformance")
        for r in reports:
            bad = [f.rule for f in r.failures()]
            result = "[yellow]skipped" if r.skipped else "[green]ok" if r.ok else \
                f"[red]FAIL ({len(conf.RULES) - len(bad)}/{len(conf.RULES)} rules)"
            t.add_row(r.server, r.transport, r.sandbox or "-", result, ", ".join(bad))
        console.print(t)
        failed = [r for r in reports if not r.ok and not r.skipped]
        for r in failed:
            for f in r.failures():
                console.print(f"[red]{r.server} {f.rule}[/]: {f.detail}")
        for r in reports:
            if r.skipped:
                console.print(f"[yellow]{r.server} skipped[/]: {r.skipped}")
        stale = [] if server else conf.stale_fixtures(declared)
        for p in stale:
            console.print(f"[red]stale fixture[/] {p}: no server {p.stem!r} in tools.yaml (rename or remove it)")
        if failed or stale:
            console.print(f"[red]{len(failed)} server(s) fail conformance")
            raise typer.Exit(code=1)
        ok = [r for r in reports if r.ok]
        console.print(f"[green]{len(ok)} server(s) conform[/]" + (f", {len(reports) - len(ok)} skipped"
                                                                    if len(ok) < len(reports) else ""))
