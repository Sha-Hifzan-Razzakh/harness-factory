"""File operations for authoring: a cross-process lock and an all-or-nothing apply of several file writes."""

from __future__ import annotations

import contextlib
import os
import shutil
import time
from collections.abc import Iterator
from pathlib import Path

from pydantic import BaseModel


class FileOp(BaseModel):
    """One file to write (`text`) or delete (`text is None`), relative to the repository root."""
    path: str
    text: str | None

    @property
    def action(self) -> str:
        return "delete" if self.text is None else "write"


class LockTimeout(RuntimeError):
    pass


@contextlib.contextmanager
def catalog_lock(path: Path, timeout_s: float = 60.0, stale_s: float = 300.0) -> Iterator[None]:
    """Only one authoring change at a time, across processes (the CLI and the API may both write). A lock file
    created with O_EXCL works on every OS; one older than `stale_s` was left by a crashed process."""
    path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            try:
                if time.time() - path.stat().st_mtime > stale_s:
                    path.unlink(missing_ok=True)
                    continue
            except FileNotFoundError:
                continue
            if time.monotonic() > deadline:
                raise LockTimeout(f"another catalog change holds {path} (delete it if no hfx process is running)")
            time.sleep(0.1)
    try:
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        yield
    finally:
        path.unlink(missing_ok=True)


def _write_atomic(target: Path, text: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.authoring-tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, target)


def _prune_empty_dirs(start: Path, stop: Path) -> None:
    d = start
    while d != stop and d.is_relative_to(stop):
        try:
            d.rmdir()
        except OSError:
            return
        d = d.parent


def apply(root: Path, ops: list[FileOp]) -> None:
    """Write every op, or none: on any failure the files touched so far are restored."""
    done: list[tuple[Path, bytes | None]] = []
    try:
        for op in ops:
            target = (root / op.path).resolve()
            if not target.is_relative_to(root.resolve()):
                raise ValueError(f"path escapes the repository: {op.path}")
            done.append((target, target.read_bytes() if target.is_file() else None))
            if op.text is None:
                target.unlink(missing_ok=True)
                _prune_empty_dirs(target.parent, root.resolve() / Path(op.path).parts[0])
            else:
                _write_atomic(target, op.text)
    except BaseException:
        for target, before in reversed(done):
            if before is None:
                target.unlink(missing_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(before)
        raise


def stage(root: Path, into: Path, ops: list[FileOp]) -> None:
    """A copy of the catalog and the prompts with `ops` applied: what the repository would look like."""
    for name in ("catalog", "prompts"):
        if (root / name).is_dir():
            shutil.copytree(root / name, into / name)
    apply(into, ops)
