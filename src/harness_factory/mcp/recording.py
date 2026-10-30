"""Record and replay of remote MCP servers (EXT-08).

A live run against a remote server can't be repeated: the service changes, goes down, or costs a token. In record
mode every remote answer is saved; in replay mode the same runs are answered from the recording, offline, with no
token and no network. Recordings are per server and pinned version (a new version needs a new recording), keyed by
tool and arguments:

    <HF_REMOTE_RECORDINGS>/<server>@<version>/_tools.json            discovery (tool names and schemas)
    <HF_REMOTE_RECORDINGS>/<server>@<version>/<tool>/<args hash>.json one answer: text, error flag, structured

Remote tools are READ-only (EXT-07), so the same call returning the same answer is a fair stand-in. A call that was
never recorded is answered with an error marked as the environment's (`replay_miss`), never the model's.
Nothing secret is written: answers only, never request headers.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from harness_factory.env.mcp.servers import MCPServerSpec
from harness_factory.hashing import content_hash


class Recording:
    def __init__(self, root: Path, spec: MCPServerSpec) -> None:
        self.dir = root / f"{spec.id}@{spec.version}"

    @staticmethod
    def key(tool: str, args: dict[str, Any]) -> str:
        return content_hash({"tool": tool, "args": args}, 16)

    def exists(self) -> bool:
        return (self.dir / "_tools.json").is_file()

    def save_tools(self, tools: list[dict[str, Any]]) -> None:
        _write(self.dir / "_tools.json", tools)

    def load_tools(self) -> list[dict[str, Any]]:
        return json.loads((self.dir / "_tools.json").read_text())

    def save_call(self, tool: str, args: dict[str, Any], answer: dict[str, Any]) -> None:
        _write(self.dir / tool / f"{self.key(tool, args)}.json", {"tool": tool, "args": args, **answer})

    def load_call(self, tool: str, args: dict[str, Any]) -> dict[str, Any] | None:
        path = self.dir / tool / f"{self.key(tool, args)}.json"
        return json.loads(path.read_text()) if path.is_file() else None


def _write(path: Path, data: Any) -> None:
    """Atomic: parallel runs recording the same call never leave a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    with os.fdopen(fd, "w") as fh:
        json.dump(data, fh, indent=1, default=str)
    os.replace(tmp, path)
