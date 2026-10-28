"""Sandbox (B1): an isolated place one run executes in.

LocalSandbox   fresh temp directory per run; MCP servers run as local subprocesses. Fast, no isolation
               beyond path confinement in the servers. Default; fine for scripted runs and development.
DockerSandbox  same directories, bind-mounted into a locked-down container; MCP servers run inside it via
               `docker exec -i` over stdio. No network, read-only root filesystem, all capabilities dropped,
               no privilege escalation, pid/memory/cpu limits, runs as the host user. Use for real models.

Both expose the same interface. Graders read the host-side directories, so grading is identical either way.
Choose with HF_SANDBOX=local|docker.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Protocol

from harness_factory import settings
from harness_factory.env.mcp.servers import MCPServerSpec
from harness_factory.tasks.model import Task

Launch = tuple[str, list[str], dict[str, str]]       # command, args, extra environment for that process

WORKSPACE_IN_CONTAINER = "/work"
DB_IN_CONTAINER = "/state/task.sqlite"


class Sandbox(Protocol):
    kind: str
    workspace: Path
    db_path: Path

    def reset(self, task: Task) -> None: ...
    def env(self) -> dict[str, str]: ...
    def launch(self, spec: MCPServerSpec) -> Launch: ...
    def describe(self) -> dict[str, str]: ...
    def teardown(self) -> None: ...


class LocalSandbox:
    kind = "local"

    def __init__(self, label: str = "run") -> None:
        settings.ensure_dirs()
        self.label = label
        self.root = Path(tempfile.mkdtemp(prefix=f"{label}-", dir=settings.SANDBOXES_DIR))
        self.workspace = self.root / "workspace"
        self.db_path = self.root / "state" / "task.sqlite"
        self.workspace.mkdir(parents=True)
        self.db_path.parent.mkdir(parents=True)

    def reset(self, task: Task) -> None:
        """Restore the task's seed in place: the directories themselves are kept, so bind mounts stay valid."""
        if task.source_dir is None:
            raise ValueError("task has no source_dir; load it with load_task()")
        files, sql = task.env_seed.resolve(task.source_dir)
        for child in self.workspace.iterdir():
            shutil.rmtree(child) if child.is_dir() and not child.is_symlink() else child.unlink()
        if files:
            shutil.copytree(files, self.workspace, dirs_exist_ok=True)
        for f in self.db_path.parent.iterdir():          # database plus any journal/WAL files
            f.unlink()
        with sqlite3.connect(self.db_path) as c:
            if sql:
                c.executescript(sql.read_text())
            c.commit()

    def env(self) -> dict[str, str]:
        return {"HF_WORKSPACE": str(self.workspace), "HF_DB": str(self.db_path)}

    def launch(self, spec: MCPServerSpec) -> Launch:
        return sys.executable, ["-m", spec.module], self.env()

    def describe(self) -> dict[str, str]:
        return {"sandbox": self.kind}

    def teardown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        self.teardown()


class DockerSandbox(LocalSandbox):
    kind = "docker"

    def __init__(self, label: str = "run", image: str | None = None) -> None:
        from harness_factory.env.sandbox_image import ensure_image

        super().__init__(label)
        self.image = image or ensure_image()
        self.container = f"hf-{self.root.name}".lower()
        self.started = False

    def reset(self, task: Task) -> None:
        super().reset(task)
        if not self.started:
            self._start()

    def run_args(self) -> list[str]:
        """The isolation contract, in one place."""
        args = [
            "run", "-d", "--name", self.container,
            "--network", "none",
            "--read-only", "--tmpfs", "/tmp:rw,size=64m",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--pids-limit", "256", "--memory", "1g", "--cpus", "1",
            "-v", f"{self.workspace}:{WORKSPACE_IN_CONTAINER}",
            "-v", f"{self.db_path.parent}:{Path(DB_IN_CONTAINER).parent}",
            "--label", "hf.sandbox=1", "--label", f"hf.task={self.label}",
        ]
        if hasattr(os, "getuid"):                          # files written in the container stay owned by you
            args += ["--user", f"{os.getuid()}:{os.getgid()}"]
        # Idle on Python itself (the one binary every valid base image has) until teardown removes it.
        return [*args, self.image, "python", "-c", "import signal; signal.pause()"]

    def _start(self) -> None:
        from harness_factory.env.sandbox_image import docker

        # Set before running: a failed `docker run` can still leave a created container behind,
        # and teardown must remove it either way.
        self.started = True
        docker(*self.run_args(), timeout=60)

    def env(self) -> dict[str, str]:
        return {"HF_WORKSPACE": WORKSPACE_IN_CONTAINER, "HF_DB": DB_IN_CONTAINER}

    def launch(self, spec: MCPServerSpec) -> Launch:
        env_flags = [x for k, v in self.env().items() for x in ("-e", f"{k}={v}")]
        return settings.DOCKER_BIN, ["exec", "-i", *env_flags, self.container, "python", "-m", spec.module], {}

    def describe(self) -> dict[str, str]:
        return {"sandbox": self.kind, "sandbox_image": self.image}

    def teardown(self) -> None:
        from harness_factory.env.sandbox_image import docker

        if self.started:
            docker("rm", "-f", self.container, check=False, timeout=60)
            self.started = False
        super().teardown()


def make_sandbox(label: str, kind: str | None = None) -> LocalSandbox:
    kind = kind or settings.SANDBOX
    if kind == "local":
        return LocalSandbox(label)
    if kind == "docker":
        return DockerSandbox(label)
    raise ValueError(f"unknown sandbox kind {kind!r}; use local or docker")
