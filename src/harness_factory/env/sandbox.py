"""Sandbox (B1): an isolated place one run executes in.

LocalSandbox   fresh temp directory per run; MCP servers run as local subprocesses. Fast, no isolation
               beyond path confinement in the servers. Default; fine for scripted runs and development.
DockerSandbox  same directories, bind-mounted into a locked-down container; MCP servers run inside it via
               `docker exec -i` over stdio. No network, read-only root filesystem, all capabilities dropped,
               no privilege escalation, pid/memory/cpu limits, runs as the host user. Use for real models.

Both expose the same interface. Graders read the host-side directories, so grading is identical either way.
Checks that run code or git after the run (tests_pass, git_log_contains) go through `probe()`, which runs them
where the agent's code ran: a local subprocess, or `docker exec` in the run's container (ADR 0012).
Choose with HF_SANDBOX=local|docker.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Protocol

from harness_factory import settings
from harness_factory.env.mcp import deps
from harness_factory.env.mcp.impl import proc
from harness_factory.env.mcp.servers import MCPServerSpec
from harness_factory.env.state import BIZ_FILE, JSON_STATES, SERVER_STATES
from harness_factory.substrate.errors import ErrorKind, HarnessError
from harness_factory.tasks.model import Task

Launch = tuple[str, list[str], dict[str, str]]       # command, args, extra environment for that process

WORKSPACE_IN_CONTAINER = "/work"
DB_IN_CONTAINER = "/state/task.sqlite"
SKILLS_IN_CONTAINER = "/state/skills"   # installed by the provisioner next to the database, outside the workspace
GIT_DIR_IN_CONTAINER = "/state/git"     # a git-seeded task's repository, outside the workspace (ADR 0012)
PROBE_MODULE = "harness_factory.env.mcp.impl.proc"
PROBE_SLACK_S = 60                      # on top of the probe's own limit, before it counts as an infrastructure fault
BIZ_IN_CONTAINER = f"/state/{BIZ_FILE}"  # the biz server's business state, next to the database (env_seed.biz_state)
WEB_DIR = "web"                          # the compiled web corpus (env_seed.corpus, ADR 0018): read-only, in the state
WEB_IN_CONTAINER = f"/state/{WEB_DIR}"   # folder, never the workspace; its own read-only mount in Docker
REF_DIR = "reference"                    # env_seed.reference (ADR 0021): read-only data a server reads, in the state
REF_FILE = "reference.json"              # folder like the web corpus; HF_REFERENCE names the file
REF_IN_CONTAINER = f"/state/{REF_DIR}"
STATE_DIR_IN_CONTAINER = "/state"       # every stateful server's JSON state sits there too (env/state.JSON_STATES)


class Sandbox(Protocol):
    kind: str
    workspace: Path
    db_path: Path

    def reset(self, task: Task) -> None: ...
    def env(self) -> dict[str, str]: ...
    def probe(self, request: dict, timeout_s: float) -> dict: ...
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
        self.git_seed: str | None = None      # the seed commit of a git-seeded task
        self.web_dir.mkdir()                  # kept for the sandbox's life: Docker mounts it read-only
        self.ref_dir.mkdir()                  # the same for the task's reference data (ADR 0021)
        self.corpus_info: dict | None = None  # the web corpus a run sees (id, version, index id), for its snapshot

    def reset(self, task: Task) -> None:
        """Restore the task's seed in place: the directories themselves are kept, so bind mounts stay valid."""
        if task.source_dir is None:
            raise ValueError("task has no source_dir; load it with load_task()")
        files, sql = task.env_seed.resolve(task.source_dir)
        for child in self.workspace.iterdir():
            shutil.rmtree(child) if child.is_dir() and not child.is_symlink() else child.unlink()
        if files:
            shutil.copytree(files, self.workspace, dirs_exist_ok=True)
        for f in self.db_path.parent.iterdir():          # database plus any journal/WAL files, installed skills
            if f not in (self.web_dir, self.ref_dir):
                shutil.rmtree(f) if f.is_dir() and not f.is_symlink() else f.unlink()
        self._mount_corpus(task)
        self._mount_reference(task)
        with sqlite3.connect(self.db_path) as c:
            if sql:
                c.executescript(sql.read_text())
            c.commit()
        self.git_seed = None
        if task.env_seed.git:
            try:
                self.git_seed = proc.init_seed_repo(self.git_dir, self.workspace)
            except proc.ProcError as e:
                raise HarnessError(ErrorKind.SANDBOX, f"{task.key()} needs a git repository: {e}") from e

        for name, seed in task.env_seed.state_seeds(task.source_dir).items():
            # A fresh copy per run, for biz and every other stateful server: the seed itself is never written.
            what = "biz_state" if name == "biz" else f"server_state.{name}"
            try:
                if not isinstance(json.loads(seed.read_text()), dict):
                    raise ValueError("not a JSON object")
            except ValueError as e:
                raise ValueError(f"{task.key()}: {what} seed {seed} is not a JSON object: {e}") from e
            shutil.copyfile(seed, self.state_path(name))

    def _mount_corpus(self, task: Task) -> None:
        """The task's web corpus, compiled once per version (env/corpus.py), copied read-only into web/. The
        folder itself stays, emptied, so a Docker bind mount of it stays valid across resets."""
        _writable(self.web_dir)
        for f in self.web_dir.iterdir():
            shutil.rmtree(f) if f.is_dir() and not f.is_symlink() else f.unlink()
        self.corpus_info = None
        if not task.env_seed.corpus:
            return
        from harness_factory.env.corpus import CorpusError, compiled
        try:
            corpus = task.env_seed.web_corpus(task.source_dir)
            stale = corpus.stale()
            if stale:
                raise CorpusError(stale)
            src = compiled(corpus)
        except CorpusError as e:
            raise HarnessError(ErrorKind.PROVISION, f"{task.key()}: {e}") from e
        dest = self.web_dir / src.name
        shutil.copyfile(src, dest)
        dest.chmod(0o444)
        self.web_dir.chmod(0o555)
        self.corpus_info = corpus.info()

    def _mount_reference(self, task: Task) -> None:
        """The task's reference file (env_seed.reference), copied read-only into reference/. As with the corpus, the
        folder stays, emptied, so a Docker bind mount of it stays valid across resets."""
        _writable(self.ref_dir)
        for f in self.ref_dir.iterdir():
            shutil.rmtree(f) if f.is_dir() and not f.is_symlink() else f.unlink()
        src = task.env_seed.reference_file(task.source_dir)
        if src is None:
            return
        if not src.is_file():
            raise HarnessError(ErrorKind.PROVISION, f"{task.key()}: env_seed.reference {src} is not a file")
        dest = self.ref_dir / REF_FILE
        shutil.copyfile(src, dest)
        dest.chmod(0o444)
        self.ref_dir.chmod(0o555)

    @property
    def web_dir(self) -> Path:
        return self.db_path.parent / WEB_DIR

    @property
    def ref_dir(self) -> Path:
        return self.db_path.parent / REF_DIR

    @property
    def git_dir(self) -> Path:
        return self.db_path.parent / "git"

    def env(self) -> dict[str, str]:
        return {"HF_WORKSPACE": str(self.workspace), "HF_DB": str(self.db_path), "HF_SKILLS": str(self.skills_dir),
                "HF_GIT_DIR": str(self.git_dir), "HF_BIZ": str(self.biz_path), "HF_WEB": str(self.web_dir),
                "HF_REFERENCE": str(self.ref_dir / REF_FILE),
                **{JSON_STATES[n][1]: str(self.state_path(n)) for n in SERVER_STATES}}

    @property
    def skills_dir(self) -> Path:
        return self.db_path.parent / "skills"

    @property
    def biz_path(self) -> Path:
        return self.db_path.parent / BIZ_FILE

    def state_path(self, name: str) -> Path:
        """Where a stateful server's JSON state lives for this run (biz_path for biz)."""
        return self.db_path.parent / JSON_STATES[name][0]

    def launch(self, spec: MCPServerSpec) -> Launch:
        # The server runs with the factory's own Python, so a server with libraries beyond mcp (ADR 0013) needs
        # them installed here; say so now rather than let the process die during the MCP handshake.
        deps.require_server(spec.module, spec.id)
        return sys.executable, ["-m", spec.module], self.env()

    def describe(self) -> dict[str, str]:
        return {"sandbox": self.kind}

    def probe_command(self) -> tuple[list[str], dict[str, str]]:
        from harness_factory.mcp.client import process_env
        return [sys.executable, "-P", "-m", PROBE_MODULE], process_env(self.env())

    def probe(self, request: dict, timeout_s: float) -> dict:
        """Ask proc.probe a grading question inside the sandbox (tests_pass, git_log_contains). A probe that can't
        answer is the environment's fault, never the agent's: it raises HarnessError(SANDBOX)."""
        argv, env = self.probe_command()
        try:
            done = subprocess.run(argv, input=json.dumps(request), capture_output=True, text=True, env=env,
                                  cwd=self.workspace, timeout=timeout_s + PROBE_SLACK_S, check=False)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise HarnessError(ErrorKind.SANDBOX, f"grading probe {request.get('op')} failed: {e}") from e
        lines = done.stdout.strip().splitlines()
        try:
            answer = json.loads(lines[-1]) if lines else None
        except ValueError:
            answer = None
        if not isinstance(answer, dict) or "error" in answer:
            why = answer["error"] if isinstance(answer, dict) else (done.stderr or done.stdout).strip()[-500:]
            raise HarnessError(ErrorKind.SANDBOX, f"grading probe {request.get('op')} failed: {why}")
        return answer

    def teardown(self) -> None:
        _writable(self.web_dir)                            # read-only files don't delete on Windows
        _writable(self.ref_dir)
        shutil.rmtree(self.root, ignore_errors=True)

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        self.teardown()


class DockerSandbox(LocalSandbox):
    kind = "docker"

    def __init__(self, label: str = "run", image: str | None = None) -> None:
        from harness_factory.env.sandbox_image import ensure_image

        # Image first: if Docker is down or the build fails, nothing has been created yet, so nothing can leak.
        self.image = image or ensure_image()
        super().__init__(label)
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
            "-v", f"{self.db_path.parent / WEB_DIR}:{WEB_IN_CONTAINER}:ro",     # the web corpus (ADR 0018)
            "-v", f"{self.db_path.parent / REF_DIR}:{REF_IN_CONTAINER}:ro",     # reference data (ADR 0021)
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
        return {"HF_WORKSPACE": WORKSPACE_IN_CONTAINER, "HF_DB": DB_IN_CONTAINER, "HF_SKILLS": SKILLS_IN_CONTAINER,
                "HF_GIT_DIR": GIT_DIR_IN_CONTAINER,
                "HF_BIZ": BIZ_IN_CONTAINER, "HF_WEB": WEB_IN_CONTAINER,
                "HF_REFERENCE": f"{REF_IN_CONTAINER}/{REF_FILE}",
                **{JSON_STATES[n][1]: f"{STATE_DIR_IN_CONTAINER}/{JSON_STATES[n][0]}" for n in SERVER_STATES}}

    def launch(self, spec: MCPServerSpec) -> Launch:
        env_flags = [x for k, v in self.env().items() for x in ("-e", f"{k}={v}")]
        return settings.DOCKER_BIN, ["exec", "-i", *env_flags, self.container, "python", "-m", spec.module], {}

    def probe_command(self) -> tuple[list[str], dict[str, str]]:
        from harness_factory.mcp.client import process_env
        env_flags = [x for k, v in self.env().items() for x in ("-e", f"{k}={v}")]
        return ([settings.DOCKER_BIN, "exec", "-i", "-w", WORKSPACE_IN_CONTAINER, *env_flags, self.container,
                 "python", "-P", "-m", PROBE_MODULE], process_env({}))

    def describe(self) -> dict[str, str]:
        return {"sandbox": self.kind, "sandbox_image": self.image}

    def teardown(self) -> None:
        from harness_factory.env.sandbox_image import docker

        if self.started:
            docker("rm", "-f", self.container, check=False, timeout=60)
            self.started = False
        super().teardown()


def _writable(web_dir: Path) -> None:
    """Undo the read-only marks of a mounted corpus or reference, so a reset or teardown can remove it."""
    if not web_dir.is_dir():
        return
    web_dir.chmod(0o755)
    for f in web_dir.iterdir():
        if f.is_file() and not f.is_symlink():
            f.chmod(0o644)


def make_sandbox(label: str, kind: str | None = None) -> LocalSandbox:
    kind = kind or settings.SANDBOX
    if kind == "local":
        return LocalSandbox(label)
    if kind == "docker":
        return DockerSandbox(label)
    raise ValueError(f"unknown sandbox kind {kind!r}; use local or docker")
