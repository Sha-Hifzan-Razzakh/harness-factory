"""fs MCP server: file tools confined to the run's workspace (HF_WORKSPACE)."""

from __future__ import annotations

import os
from pathlib import Path

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError  # message reaches the model; ValueError would be masked

app = MCPServer("fs", instructions="File tools for the task workspace. Paths are relative to the workspace root.")


def _root() -> Path:
    return Path(os.environ["HF_WORKSPACE"]).resolve()


def _resolve(path: str) -> Path:
    root = _root()
    p = (root / path).resolve()
    if not p.is_relative_to(root):
        raise ToolError(f"path escapes the workspace: {path}")
    return p


@app.tool()
def list_dir(path: str = ".") -> list[str]:
    """List files and folders under a workspace path. Folders end with '/'."""
    p = _resolve(path)
    if not p.is_dir():
        raise ToolError(f"not a directory: {path}")
    return sorted(f"{c.name}/" if c.is_dir() else c.name for c in p.iterdir())


@app.tool()
def read_file(path: str) -> str:
    """Read a UTF-8 text file from the workspace."""
    p = _resolve(path)
    if not p.is_file():
        raise ToolError(f"no such file: {path}")
    return p.read_text()


@app.tool()
def write_file(path: str, content: str) -> str:
    """Create or overwrite a UTF-8 text file in the workspace. Parent folders are created."""
    p = _resolve(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return f"wrote {len(content)} characters to {path}"


@app.tool()
def delete_file(path: str) -> str:
    """Delete a file from the workspace. This cannot be undone."""
    p = _resolve(path)
    if not p.is_file():
        raise ToolError(f"no such file: {path}")
    p.unlink()
    return f"deleted {path}"


if __name__ == "__main__":
    app.run()
