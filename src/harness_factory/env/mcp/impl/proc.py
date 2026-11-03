"""Child processes for the code and git servers, the git seed, and the graders that check their work (ADR 0012).

Standard library only, so it runs inside the sandbox image. Nothing here imports pytest or a git library: it starts
`python -m pytest` and `git` as child processes, each with

- a hard wall-time limit (the whole process group is killed when it runs out),
- a bounded output buffer (the head and the tail are kept, the middle is dropped),
- a scrubbed environment: no API keys, no HF_* paths, no user or system git config, no pytest plugins or options
  from the environment, no bytecode written into the workspace.

Grading probe: `python -P -m harness_factory.env.mcp.impl.proc` reads one JSON request on stdin and prints one
JSON answer. The graders start it through the sandbox (`Sandbox.probe`), so the code under test runs where the
agent's code ran: inside the container in docker mode. `-P` keeps the workspace off `sys.path`, so a file the agent
wrote can't stand in for this module or for pytest.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
from dataclasses import asdict, dataclass
from pathlib import Path

DEFAULT_TIMEOUT_S = 60.0       # per run_tests / run_python call; HF_CODE_TIMEOUT_S overrides
GIT_TIMEOUT_S = 20.0
GRADE_TIMEOUT_S = 120.0        # per tests_pass check
HEAD_BYTES = 4_000             # kept from the start of a process's output
TAIL_BYTES = 12_000            # kept from the end (where pytest prints its summary)

# Every commit is made by this identity at a fixed clock, so the same work gives the same commit ids everywhere.
GIT_NAME = "Harness Factory"
GIT_EMAIL = "agent@harness-factory.invalid"
GIT_SEED = {"branch": "main", "message": "Initial commit", "epoch": 1767225600,    # 2026-01-01T00:00:00Z
            "object_format": "sha1", "name": GIT_NAME, "email": GIT_EMAIL}
COMMIT_STEP_S = 60             # each later commit is dated one minute after its parent

# Read from the server's environment and passed on to code; everything else is dropped.
_KEEP_ENV = ("PATH", "PYTHONPATH", "LANG", "SYSTEMROOT", "COMSPEC", "PATHEXT", "WINDIR", "TEMP", "TMP", "TMPDIR")


class ProcError(Exception):
    """The process could not be started (missing binary). Servers turn it into a ToolError."""


@dataclass
class Ran:
    exit_code: int | None          # None: killed at the time limit
    timed_out: bool
    output: str                    # stdout and stderr together, head and tail kept
    dropped: int = 0               # bytes of output cut from the middle

    def text(self, max_chars: int = 8_000) -> str:
        """The output's last max_chars characters (pytest's summary is at the end), marked when cut."""
        out = self.output
        if len(out) > max_chars:
            out = f"[... {len(out) - max_chars} characters cut ...]\n" + out[-max_chars:]
        return out


class _Capped:
    """Drains a pipe into a bounded head and a rolling tail, so a process that prints forever can't fill memory."""

    def __init__(self) -> None:
        self.head = bytearray()
        self.tail = bytearray()
        self.total = 0

    def drain(self, pipe) -> None:
        for chunk in iter(lambda: pipe.read(8192), b""):
            self.total += len(chunk)
            room = HEAD_BYTES - len(self.head)
            if room > 0:
                self.head += chunk[:room]
                chunk = chunk[room:]
            self.tail += chunk
            if len(self.tail) > TAIL_BYTES:
                del self.tail[:len(self.tail) - TAIL_BYTES]
        pipe.close()

    def text(self) -> tuple[str, int]:
        dropped = self.total - len(self.head) - len(self.tail)
        body = self.head.decode("utf-8", "replace")
        if dropped:
            body += f"\n[... {dropped} bytes of output dropped ...]\n"
        return body + self.tail.decode("utf-8", "replace"), dropped


def _kill_group(p: subprocess.Popen) -> None:
    if os.name == "posix":
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    elif p.poll() is None:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True, check=False)
        p.kill()


def run(argv: list[str], cwd: Path | str, timeout_s: float, env: dict[str, str]) -> Ran:
    """Run argv with a hard time limit. Children it leaves behind are killed with it (POSIX process group)."""
    group = {"start_new_session": True} if os.name == "posix" else \
        {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}   # type: ignore[attr-defined]
    try:
        p = subprocess.Popen(argv, cwd=str(cwd), env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, **group)
    except OSError as e:
        raise ProcError(f"could not start {Path(argv[0]).name}: {e}") from e
    buf = _Capped()
    reader = threading.Thread(target=buf.drain, args=(p.stdout,), daemon=True)
    reader.start()
    timed_out = False
    try:
        p.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        timed_out = True
    _kill_group(p)                       # also what it started in the background
    p.wait()
    reader.join(timeout=5)
    out, dropped = buf.text()
    return Ran(exit_code=None if timed_out else p.returncode, timed_out=timed_out, output=out, dropped=dropped)


def code_env(home: str, workspace: Path | None = None) -> dict[str, str]:
    """What code from the workspace runs with: the server's PATH and PYTHONPATH (the sandbox image needs it to find
    pytest), a throwaway HOME, and nothing else from the caller. workspace: prepended to PYTHONPATH, so a script
    in a subfolder can import the workspace's packages."""
    env = {k: os.environ[k] for k in _KEEP_ENV if k in os.environ}
    if workspace is not None:
        env["PYTHONPATH"] = os.pathsep.join(p for p in (str(workspace), env.get("PYTHONPATH")) if p)
    env.update(HOME=home, USERPROFILE=home, PYTHONDONTWRITEBYTECODE="1", PYTHONHASHSEED="0", PYTHONUTF8="1",
               PYTHONIOENCODING="utf-8", NO_COLOR="1", PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", LC_ALL="C.UTF-8")
    return env


def pytest_argv(workspace: Path, targets: list[str], extra: list[str], *, ini: Path | None = None) -> list[str]:
    """`python -P -m pytest` over targets. The workspace joins sys.path only after pytest is imported
    (`-o pythonpath`), so a `pytest.py` the agent wrote can't replace pytest. No cache, no colour, no plugins.
    ini: an empty config file, so the workspace's pytest.ini / pyproject options don't apply (grading)."""
    argv = [sys.executable, "-P", "-m", "pytest", "-q", "-p", "no:cacheprovider", "--color=no",
            f"--rootdir={workspace}", "-o", f"pythonpath={workspace}"]
    if ini is not None:
        argv += ["-c", str(ini), "--noconftest"]
    return [*argv, *extra, *targets]


_SUMMARY = re.compile(r"(\d+) (passed|failed|errors?|skipped|xfailed|xpassed|deselected)")


def pytest_counts(output: str) -> dict[str, int]:
    """Counts from pytest's last summary line, e.g. '1 failed, 3 passed in 0.02s'."""
    for line in reversed(output.splitlines()):
        if re.search(r"\bin \d+(\.\d+)?s\b", line):
            counts = {"passed": 0, "failed": 0, "errors": 0, "skipped": 0}
            for n, what in _SUMMARY.findall(line):
                counts["errors" if what.startswith("error") else what] = int(n)
            return counts
    return {}


# ------------------------------------------------------------------------------------------------------------- git

def git_bin() -> str:
    exe = shutil.which("git")
    if not exe:
        raise ProcError("git is not installed or not on PATH (on Windows, install Git for Windows and reopen the "
                        "shell)")
    return exe


def _git_config(home: Path) -> list[str]:
    """Command-line config: the highest-precedence scope, so a repository config written by code in the sandbox
    can't turn on hooks, an fsmonitor command, signing, a pager or a remote transport."""
    hooks = home / "hooks"
    hooks.mkdir(exist_ok=True)                          # empty: no hook ever runs
    pairs = {"core.hooksPath": str(hooks), "core.fsmonitor": "false", "core.untrackedCache": "false",
             "core.pager": "cat", "core.editor": "true", "core.autocrlf": "false", "core.safecrlf": "false",
             "core.quotePath": "false", "commit.gpgSign": "false", "tag.gpgSign": "false",
             "log.showSignature": "false", "protocol.allow": "never", "credential.helper": "",
             "gc.auto": "0", "maintenance.auto": "false", "color.ui": "false", "advice.detachedHead": "false",
             "init.defaultBranch": GIT_SEED["branch"], "user.name": GIT_NAME, "user.email": GIT_EMAIL,
             "user.useConfigOnly": "true"}
    return [x for k, v in pairs.items() for x in ("-c", f"{k}={v}")]


def git(args: list[str], *, git_dir: Path, work_tree: Path, epoch: int | None = None,
        timeout_s: float = GIT_TIMEOUT_S) -> Ran:
    """One git command over the run's repository. The repository lives outside the workspace (git_dir), so the agent's
    file tools can't reach its config or hooks; the work tree is the workspace. epoch: the commit date."""
    with tempfile.TemporaryDirectory(prefix="hf-git-") as tmp:
        home = Path(tmp)
        (home / ".gitconfig").write_text("[safe]\n\tdirectory = *\n")     # same user, but be explicit
        env = {k: os.environ[k] for k in ("PATH", "SYSTEMROOT", "COMSPEC", "PATHEXT", "WINDIR", "TEMP", "TMP")
               if k in os.environ}
        env.update(HOME=tmp, USERPROFILE=tmp, XDG_CONFIG_HOME=tmp, GIT_CONFIG_GLOBAL=str(home / ".gitconfig"),
                   GIT_CONFIG_NOSYSTEM="1", GIT_TERMINAL_PROMPT="0", GIT_EDITOR="true", LC_ALL="C", LANG="C",
                   GIT_AUTHOR_NAME=GIT_NAME, GIT_AUTHOR_EMAIL=GIT_EMAIL, GIT_COMMITTER_NAME=GIT_NAME,
                   GIT_COMMITTER_EMAIL=GIT_EMAIL)
        if epoch is not None:
            env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = f"@{epoch} +0000"
        argv = [git_bin(), "--no-pager", f"--git-dir={git_dir}", f"--work-tree={work_tree}", *_git_config(home),
                *args]
        return run(argv, work_tree, timeout_s, env)


def git_ok(args: list[str], **kw) -> str:
    """git(), raising ProcError with git's own message when it fails."""
    ran = git(args, **kw)
    if ran.exit_code != 0:
        why = "timed out" if ran.timed_out else ran.output.strip() or f"exit code {ran.exit_code}"
        raise ProcError(f"git {args[0]}: {why}")
    return ran.output


def init_seed_repo(git_dir: Path, work_tree: Path) -> str:
    """A repository over the seeded workspace with one commit, made the same way everywhere (identity, date,
    branch and object format fixed), so its commit id is a function of the seed files alone. Returns that id."""
    kw = {"git_dir": git_dir, "work_tree": work_tree}
    git_ok(["init", "--quiet", f"--object-format={GIT_SEED['object_format']}",
            f"--initial-branch={GIT_SEED['branch']}"], **kw)
    git_ok(["config", "--unset", "core.worktree"], **kw)    # always passed explicitly: the path differs in docker
    git_ok(["config", "core.filemode", "false"], **kw)      # same tree on Windows and Linux
    git_ok(["add", "--all"], **kw)
    git_ok(["commit", "--quiet", "--no-verify", "--allow-empty", "-m", GIT_SEED["message"]], epoch=GIT_SEED["epoch"],
           **kw)
    return git_ok(["rev-parse", "HEAD"], **kw).strip()


def commits_since(ref: str, since: str | None, *, git_dir: Path, work_tree: Path) -> list[dict] | None:
    """Commits reachable from ref and not from since (the seed commit), newest first, each with its full message
    and the paths it touched. None when ref doesn't name a commit."""
    kw = {"git_dir": git_dir, "work_tree": work_tree}
    found = git(["rev-parse", "--verify", "--quiet", "--end-of-options", f"{ref}^{{commit}}"], **kw)
    if found.exit_code != 0:
        return None
    revs = [found.output.strip()] + (["--not", since] if since else [])
    log = git_ok(["log", "--no-show-signature", "--format=%H%x00%B%x1e", *revs, "--"], **kw)
    out = []
    for entry in log.split("\x1e"):
        sha, sep, message = entry.strip("\n").partition("\x00")
        if not sep:
            continue
        paths = git_ok(["diff-tree", "--no-commit-id", "--name-only", "-r", "--root", "--no-ext-diff", "--no-textconv",
                        sha], **kw).split()
        out.append({"sha": sha, "message": message.strip(), "paths": paths})
    return out


# --------------------------------------------------------------------------------------------- the grading probe

def _sandbox_paths() -> tuple[Path, Path]:
    return Path(os.environ["HF_WORKSPACE"]).resolve(), Path(os.environ["HF_GIT_DIR"])


def probe(req: dict) -> dict:
    """One grading question about the sandbox after the run. Ops:

    pytest       {targets, k, hidden, timeout_s}: run the tests; hidden is a test module's source, written outside
                 the workspace for this run only. Workspace config files and conftest.py files don't apply.
    git_commits  {ref, since}: commits on ref after the seed commit (commits_since).
    """
    workspace, git_dir = _sandbox_paths()
    if req["op"] == "pytest":
        with tempfile.TemporaryDirectory(prefix="hf-grade-") as tmp:
            ini = Path(tmp) / "pytest.ini"
            ini.write_text("[pytest]\n")
            targets = list(req.get("targets") or [])
            if req.get("hidden"):
                hidden = Path(tmp) / "test_hidden_checks.py"
                hidden.write_text(req["hidden"])
                targets.append(str(hidden))
            extra = ["-k", req["k"]] if req.get("k") else []
            home = Path(tmp) / "home"
            home.mkdir()
            ran = run(pytest_argv(workspace, targets or ["."], extra, ini=ini), workspace,
                      float(req.get("timeout_s") or GRADE_TIMEOUT_S), code_env(str(home)))
        return {**asdict(ran), "output": ran.text(2_000), "counts": pytest_counts(ran.output)}
    if req["op"] == "git_commits":
        return {"commits": commits_since(req.get("ref") or "HEAD", req.get("since"), git_dir=git_dir,
                                         work_tree=workspace)}
    raise ValueError(f"unknown probe op {req['op']!r}")


def main() -> None:
    try:
        answer = probe(json.loads(sys.stdin.read()))
    except (ProcError, KeyError, ValueError, OSError) as e:
        answer = {"error": f"{type(e).__name__}: {e}"}
    sys.stdout.write(json.dumps(answer) + "\n")


if __name__ == "__main__":
    main()
