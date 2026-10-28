"""Sandbox (B1): an isolated place one run executes in.

P0 ships LocalSandbox: a fresh temp directory per run holding the workspace and the task database.
A DockerSandbox with network isolation implements the same interface in P1.
"""

from __future__ import annotations

import shutil
import sqlite3
import tempfile
from pathlib import Path
from typing import Protocol

from harness_factory import settings
from harness_factory.tasks.model import Task


class Sandbox(Protocol):
    workspace: Path
    db_path: Path

    def reset(self, task: Task) -> None: ...
    def env(self) -> dict[str, str]: ...
    def teardown(self) -> None: ...


class LocalSandbox:
    def __init__(self, label: str = "run") -> None:
        settings.ensure_dirs()
        self.root = Path(tempfile.mkdtemp(prefix=f"{label}-", dir=settings.SANDBOXES_DIR))
        self.workspace = self.root / "workspace"
        self.db_path = self.root / "state" / "task.sqlite"
        self.workspace.mkdir(parents=True)
        self.db_path.parent.mkdir(parents=True)

    def reset(self, task: Task) -> None:
        """Restore the task's seed: copy files, build the database from its SQL script."""
        if task.source_dir is None:
            raise ValueError("task has no source_dir; load it with load_task()")
        files, sql = task.env_seed.resolve(task.source_dir)
        shutil.rmtree(self.workspace, ignore_errors=True)
        if files:
            shutil.copytree(files, self.workspace)
        else:
            self.workspace.mkdir(parents=True)
        if self.db_path.exists():
            self.db_path.unlink()
        with sqlite3.connect(self.db_path) as c:
            if sql:
                c.executescript(sql.read_text())
            c.commit()

    def env(self) -> dict[str, str]:
        return {"HF_WORKSPACE": str(self.workspace), "HF_DB": str(self.db_path)}

    def teardown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def __enter__(self) -> LocalSandbox:
        return self

    def __exit__(self, *exc) -> None:
        self.teardown()
