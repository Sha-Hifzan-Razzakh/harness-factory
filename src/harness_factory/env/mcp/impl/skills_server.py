"""skills MCP server: serves the run's skills (skills dimension, loading: on_demand).

The provisioner installs the recipe's skills into the run's state folder (HF_SKILLS, outside the workspace), so
the model can read them but its file tools can't see or change them, and the workspace diff never includes them.
Standard library only, so it runs inside the sandbox image.
"""

from __future__ import annotations

import os
from pathlib import Path

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError  # message reaches the model; ValueError would be masked

app = MCPServer("skills", instructions="How-to notes for this kind of work. Load one by name when it fits the task.")


def _root() -> Path:
    return Path(os.environ.get("HF_SKILLS", "/nonexistent")).resolve()


def _available() -> list[str]:
    root = _root()
    return sorted(p.parent.name for p in root.glob("*/SKILL.md")) if root.is_dir() else []


@app.tool()
def load(name: str) -> str:
    """Read one skill: a short how-to note, by the name given in the skills list."""
    names = _available()
    if name not in names:
        raise ToolError(f"no skill named {name!r}; available: {', '.join(names) or 'none'}")
    text = (_root() / name / "SKILL.md").read_text()
    return text.split("\n---\n", 1)[1].strip() if text.startswith("---\n") and "\n---\n" in text[4:] else text


if __name__ == "__main__":
    app.run()
