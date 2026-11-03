"""fsx MCP server: file edits finer than fs.write_file, confined to the run's workspace (HF_WORKSPACE).

fs can only rewrite a whole file. fsx adds an exact-string `edit`, `append`, `move`, `mkdir` and a `tree` listing.
It is a separate server so that recipes which don't open it keep their environment hash.

Each tool does nothing the fs tools named in its `granted_by` (tools.yaml) can't do together, so a task that allows
those tools gets it without an edit to the task: `edit` = read_file + write_file, `append` and `mkdir` =
list_dir + write_file, `move` = read_file + write_file + delete_file (the source disappears), `tree` = list_dir.

Paths are resolved (symlinks followed) and refused unless they stay inside the workspace. Every refusal is a
ToolError, whose message reaches the model. Text is read and written as UTF-8 with the same newline handling as
fs.read_file / fs.write_file. Standard library only, so it runs inside the sandbox image.
"""

from __future__ import annotations

import os
from pathlib import Path

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError  # message reaches the model; ValueError would be masked

app = MCPServer("fsx", instructions="Finer file tools for the task workspace: edit part of a file, append, move, "
                                    "make folders, and list a folder tree. Paths are relative to the workspace root.")

MAX_TREE_ENTRIES = 200
MAX_TREE_DEPTH = 6


def _root() -> Path:
    return Path(os.environ["HF_WORKSPACE"]).resolve()


def _resolve(path: str) -> Path:
    root = _root()
    p = (root / path).resolve()              # follows symlinks, so a link out of the workspace is refused too
    if not p.is_relative_to(root):
        raise ToolError(f"path escapes the workspace: {path}")
    return p


def _rel(p: Path) -> str:
    return p.relative_to(_root()).as_posix()           # "." for the root


def _text_file(path: str) -> tuple[Path, str]:
    p = _resolve(path)
    if p.is_dir():
        raise ToolError(f"{path} is a folder, not a file")
    if not p.is_file():
        raise ToolError(f"no such file: {path}")
    try:
        return p, p.read_text(encoding="utf-8")
    except UnicodeDecodeError as e:
        raise ToolError(f"{path} is not a UTF-8 text file") from e
    except OSError as e:
        raise ToolError(f"cannot read {path}: {e.strerror}") from e


@app.tool()
def edit(path: str, old: str, new: str, count: int = 1) -> str:
    """Replace exact text in a UTF-8 file: `old` must occur exactly `count` times (default 1), and every
    occurrence is replaced with `new`. Include enough surrounding text in `old` to make it unique. Fails without
    changing anything if `old` is missing or occurs a different number of times."""
    if not old:
        raise ToolError("old must not be empty; use fsx.append to add text, or fs.write_file to write a file")
    if count < 1:
        raise ToolError(f"count must be 1 or more, got {count}")
    if old == new:
        raise ToolError("old and new are the same; nothing would change")
    p, text = _text_file(path)
    found = text.count(old)
    if found == 0:
        raise ToolError(f"old text not found in {path}; nothing changed. Read the file and copy the text exactly "
                        f"(spaces and line breaks included)")
    if found != count:
        times = "once" if found == 1 else f"{found} times"
        raise ToolError(f"old text occurs {times} in {path}, not {count}; nothing changed. Add surrounding text "
                        f"to pick one occurrence, or pass count={found} to replace them all")
    try:
        p.write_text(text.replace(old, new), encoding="utf-8")
    except OSError as e:
        raise ToolError(f"cannot write {path}: {e.strerror}") from e
    return f"replaced {count} occurrence{'s' if count > 1 else ''} in {path}"


@app.tool()
def append(path: str, text: str) -> str:
    """Append text to the end of an existing UTF-8 file, exactly as given (add a leading line break yourself if
    the file doesn't end with one). The file must exist: create new files with fs.write_file."""
    if not text:
        raise ToolError("text is empty; nothing to append")
    p = _resolve(path)
    if p.is_dir():
        raise ToolError(f"{path} is a folder, not a file")
    if not p.is_file():
        raise ToolError(f"no such file: {path} (fsx.append only extends existing files; create it with "
                        f"fs.write_file)")
    try:
        with p.open("a", encoding="utf-8") as fh:
            fh.write(text)
    except OSError as e:
        raise ToolError(f"cannot append to {path}: {e.strerror}") from e
    return f"appended {len(text)} characters to {path}"


@app.tool()
def move(src: str, dst: str) -> str:
    """Move or rename a file inside the workspace. dst is the new file path; missing parent folders are created.
    Refuses to overwrite: dst must not exist yet."""
    s, d = _resolve(src), _resolve(dst)
    if s.is_dir():
        raise ToolError(f"{src} is a folder; fsx.move moves files only")
    if not s.is_file():
        raise ToolError(f"no such file: {src}")
    if d == s:
        raise ToolError(f"{src} and {dst} are the same file")
    if d.exists():
        what = "a folder" if d.is_dir() else "a file"
        raise ToolError(f"{dst} already exists ({what}); nothing moved. fsx.move never overwrites: give a new "
                        f"file path (e.g. folder/name.txt)")
    try:
        d.parent.mkdir(parents=True, exist_ok=True)
        s.rename(d)
    except OSError as e:
        raise ToolError(f"cannot move {src} to {dst}: {e.strerror}") from e
    return f"moved {src} to {_rel(d)}"


@app.tool()
def mkdir(path: str) -> str:
    """Create a folder in the workspace, with any missing parent folders. An existing folder is left as it is."""
    p = _resolve(path)
    if p.is_dir():
        return f"folder {_rel(p)} already exists"
    if p.exists():
        raise ToolError(f"{path} is a file, not a folder")
    try:
        p.mkdir(parents=True)
    except FileExistsError as e:                       # a parent on the way is a file
        raise ToolError(f"cannot create {path}: a parent on the way is a file") from e
    except OSError as e:
        raise ToolError(f"cannot create {path}: {e.strerror}") from e
    return f"created folder {_rel(p)}"


@app.tool()
def tree(path: str = ".", depth: int = 2) -> str:
    """List a folder and its subfolders as an indented tree, `depth` levels deep (1 = the folder's own entries,
    at most 6). Folders end with '/'. At most 200 entries; narrow the path or depth for more."""
    if not 1 <= depth <= MAX_TREE_DEPTH:
        raise ToolError(f"depth must be between 1 and {MAX_TREE_DEPTH}, got {depth}")
    start = _resolve(path)
    if not start.is_dir():
        raise ToolError(f"not a folder: {path}" if start.exists() else f"no such folder: {path}")
    root = _root()
    lines = [f"{_rel(start)}/"]
    hidden = 0

    def walk(folder: Path, level: int) -> None:
        nonlocal hidden
        try:
            children = sorted(folder.iterdir(), key=lambda c: c.name)
        except OSError:
            return
        for c in children:
            is_dir = c.is_dir()
            if len(lines) <= MAX_TREE_ENTRIES:
                lines.append(f"{'  ' * level}{c.name}{'/' if is_dir else ''}")
            else:
                hidden += 1
            # Never through a symlink: no loops, and nothing outside the workspace.
            if is_dir and level < depth and not c.is_symlink() and c.resolve().is_relative_to(root):
                walk(c, level + 1)

    walk(start, 1)
    if len(lines) == 1:
        lines.append("  (empty)")
    if hidden:
        lines.append(f"... {hidden} more entries; narrow the path or depth")
    return "\n".join(lines)


if __name__ == "__main__":
    app.run()
