"""Progress signals per cell (#114): what runs did on the way, next to E and never inside it.

When every run of a comparison scores E = 0, the paired deltas all tie and say nothing, although the traces can
still tell recipes apart (one reads far less, one calls the engine early, one at least writes the output file).
These signals read what is already stored (the run's row, its JSONL trace, its task's oracle); nothing is graded
again and no score, status or hash changes.

Per run:
  wrote output   a successful write to a file the task's oracle grades (a weighted check with a `path`)
  read / write   a successful call with a READ / a REVERSIBLE or IRREVERSIBLE side effect (as the call was made)
  engine         a successful call to a domain server: anything but the generic data servers (fs, fsx, search,
                 sql) and the harness's own tools (spill, delegate, retrieve, skills)
  first write    the position of the first successful write among the run's tool calls (1 = the first call)
  tool calls     how many tool calls the run made
  schema valid   the task's `json_schema` checks all passed (only for tasks that have one)
"""

from __future__ import annotations

import json
import re
import statistics
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

GENERIC_SERVERS = frozenset({"fs", "fsx", "search", "sql"})
HARNESS_SERVERS = frozenset({"spill", "delegate", "retrieve", "skills"})
WRITES = frozenset({"REVERSIBLE", "IRREVERSIBLE"})
_PATH = re.compile(r'"path"\s*:\s*"((?:[^"\\]|\\.)*)"')


@dataclass(frozen=True)
class RunSignals:
    wrote_output: bool | None          # None: the task's oracle grades no file
    read: bool
    write: bool
    engine: bool
    first_write: int | None            # None: no successful write
    tool_calls: int
    schema_valid: bool | None          # None: the task has no json_schema check


def output_paths(task) -> set[str]:
    """Files the task's oracle grades: weighted checks with a path (guards and `file_absent` aren't outputs)."""
    return {_norm(c.path) for c in task.oracle.checks
            if c.path and c.weight > 0 and c.kind not in ("file_absent", "remote_equals")}


def _norm(path: str) -> str:
    p = PurePosixPath(path.strip())
    parts = [x for x in p.parts if x not in ("/", ".")]
    if parts[:1] == ["workspace"]:
        parts = parts[1:]
    return "/".join(parts)


def _path_of(args: str | None) -> str | None:
    """The `path` argument of a call, from the trace's (possibly cut) JSON copy of its arguments."""
    if not args:
        return None
    try:
        value = json.loads(args)
        path = value.get("path") if isinstance(value, dict) else None
        return path if isinstance(path, str) else None
    except ValueError:
        m = _PATH.search(args)
        return json.loads(f'"{m.group(1)}"') if m else None


def _spans(trace_path: str | None) -> list[dict] | None:
    if not trace_path or not Path(trace_path).is_file():
        return None
    try:
        return [json.loads(line) for line in Path(trace_path).read_text().splitlines() if line.strip()]
    except (OSError, ValueError):
        return None


def run_signals(row: dict, task) -> RunSignals | None:
    """A run's signals, or None when its trace can't be read (an older or moved store)."""
    spans = _spans(row.get("trace_path"))
    if spans is None:
        return None
    calls = sorted((s for s in spans if s.get("kind") == "tool_call"), key=lambda s: s.get("start_ns", 0))
    outputs = output_paths(task)
    read = write = engine = wrote = False
    first_write = None
    for i, s in enumerate(calls, 1):
        if s.get("status") != "ok":
            continue
        server = str(s.get("name", "")).split(".", 1)[0]
        effect = (s.get("attributes") or {}).get("side_effect")
        if server in HARNESS_SERVERS:
            continue
        if server not in GENERIC_SERVERS:
            engine = True
        if effect == "READ":
            read = True
        elif effect in WRITES:
            write = True
            first_write = first_write or i
            path = _path_of((s.get("attributes") or {}).get("args"))
            if path is not None and _norm(path) in outputs:
                wrote = True
    schema_ids = {c.id for c in task.oracle.checks if c.kind == "json_schema"}
    results = {c["id"]: c.get("passed") for g in (row.get("grades") or []) if g.get("grader_id") == "state_diff"
               for c in g.get("checks", [])}
    return RunSignals(wrote_output=wrote if outputs else None, read=read, write=write, engine=engine,
                      first_write=first_write, tool_calls=len(calls),
                      schema_valid=all(results.get(c) is True for c in schema_ids) if schema_ids else None)


def _share(values: list[bool | None]) -> str:
    known = [v for v in values if v is not None]
    return f"{sum(known)}/{len(known)}" if known else "-"


def _median(values: list[int | None]) -> str:
    known = [v for v in values if v is not None]
    return f"{statistics.median(known):g}" if known else "-"


def cell_rows(f, rows: list[dict]) -> list[list[str]]:
    """One table row per (recipe, model) cell of scored runs: the share of runs showing each signal (runs with the
    signal / runs where it applies), the median first-write position and the median number of tool calls."""
    cells: dict[tuple[str, str], list[RunSignals]] = {}
    unread: dict[tuple[str, str], int] = {}
    for r in rows:
        key = (r["recipe_id"], r["canonical_model"])
        task = _task(f, r)
        sig = run_signals(r, task) if task is not None else None
        if sig is None:
            unread[key] = unread.get(key, 0) + 1
            continue
        cells.setdefault(key, []).append(sig)
    out = []
    for key in sorted(set(cells) | set(unread)):
        ss = cells.get(key, [])
        out.append([f"`{key[0]}`", f"`{key[1]}`", str(len(ss)), _share([s.wrote_output for s in ss]),
                    _share([s.engine for s in ss]), _share([s.read for s in ss]), _share([s.write for s in ss]),
                    _median([s.first_write for s in ss]), _median([s.tool_calls for s in ss]),
                    _share([s.schema_valid for s in ss])] + ([f"{unread[key]} not read (no trace, or another task version)"] if key in unread
                                                             else ["-"]))
    return out


HEADER = ["recipe", "model", "runs", "wrote output", "engine", "read", "write", "first write (median)",
          "tool calls (median)", "schema valid", "note"]


def _task(f, row: dict):
    """The task as the catalog defines it now, when the run is of that version (an older version's oracle may grade
    other files)."""
    tid = row["task_version"].partition("@")[0]
    try:
        task = f.tasks.get(tid)
    except KeyError:
        return None
    return task if task.key() == row["task_version"] else None
