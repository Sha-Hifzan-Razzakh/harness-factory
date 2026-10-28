"""MCPServerSpec, ToolSpec, SideEffect (B11, environment side). Declared in catalog/tools.yaml."""

from __future__ import annotations

import sys
from enum import Enum
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field


class SideEffect(str, Enum):
    READ = "READ"                    # no change
    REVERSIBLE = "REVERSIBLE"        # change can be undone
    IRREVERSIBLE = "IRREVERSIBLE"    # send, pay, delete


RISK_TO_TIER = {"R0": "T1", "R1": "T2", "R2": "T2", "R3": "T3"}
TIER_ORDER = ["T1", "T2", "T3", "T4"]


class ToolSpec(BaseModel):
    name: str
    side_effect: SideEffect
    risk: Literal["R0", "R1", "R2", "R3"]
    timeout_s: float = 30.0
    schema_hash: str | None = None   # declared hash; drift against discovery fails provisioning

    def ref(self, server: str) -> str:
        return f"{server}.{self.name}"


class MCPServerSpec(BaseModel):
    id: str
    transport: Literal["stdio", "streamable_http"] = "stdio"
    module: str                      # python module run with -m for stdio servers
    tools: list[ToolSpec] = Field(default_factory=list)

    def command(self) -> tuple[str, list[str]]:
        return sys.executable, ["-m", self.module]


class ToolCatalogFile(BaseModel):
    servers: list[MCPServerSpec]

    def server(self, server_id: str) -> MCPServerSpec:
        for s in self.servers:
            if s.id == server_id:
                return s
        raise KeyError(f"unknown MCP server {server_id!r}")

    def spec(self, tool_ref: str) -> ToolSpec:
        server_id, _, name = tool_ref.partition(".")
        for t in self.server(server_id).tools:
            if t.name == name:
                return t
        raise KeyError(f"tool {tool_ref!r} is not declared")

    def all_refs(self) -> list[str]:
        return [t.ref(s.id) for s in self.servers for t in s.tools]


def load_tool_catalog(path: Path) -> ToolCatalogFile:
    return ToolCatalogFile.model_validate(yaml.safe_load(path.read_text()))
