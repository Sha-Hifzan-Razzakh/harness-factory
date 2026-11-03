"""code MCP server: run the workspace's tests or one of its Python scripts (ADR 0012).

Code runs as a child process with the workspace as its working directory, a hard time limit (the process group is
killed when it runs out), an output cap and a scrubbed environment (proc.py). In docker mode it runs inside the
run's container, so it has no network and can't write outside /work, /state and /tmp. In local mode nothing
contains it beyond the path checks here, so runs with a real model need HF_SANDBOX=docker or an explicit
HF_ALLOW_LOCAL_CODE=1 (the provisioner refuses otherwise).

Both tools are REVERSIBLE: code can change any file in the sandbox, and resetting the sandbox undoes it.
Standard library and mcp only: pytest is started as `python -m pytest`, never imported.
"""

from __future__ import annotations

import os
import re
import shlex
import sys
import tempfile
from pathlib import Path

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError  # message reaches the model; ValueError would be masked

from harness_factory.env.mcp.impl import proc

app = MCPServer("code", instructions="Run the workspace's tests (pytest) or one of its Python scripts. Paths are "
                                     "relative to the workspace root. Each call has a time limit and its output is "
                                     "cut to the last few thousand characters.")

MAX_OUTPUT_CHARS = 6_000
# pytest options a model may pass: selection and reporting only. Anything that names a file or directory
# (--rootdir, --basetemp, -c, --junitxml, -p, ...) could reach outside the workspace, so it is refused.
_FLAGS = {"-x", "-q", "-v", "-vv", "-l", "--exitfirst", "--co", "--collect-only", "--no-header",
          "--showlocals", "-s"}
_VALUED = {"-k", "-m"}
_FLAG_RX = re.compile(r"^(--tb=(auto|long|short|line|native|no)|--maxfail=\d{1,3}|-r[fEsxXpPaAN]{1,10}|"
                      r"--durations=\d{1,2})$")


def _root() -> Path:
    return Path(os.environ["HF_WORKSPACE"]).resolve()


def _timeout() -> float:
    return float(os.environ.get("HF_CODE_TIMEOUT_S") or proc.DEFAULT_TIMEOUT_S)


def _confined(path: str, what: str) -> Path:
    root = _root()
    if not path or Path(path).is_absolute():
        raise ToolError(f"{what} must be a path relative to the workspace: {path!r}")
    p = (root / path).resolve()
    if not p.is_relative_to(root):
        raise ToolError(f"path escapes the workspace: {path}")
    if not p.exists():
        raise ToolError(f"no such file or folder: {path}")
    return p


def _split(args: str) -> list[str]:
    try:
        return shlex.split(args or "")
    except ValueError as e:
        raise ToolError(f"cannot parse args {args!r}: {e}") from e


def _pytest_options(args: str) -> list[str]:
    words, out = _split(args), []
    it = iter(words)
    for w in it:
        if w == "-q":
            continue                          # already quiet; -qq would drop the summary line the counts come from
        if w in _FLAGS or _FLAG_RX.match(w):
            out.append(w)
        elif w in _VALUED:
            value = next(it, None)
            if value is None:
                raise ToolError(f"{w} needs a value, e.g. {w} \"name and not slow\"")
            out += [w, value]
        else:
            raise ToolError(f"pytest option not allowed: {w!r}. Allowed: -k EXPR, -m EXPR, -x, -q, -v, -vv, -l, "
                            f"-s, --tb=STYLE, --maxfail=N, -rCHARS, --co; give the test path in `path`")
    return out


def _run(argv: list[str]) -> proc.Ran:
    with tempfile.TemporaryDirectory(prefix="hf-code-") as home:
        try:
            return proc.run(argv, _root(), _timeout(), proc.code_env(home, _root()))
        except proc.ProcError as e:
            raise ToolError(str(e)) from e


def _result(ran: proc.Ran) -> dict:
    out = {"exit_code": ran.exit_code, "timed_out": ran.timed_out, "output": ran.text(MAX_OUTPUT_CHARS)}
    if ran.timed_out:
        out["output"] += f"\n[killed: the {_timeout():g} s time limit ran out]"
    return out


@app.tool()
def run_tests(path: str = ".", args: str = "") -> dict:
    """Run pytest on a workspace path (a folder, a file, or file::test_name) and return the exit code (0 = all
    passed, 1 = some failed, 5 = no tests found), pass/fail counts and the end of the output. args: pytest
    options such as "-k discount -x" or "--tb=short"."""
    file_part, sep, node = path.partition("::")
    target = _confined(file_part or ".", "path").relative_to(_root()).as_posix()
    ran = _run(proc.pytest_argv(_root(), [target + (sep + node if sep else "")], _pytest_options(args)))
    return {**_result(ran), "counts": proc.pytest_counts(ran.output)}


@app.tool()
def run_python(path: str, args: str = "") -> dict:
    """Run a Python script from the workspace (python <path> <args>) with the workspace as the working directory
    and on the import path. Returns the exit code and the end of its output (stdout and stderr together)."""
    script = _confined(path, "path")
    if not script.is_file() or script.suffix != ".py":
        raise ToolError(f"not a Python file: {path}")
    return _result(_run([sys.executable, str(script), *_split(args)]))


if __name__ == "__main__":
    app.run()
