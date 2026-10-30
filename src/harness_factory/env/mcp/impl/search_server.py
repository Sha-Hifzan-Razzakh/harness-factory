"""search MCP server: find files by name and grep their contents, confined to the run's workspace (HF_WORKSPACE).

Read-only, and it reveals nothing that fs.list_dir and fs.read_file don't: a task that allows those gets these
too (`granted_by` in tools.yaml). Standard library only, so it runs inside the sandbox image.
"""

from __future__ import annotations

import os
import re
from fnmatch import fnmatch
from pathlib import Path

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError  # message reaches the model; ValueError would be masked

app = MCPServer("search", instructions="Search the task workspace: find files by name, or grep their contents. "
                                       "Paths are relative to the workspace root.")

MAX_RESULTS = 100
MAX_FILE_BYTES = 1_000_000          # bigger files are skipped by grep
MAX_LINE_CHARS = 300


def _root() -> Path:
    return Path(os.environ["HF_WORKSPACE"]).resolve()


def _resolve(path: str) -> Path:
    root = _root()
    p = (root / path).resolve()
    if not p.is_relative_to(root):
        raise ToolError(f"path escapes the workspace: {path}")
    if not p.exists():
        raise ToolError(f"no such file or folder: {path}")
    return p


def _files(start: Path):
    """Every regular file under start (or start itself), in a stable order, never leaving the workspace."""
    root = _root()
    if start.is_file():
        yield start
        return
    for dirpath, dirnames, filenames in os.walk(start):
        dirnames.sort()
        for name in sorted(filenames):
            p = Path(dirpath) / name
            if p.is_file() and p.resolve().is_relative_to(root):
                yield p


def _rel(p: Path) -> str:
    return p.relative_to(_root()).as_posix()


@app.tool()
def find(pattern: str, path: str = ".") -> list[str]:
    """Find files whose name matches a glob pattern (e.g. "*.ini" or "test_*.py"), searching folders recursively
    from path. Returns workspace-relative paths, at most 100."""
    out = [_rel(p) for p in _files(_resolve(path)) if fnmatch(p.name, pattern)]
    if len(out) > MAX_RESULTS:
        return [*out[:MAX_RESULTS], f"... {len(out) - MAX_RESULTS} more; narrow the pattern or path"]
    return out


@app.tool()
def grep(pattern: str, path: str = ".", glob: str = "*", ignore_case: bool = False) -> list[str]:
    """Search file contents for a regular expression, recursively from path. glob limits which file names are
    searched (e.g. "*.ini"). Returns matches as "path:line: text", at most 100."""
    try:
        rx = re.compile(pattern, re.IGNORECASE if ignore_case else 0)
    except re.error as e:
        raise ToolError(f"invalid regular expression {pattern!r}: {e}") from e
    out: list[str] = []
    more = 0
    for p in _files(_resolve(path)):
        if not fnmatch(p.name, glob) or p.stat().st_size > MAX_FILE_BYTES:
            continue
        try:
            lines = p.read_text().splitlines()
        except (UnicodeDecodeError, OSError):
            continue                                   # binary or unreadable: not searchable text
        for n, line in enumerate(lines, 1):
            if rx.search(line):
                if len(out) < MAX_RESULTS:
                    out.append(f"{_rel(p)}:{n}: {line[:MAX_LINE_CHARS]}")
                else:
                    more += 1
    if more:
        out.append(f"... {more} more matches; narrow the pattern, path or glob")
    return out


if __name__ == "__main__":
    app.run()
