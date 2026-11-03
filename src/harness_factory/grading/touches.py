"""Did a tool call touch the task's data? The `touches` field of a `tool_called` check (GRD-01).

A grounding check pays for looking at the data the answer comes from, not for calling a tool: `SELECT 1` or
reading an unrelated file earns nothing. With `touches` set, a successful call to a tool matching the check's
pattern counts only when it touched one of the entries, read from the arguments its tool_call span recorded:

  table       a call with a `sql` argument (sql.query, sql.execute) names the table in its statement: an
              identifier token compared case-insensitively, bare, quoted ("orders", `orders`, [orders]) or
              qualified (main.orders). String literals and comments are skipped, so SELECT 'orders' names none.
  path/glob   a call with a path argument (path, src, dst) names that workspace path, after normalising
              (./, .., backslashes, the workspace's own absolute path); the entry is a glob (fnmatch, case
              sensitive; * also crosses /), so `service/config/*.ini` covers every environment file.
  name=value  any other call: its argument `name` matches value (a glob). A call that doesn't set `name` isn't
              narrowed by it and counts: a listing without a customer filter lists every customer's invoices.

Limits, by design: this reads the call, not what came back. A statement that names a table proves it was asked
about, not that its rows were read (`... WHERE 0`, a column or alias spelled like a table) or that the answer
used them; a path matches the argument itself, so for a tool that walks a folder (search.grep) name the folder
or a glob; only the first ARGS_CHARS of the arguments are on the span, so a table named after them is missed.
A call answered by an injected `empty` fault never ran and touches nothing.
"""

from __future__ import annotations

import json
import posixpath
import re
from fnmatch import fnmatch, fnmatchcase
from pathlib import Path

from harness_factory.observability.span import Span, SpanKind

PATH_ARGS = ("path", "src", "dst")
_ARG_ENTRY = re.compile(r"^([A-Za-z_]\w*)=(.*)$", re.DOTALL)
_SQL_TOKEN = re.compile(r"""
      '(?:[^']|'')*'?               # string literal: skipped
    | --[^\n]*                      # line comment: skipped
    | /\*.*?(?:\*/|\Z)              # block comment: skipped
    | \d[\w.]*                      # number (1e5, 0x1F): skipped
    | "((?:[^"]|"")*)"?             # "quoted identifier"
    | `((?:[^`]|``)*)`?             # `quoted identifier`
    | \[([^\]]*)\]?                 # [quoted identifier]
    | ([A-Za-z_][A-Za-z0-9_$]*)     # bare identifier or keyword
""", re.VERBOSE | re.DOTALL)


def sql_identifiers(sql: str) -> set[str]:
    """Every identifier token in a statement, case-folded and unquoted (keywords included: a table can't be
    called SELECT without quotes, and a quoted one is still a table name)."""
    out = set()
    for m in _SQL_TOKEN.finditer(sql):
        dq, bq, br, bare = m.groups()
        name = (dq.replace('""', '"') if dq is not None else bq.replace("``", "`") if bq is not None
                else br if br is not None else bare)
        if name is not None:
            out.add(name.casefold())
    return out


def span_args(span: Span) -> dict | None:
    """The call's arguments as its span recorded them (JSON, cut at ARGS_CHARS: a cut string is closed again);
    None when the span has none, so an unrecorded call can't pass an `arg=value` entry by leaving `arg` unset."""
    raw = span.attributes.get("args")
    if not isinstance(raw, str):
        return None
    for text in [raw, *(raw[:len(raw) - cut] + '"}' for cut in range(7))]:
        try:
            args = json.loads(text)
        except ValueError:
            continue
        if isinstance(args, dict):
            return args
    return None


def workspace_path(path: str, workspace: Path | None) -> str:
    """A path argument as a workspace-relative POSIX path, the way the file servers resolve it."""
    p = path.replace("\\", "/")
    if workspace is not None and posixpath.isabs(p):
        for root in {str(workspace).replace("\\", "/"), str(workspace.resolve()).replace("\\", "/")}:
            rel = posixpath.relpath(p, root)
            if rel != ".." and not rel.startswith("../"):
                p = rel
                break
    p = posixpath.normpath(p)
    return "." if p in ("", ".") else p.removeprefix("./")


def touched(args: dict, entries: list[str], workspace: Path | None = None) -> str | None:
    """The first entry the call touched, or None."""
    sql = args.get("sql")
    tables = sql_identifiers(sql) if isinstance(sql, str) else set()
    paths = [workspace_path(v, workspace) for k in PATH_ARGS if isinstance(v := args.get(k), str)]
    for entry in entries:
        if (m := _ARG_ENTRY.match(entry)) is not None:
            name, want = m.groups()
            if name not in args or fnmatchcase(str(args[name]), want):
                return entry
        elif isinstance(sql, str):
            if entry.strip('"`[]').casefold() in tables:
                return entry
        elif any(fnmatchcase(p, workspace_path(entry, None)) for p in paths):
            return entry
    return None


def grounded_on_data(pattern: str, entries: list[str], spans: list[Span],
                     workspace: Path | None = None) -> tuple[bool, str]:
    """A tool_called check with `touches`: some successful call matching pattern touched one of the entries."""
    calls = [s for s in spans if s.kind == SpanKind.tool_call and s.status != "error" and fnmatch(s.name, pattern)
             and s.attributes.get("fault") != "empty"]
    for s in calls:
        args = span_args(s)
        hit = None if args is None else touched(args, entries, workspace)
        if hit is not None:
            return True, f"grounded: {s.name} touched {hit}"
    if not calls:
        return False, f"no successful call matching {pattern}"
    return False, f"{len(calls)} call(s) matching {pattern}, none touched {', '.join(entries)}"


__all__ = ["PATH_ARGS", "grounded_on_data", "span_args", "sql_identifiers", "touched", "workspace_path"]
