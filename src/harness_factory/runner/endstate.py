"""End states (GRD-04): what a run left behind, kept so a stored run can be re-graded after its sandbox is gone.

One zip per run, next to its trace (`<trace>.endstate.zip`):
  workspace/...      every file in the sandbox workspace
  state/task.sqlite  the task database (an sqlite3 serialize() of it: a consistent copy, WAL included)
  run.json           what the graders read besides the environment: the before-state, the run's result (status,
                     whether it was scored, final answer, harness violations), the spans graders saw, the remote
                     tenants' end state, max_steps, task class and weight profile

Settings, read at call time:
  HF_KEEP_ENDSTATE=0     keep nothing (default 1)
  HF_ENDSTATE_MAX_MB=50  skip a run whose workspace + database exceed this many MB before compression

Archive names are POSIX paths; extraction rebuilds them with the host's separators and refuses any name that
would land outside the target (absolute, drive-qualified or `..`), so bundles move between Linux and Windows.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import sqlite3
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from harness_factory.grading.base import GradeContext

log = logging.getLogger(__name__)

FORMAT = 1
_DATE = (1980, 1, 1, 0, 0, 0)            # fixed: zip can't store earlier dates, and bundles stay byte-stable
WORKSPACE = "workspace"
DB = "state/task.sqlite"
META = "run.json"


def keep_endstate() -> bool:
    return os.environ.get("HF_KEEP_ENDSTATE", "1") != "0"


def max_bytes() -> int:
    return int(float(os.environ.get("HF_ENDSTATE_MAX_MB", "50")) * 1024 * 1024)


def endstate_path_for(trace_path: Path) -> Path:
    return trace_path.with_suffix(".endstate.zip")


def _workspace_files(workspace: Path) -> list[tuple[str, Path]]:
    """(archive name, file) for every file in the workspace; a link pointing outside it is left out."""
    root = workspace.resolve()
    out = []
    for p in sorted(workspace.rglob("*")):
        if not p.is_file():
            continue
        if p.is_symlink() and not p.resolve().is_relative_to(root):
            continue
        out.append((f"{WORKSPACE}/{p.relative_to(workspace).as_posix()}", p))
    return out


def _db_bytes(db_path: Path) -> bytes | None:
    if not db_path.is_file():
        return None
    with contextlib.closing(sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)) as c:
        try:
            return c.serialize()
        except sqlite3.OperationalError:
            if db_path.stat().st_size == 0:          # a task without a database: an empty file, nothing to copy
                return b""
            raise


def save_endstate(trace_path: Path, ctx: GradeContext, **meta: Any) -> str | None:
    """Write the run's end-state bundle; its path, or None when it is off, too large, or unreadable. Never fails
    the run: an end state is for re-grading later, not part of this run's result."""
    if not keep_endstate():
        return None
    tmp: Path | None = None
    try:
        files = _workspace_files(ctx.workspace)
        db = _db_bytes(ctx.db_path)
        size = sum(p.stat().st_size for _, p in files) + len(db or b"")
        if size > max_bytes():
            log.warning("end state of %s not kept: %.1f MB > HF_ENDSTATE_MAX_MB", trace_path.name, size / 2**20)
            return None
        r = ctx.result
        doc = {
            "format": FORMAT, "run_key": r.run_key.as_str(), "status": r.status.value, "scored": r.scored,
            "error": r.error, "output": r.output, "violations": [v.model_dump() for v in r.violations],
            "before": ctx.before.model_dump(mode="json"), "remote": ctx.remote, "max_steps": ctx.max_steps,
            "spans": [s.model_dump(mode="json") for s in ctx.spans], "has_db": db is not None, **meta,
        }
        out = endstate_path_for(trace_path)
        tmp = out.with_suffix(".zip.part")
        with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as z:
            for name, p in files:
                z.writestr(zipfile.ZipInfo(name, _DATE), p.read_bytes(), compress_type=zipfile.ZIP_DEFLATED)
            if db is not None:
                z.writestr(zipfile.ZipInfo(DB, _DATE), db, compress_type=zipfile.ZIP_DEFLATED)
            z.writestr(zipfile.ZipInfo(META, _DATE), json.dumps(doc, default=str),
                       compress_type=zipfile.ZIP_DEFLATED)
        os.replace(tmp, out)
        return str(out)
    except (OSError, sqlite3.Error, ValueError, TypeError) as e:   # disk full, unreadable file, odd output
        log.warning("end state of %s not kept: %s: %s", trace_path.name, type(e).__name__, e)
        if tmp is not None:
            with contextlib.suppress(OSError):
                tmp.unlink(missing_ok=True)
        return None


def _target(dest: Path, name: str) -> Path:
    pure = PurePosixPath(name)
    # On Windows a ':' or '\\' in a name would mean a drive or a separator: refuse rather than guess.
    odd = (":", "\\") if os.name == "nt" else ()
    if pure.is_absolute() or ".." in pure.parts or any(c in part for part in pure.parts for c in odd):
        raise ValueError(f"unsafe name in end-state bundle: {name!r}")
    return dest.joinpath(*pure.parts)


def load_endstate(bundle: Path, dest: Path) -> tuple[Path, Path, dict[str, Any]]:
    """Unpack a bundle into dest; (workspace, database path, run.json). The database is absent if the run had
    none."""
    workspace, db_path = dest / WORKSPACE, dest.joinpath(*PurePosixPath(DB).parts)
    workspace.mkdir(parents=True, exist_ok=True)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(bundle) as z:
        meta = json.loads(z.read(META))
        if meta.get("format") != FORMAT:
            raise ValueError(f"{bundle}: end-state format {meta.get('format')!r}, this code reads {FORMAT}")
        for info in z.infolist():
            if info.is_dir() or info.filename == META:
                continue
            target = _target(dest, info.filename)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(z.read(info))
    return workspace, db_path, meta
