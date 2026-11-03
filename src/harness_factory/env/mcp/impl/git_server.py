"""git MCP server: version control over the run's workspace (ADR 0012).

The repository's git directory is HF_GIT_DIR, in the sandbox's state folder next to the database, never inside the
workspace: the agent's file tools can't edit its config or hooks, and the workspace's files are only the task's.
A task asks for one with `env_seed.git: true`; the sandbox then commits the seed files once, at a fixed date as a
fixed identity, so the seed commit id is the same on every machine.

Every call runs `git` as a child process (proc.git): command-line config that turns off hooks, fsmonitor, pagers,
signing and every remote transport, no user or system config, a time limit and an output cap. There is no tool for
remotes, push, fetch, reset or rebase. Commits are dated one minute after their parent, so the same work makes the
same commit ids. Standard library and mcp only.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError  # message reaches the model; ValueError would be masked

from harness_factory.env.mcp.impl import proc

app = MCPServer("git", instructions="Git for the task workspace: status, diff, log, commit, branch, checkout. "
                                    "Paths are relative to the workspace root. There are no remotes.")

MAX_OUTPUT_CHARS = 12_000
MAX_LOG = 50
MAX_MESSAGE = 2_000
_BRANCH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,99}$")


def _root() -> Path:
    return Path(os.environ["HF_WORKSPACE"]).resolve()


def _git_dir() -> Path:
    d = Path(os.environ.get("HF_GIT_DIR") or "")
    if not os.environ.get("HF_GIT_DIR") or not (d / "HEAD").is_file():
        raise ToolError("the workspace is not a git repository in this task")
    return d


def _git(*args: str, epoch: int | None = None, read: bool = False) -> str:
    try:
        # Reads take no optional locks: they never rewrite the index behind a concurrent write.
        return proc.git_ok([*(["--no-optional-locks"] if read else []), *args], git_dir=_git_dir(),
                           work_tree=_root(), epoch=epoch)
    except proc.ProcError as e:
        raise ToolError(str(e)) from e


def _cap(text: str) -> str:
    if len(text) > MAX_OUTPUT_CHARS:
        return text[:MAX_OUTPUT_CHARS] + f"\n[... {len(text) - MAX_OUTPUT_CHARS} more characters; narrow the path]"
    return text


def _branch_name(name: str) -> str:
    if not _BRANCH.match(name or "") or ".." in name or name.endswith((".lock", "/", ".")) or "//" in name:
        raise ToolError(f"invalid branch name {name!r}: use letters, digits, '.', '_', '-' and '/', e.g. fix/vat")
    try:
        proc.git_ok(["check-ref-format", "--branch", name], git_dir=_git_dir(), work_tree=_root())
    except proc.ProcError as e:
        raise ToolError(f"invalid branch name {name!r}") from e
    return name


def _pathspec(path: str) -> list[str]:
    if not path:
        return []
    root = _root()
    if Path(path).is_absolute() or not (root / path).resolve().is_relative_to(root):
        raise ToolError(f"path escapes the workspace: {path}")
    return [(root / path).resolve().relative_to(root).as_posix() or "."]


def _current_branch() -> str:
    return _git("rev-parse", "--abbrev-ref", "HEAD").strip()


@app.tool()
def status() -> str:
    """Show the current branch and every changed, staged or untracked file (git status --porcelain)."""
    return _cap(_git("status", "--porcelain=v1", "--branch", "--untracked-files=all", read=True))


@app.tool()
def diff(path: str = "", staged: bool = False) -> str:
    """Show uncommitted changes as a unified diff, for the whole workspace or one path. staged: show what is staged
    for the next commit instead of what isn't."""
    out = _git("diff", "--no-color", "--no-ext-diff", "--no-textconv", *(["--cached"] if staged else []), "--",
               *_pathspec(path), read=True)
    return _cap(out) or "no changes"


@app.tool()
def log(n: int = 10) -> str:
    """Show the last n commits on the current branch (at most 50), newest first: short id, date, subject."""
    if n < 1:
        raise ToolError("n must be at least 1")
    return _cap(_git("log", f"--max-count={min(n, MAX_LOG)}", "--no-show-signature", "--date=short",
                     "--format=%h %ad %s", read=True))


@app.tool()
def commit(message: str) -> str:
    """Stage every change in the workspace (new, changed and deleted files) and commit it on the current branch."""
    message = (message or "").strip()
    if not message:
        raise ToolError("a commit needs a message")
    if len(message) > MAX_MESSAGE:
        raise ToolError(f"commit message longer than {MAX_MESSAGE} characters")
    _git("add", "--all")
    staged = _git("diff", "--cached", "--name-only", "--no-ext-diff")
    if not staged.strip():
        raise ToolError("nothing to commit: the workspace matches the last commit")
    parent = int(_git("log", "-1", "--format=%ct").strip())
    _git("commit", "--quiet", "--no-verify", "-m", message, epoch=parent + proc.COMMIT_STEP_S)
    sha = _git("rev-parse", "--short", "HEAD").strip()
    files = len(staged.split())
    return f"committed {sha} on {_current_branch()}: {message.splitlines()[0]} ({files} file{'s' * (files != 1)})"


@app.tool()
def branch(name: str) -> str:
    """Create a branch at the current commit. It does not switch to it; use checkout for that."""
    name = _branch_name(name)
    _git("branch", "--no-track", name)
    return f"created branch {name} at {_git('rev-parse', '--short', 'HEAD').strip()}"


@app.tool()
def checkout(name: str) -> str:
    """Switch to an existing branch. Uncommitted changes are kept, unless they conflict with the branch (then
    nothing changes and the error says which files)."""
    name = _branch_name(name)
    try:
        proc.git_ok(["rev-parse", "--verify", "--quiet", f"refs/heads/{name}"], git_dir=_git_dir(), work_tree=_root())
    except proc.ProcError as e:
        raise ToolError(f"no such branch: {name} (create it with branch)") from e
    _git("checkout", "--quiet", name, "--")
    return f"switched to {name}"


if __name__ == "__main__":
    app.run()
