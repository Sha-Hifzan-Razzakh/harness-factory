"""Record and replay of remote MCP servers (EXT-08).

A live run against a remote server can't be repeated: the service changes, goes down, or costs a token. In record
mode every remote answer is saved; in replay mode the same runs are answered from the recording, offline, with no
token and no network. Recordings are per server and pinned version (a new version needs a new recording), keyed by
tool and arguments within the run's scope: the task (id and version), the run seed and the test tenant's seed state.
Two tasks that make the same call against different worlds never share an answer (a recording made before scopes
existed is a miss: record it again):

    <HF_REMOTE_RECORDINGS>/<server>@<version>/_tools.json            discovery (tool names and schemas)
    <HF_REMOTE_RECORDINGS>/<server>@<version>/<tool>/<args hash>.json one answer: text, error flag, structured

The n-th repeat of the same call in a run is recorded under its own key (n > 0), so a call made before and after a
change gets the answer it got live; a READ call repeated more often than recorded falls back to the first answer.
A call that was never recorded is answered with an error marked as the environment's (`replay_miss`), never the
model's. A test tenant's export (EXT-07) is recorded with the calls that changed state before it, and a replay that
made different ones is told so (`replay_diverged`): the recorded end state is not its end state.
Nothing secret is written: answers only, never request headers (tenant ids included).
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from harness_factory.env.mcp.servers import MCPServerSpec
from harness_factory.hashing import content_hash


def recording_scope(task_key: str, seed: int, tenant_seed: Any) -> str:
    """What a recorded answer depends on besides the call: which task, which run seed, and the state the test tenant
    was reset to (a seed edited without a version bump is still another world)."""
    return content_hash({"task": task_key, "seed": seed, "tenant_seed": tenant_seed}, 16)


class Recording:
    def __init__(self, root: Path, spec: MCPServerSpec, scope: str | None = None) -> None:
        self.dir = root / f"{spec.id}@{spec.version}"
        self.scope = scope

    @staticmethod
    def call_key(tool: str, args: dict[str, Any], n: int = 0, scope: str | None = None) -> str:
        """n: earlier identical calls in the same session. 0 keeps the key recordings had before repeats counted."""
        return content_hash({"tool": tool, "args": args, **({"n": n} if n else {}),
                             **({"scope": scope} if scope else {})}, 16)

    def key(self, tool: str, args: dict[str, Any], n: int = 0) -> str:
        return self.call_key(tool, args, n, self.scope)

    def exists(self) -> bool:
        return (self.dir / "_tools.json").is_file()

    def save_tools(self, tools: list[dict[str, Any]]) -> None:
        _write(self.dir / "_tools.json", tools)

    def load_tools(self) -> list[dict[str, Any]]:
        return json.loads((self.dir / "_tools.json").read_text())

    def save_call(self, tool: str, args: dict[str, Any], answer: dict[str, Any], n: int = 0) -> None:
        _write(self.dir / tool / f"{self.key(tool, args, n)}.json", {"tool": tool, "args": args, **answer})

    def load_call(self, tool: str, args: dict[str, Any], n: int = 0) -> dict[str, Any] | None:
        path = self.dir / tool / f"{self.key(tool, args, n)}.json"
        return json.loads(path.read_text()) if path.is_file() else None


def _write(path: Path, data: Any) -> None:
    """Atomic: parallel runs recording the same call never leave a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    with os.fdopen(fd, "w") as fh:
        json.dump(data, fh, indent=1, default=str)
    os.replace(tmp, path)
