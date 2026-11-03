"""Local tool servers written through authoring: the rules a server module must follow, a stub generator, and
discovery of the tools a module really serves.

A local server runs inside every run's sandbox (no network) and its module goes in the sandbox image, so it may
import only `mcp` and the standard library (CLAUDE.md, "Isolation"). Errors meant for the model are raised as
`ToolError`: MCP 2.x hides other exception messages from the model.
"""

from __future__ import annotations

import ast
import asyncio
import re
import sys
import tempfile
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

IMPL_DIR = "src/harness_factory/env/mcp/impl"
MODULE_PREFIX = "harness_factory.env.mcp.impl."
ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,30}$")
ALLOWED_TOP = {"mcp", "__future__"}


class Param(BaseModel):
    """A parameter of a generated stub tool."""
    name: str
    type: Literal["str", "int", "float", "bool"] = "str"
    description: str = ""
    default: str | int | float | bool | None = None
    required: bool = True


class ToolDef(BaseModel):
    name: str
    description: str = ""
    side_effect: Literal["READ", "REVERSIBLE", "IRREVERSIBLE"]
    risk: Literal["R0", "R1", "R2", "R3"]
    timeout_s: float = Field(10.0, gt=0)
    granted_by: list[str] | None = None
    params: list[Param] = Field(default_factory=list)     # used only to generate a stub


def module_path(server_id: str) -> str:
    return f"{IMPL_DIR}/{server_id}_server.py"


def module_name(server_id: str) -> str:
    return f"{MODULE_PREFIX}{server_id}_server"


def check_code(code: str) -> list[str]:
    """Static rules for a server module. Returns problems; empty means it may be discovered."""
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return [f"syntax error, line {e.lineno}: {e.msg}"]
    problems: list[str] = []
    stdlib = set(sys.stdlib_module_names)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level:
            problems.append(f"line {node.lineno}: relative import; a server module stands alone")
        names = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            names = [node.module]
        for n in names:
            top = n.split(".")[0]
            if top not in stdlib and top not in ALLOWED_TOP:
                problems.append(f"line {node.lineno}: imports {n}; a server may import only mcp and the "
                                f"standard library (it runs in the offline sandbox)")
        if isinstance(node, ast.Raise) and node.exc is not None:
            exc = node.exc.func if isinstance(node.exc, ast.Call) else node.exc
            if isinstance(exc, ast.Name) and exc.id == "ValueError":
                problems.append(f"line {node.lineno}: raises ValueError; raise ToolError so the model sees the "
                                f"message")
    has_app = any(isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "app" for t in n.targets)
                  and isinstance(n.value, ast.Call) and getattr(n.value.func, "id", None) == "MCPServer"
                  for n in tree.body)
    if not has_app:
        problems.append("no `app = MCPServer(...)` at module level")
    has_main = any(isinstance(n, ast.If) and "__main__" in ast.unparse(n.test) and "app.run()" in ast.unparse(n)
                   for n in tree.body)
    if not has_main:
        problems.append('no `if __name__ == "__main__": app.run()` at the end')
    return problems


def _annotation(p: Param) -> str:
    return p.type if p.required else f"{p.type} = {p.default!r}"


def stub(server_id: str, instructions: str, tools: list[ToolDef]) -> str:
    """A server module with one function per tool that refuses to run until it is filled in."""
    lines = [
        (f'"""{server_id} MCP server (authored). Fill in each tool; workspace paths come from HF_WORKSPACE, the '
         f'task database from HF_DB."""'),
        "",
        "from __future__ import annotations",
        "",
        "import os",
        "from pathlib import Path",
        "",
        "from mcp.server.mcpserver import MCPServer",
        ("from mcp.server.mcpserver.exceptions import ToolError  # message reaches the model; ValueError would be "
         "masked"),
        "",
        f"app = MCPServer({server_id!r}, instructions={instructions!r})",
        "",
        "",
        "def _root() -> Path:",
        '    return Path(os.environ["HF_WORKSPACE"]).resolve()',
    ]
    for t in tools:
        ordered = [p for p in t.params if p.required] + [p for p in t.params if not p.required]
        sig = ", ".join(f"{p.name}: {_annotation(p)}" for p in ordered)
        doc = t.description or f"{t.name} ({t.side_effect.lower()})."
        args = "\n".join(f"        {p.name}: {p.description}" for p in ordered if p.description)
        lines += ["", "", "@app.tool()", f"def {t.name}({sig}) -> str:",
                  f'    """{doc}' + (f"\n\n    Args:\n{args}\n    " if args else "") + '"""',
                  f'    raise ToolError("{t.name} is not implemented yet")']
    lines += ["", "", 'if __name__ == "__main__":', "    app.run()", ""]
    return "\n".join(lines)


async def _discover(module_file: Path, server_id: str) -> list:
    from harness_factory.env.mcp.servers import MCPServerSpec
    from harness_factory.mcp.client import MCPClient

    with tempfile.TemporaryDirectory(prefix="hf-discover-") as d:
        work, state = Path(d, "work"), Path(d, "state")
        work.mkdir()
        state.mkdir()
        env = {"HF_WORKSPACE": str(work), "HF_DB": str(state / "task.db"), "HF_SKILLS": str(state / "skills")}
        spec = MCPServerSpec(id=server_id, module=module_name(server_id))
        launcher = lambda _spec: (sys.executable, ["-I", str(module_file)], env)
        async with MCPClient(spec, env, launcher=launcher) as client:
            return await asyncio.wait_for(client.list_tools(), timeout=30)


def discover_tools(module_file: Path, server_id: str) -> dict:
    """Start the module as the sandbox would (a stdio server in a scratch workspace) and list its tools:
    name -> DiscoveredTool (schema and description). Raises on a module that doesn't start or answer."""
    return {t.name: t for t in asyncio.run(_discover(module_file, server_id))}


def discover(module_file: Path, server_id: str) -> dict[str, dict]:
    """Like discover_tools, but name -> input schema only."""
    return {name: t.input_schema for name, t in discover_tools(module_file, server_id).items()}
