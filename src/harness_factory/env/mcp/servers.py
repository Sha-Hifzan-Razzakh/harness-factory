"""MCPServerSpec, ToolSpec, SideEffect (B11, environment side). Declared in catalog/tools.yaml."""

from __future__ import annotations

import sys
from enum import Enum
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, model_validator


class SideEffect(str, Enum):
    READ = "READ"                    # no change
    REVERSIBLE = "REVERSIBLE"        # change can be undone
    IRREVERSIBLE = "IRREVERSIBLE"    # send, pay, delete


RISK_TO_TIER = {"R0": "T1", "R1": "T2", "R2": "T2", "R3": "T3"}
TIER_ORDER = ["T1", "T2", "T3", "T4"]
USAGE_META = "harness-factory/usage"    # the `_meta` key an agent tool reports its usage under (EXT-10)


class AgentTool(BaseModel):
    """A tool that is another agent (EXT-10): it runs its own model calls and reports what they used.

    Its answer must carry `_meta["harness-factory/usage"] = {tokens_in, tokens_out, usd?, model?}`; the tokens
    count toward the run's token budget and cost, so a harness that hands work to an agent pays for it. A missing
    `usd` is priced at the reference rates below (per million tokens), as free models are. Prices are not hashed."""
    max_calls: int = Field(default=3, ge=1)     # per run; calls past it are refused and count as invalid in Q
    price_in: float = Field(default=0.0, ge=0)
    price_out: float = Field(default=0.0, ge=0)

    def usd(self, tokens_in: int, tokens_out: int) -> float:
        return (tokens_in * self.price_in + tokens_out * self.price_out) / 1e6


class TenantSpec(BaseModel):
    """A remote server's test tenant (EXT-07): state the factory can reset before a run and read back after it.

    Each run gets its own tenant id, sent as a request header the model can't set; the server keeps one state per
    tenant. The three admin tools are called by the factory only: they are never declared as tools, never offered to
    the model, and left out of discovery."""
    header: str = "HF-Tenant"
    reset: str = "_tenant_reset"     # (state: the task's seed) -> anything; the tenant now holds exactly that state
    export: str = "_tenant_export"   # () -> the tenant's state as JSON (structured content or JSON text)
    drop: str | None = "_tenant_drop"  # () -> anything; best effort when the run's session closes

    def admin_tools(self) -> set[str]:
        return {self.reset, self.export} | ({self.drop} if self.drop else set())


class Escalation(BaseModel):
    """Calls the tool's declared side effect doesn't cover (env/mcp/effects.py): when `classifier` says a call's
    arguments match, that call has this side effect and risk instead, so the recipe's permission policy for it applies
    (e.g. `sql.execute` with `DELETE FROM orders` is IRREVERSIBLE, refused like `fs.delete_file`)."""
    classifier: str
    side_effect: SideEffect
    risk: Literal["R0", "R1", "R2", "R3"]

    @model_validator(mode="after")
    def _known(self) -> Escalation:
        from harness_factory.env.mcp.effects import CLASSIFIERS
        if self.classifier not in CLASSIFIERS:
            raise ValueError(f"unknown side-effect classifier {self.classifier!r}; known: {', '.join(CLASSIFIERS)}")
        return self


class ToolSpec(BaseModel):
    name: str
    side_effect: SideEffect
    risk: Literal["R0", "R1", "R2", "R3"]
    timeout_s: float = 30.0
    schema_hash: str | None = None   # declared hash; drift against discovery fails provisioning
    # Declared hash of the tool's description, the text the model reads to decide what the tool does. Drift fails
    # provisioning like a schema change: a new description is a new experiment (part of env_hash).
    description_hash: str | None = None
    escalation: Escalation | None = None   # calls with a stronger side effect than the declared one
    # A READ tool that reveals nothing beyond these tools: a task allowing all of them allows this one too, so a
    # new capability (e.g. search) needs no edit to every task's allowance (and no task hash change). [] = every
    # task: the tool reads only harness material, nothing from the task's world (e.g. skills.load).
    granted_by: list[str] | None = None
    agent: AgentTool | None = None   # set when the tool is another agent (EXT-10)

    def hashed(self) -> dict:
        """What of this spec is part of env_hash: unset optional fields are left out (so adding one keeps every
        existing hash), and so are prices."""
        optional = ("granted_by", "agent", "description_hash", "escalation")
        data = self.model_dump(mode="json", exclude={k for k in optional if getattr(self, k) is None})
        if self.agent is not None:
            data["agent"] = {"max_calls": self.agent.max_calls}
        return data

    def ref(self, server: str) -> str:
        return f"{server}.{self.name}"

    def effect_for(self, args: dict) -> tuple[SideEffect, str]:
        """(side effect, risk) of one call: the declared ones, or the escalation's when its classifier matches."""
        if self.escalation is not None:
            from harness_factory.env.mcp.effects import CLASSIFIERS
            if CLASSIFIERS[self.escalation.classifier](args):
                return self.escalation.side_effect, self.escalation.risk
        return self.side_effect, self.risk


class MCPServerSpec(BaseModel):
    """A local server (stdio, started inside each run's sandbox) or a remote one (streamable HTTP, an existing
    service the factory's MCP client connects to; EXT-05). The sandbox never gets network access for a remote
    server: the client runs in the factory process."""
    id: str
    transport: Literal["stdio", "streamable_http"] = "stdio"
    module: str | None = None        # stdio: python module run with -m inside the sandbox
    url: str | None = None           # streamable_http: the server endpoint
    version: str | None = None       # streamable_http: the pinned server version; part of env_hash
    auth_env: str | None = None      # streamable_http: env var holding a bearer token (from .env, never a file)
    tenant: TenantSpec | None = None  # streamable_http: per-run test tenants; required for tools that change state
    tools: list[ToolSpec] = Field(default_factory=list)

    @model_validator(mode="after")
    def _transport_fields(self) -> MCPServerSpec:
        if self.transport == "stdio" and not self.module:
            raise ValueError(f"server {self.id!r}: a stdio server needs `module`")
        if self.transport == "streamable_http":
            if not self.url or not self.version:
                raise ValueError(f"server {self.id!r}: a remote server needs `url` and a pinned `version`")
            # EXT-07: a remote side effect must land somewhere the factory can reset and grade: a test tenant.
            writes = [t.name for t in self.tools if t.side_effect != SideEffect.READ]
            if writes and self.tenant is None:
                raise ValueError(f"server {self.id!r}: remote tools that change state need a test tenant "
                                 f"(`tenant:`, EXT-07); not READ: {', '.join(writes)}")
        if self.tenant is not None:
            if self.transport == "stdio":
                raise ValueError(f"server {self.id!r}: `tenant` is for remote servers (a local one is reset with "
                                 f"its sandbox)")
            clash = self.tenant.admin_tools() & {t.name for t in self.tools}
            if clash:
                raise ValueError(f"server {self.id!r}: tenant admin tools are the factory's, never declared as "
                                 f"tools: {', '.join(sorted(clash))}")
        agents = [t.name for t in self.tools if t.agent is not None]
        if agents and self.transport == "stdio":
            # An agent calls a model, and nothing in the sandbox can reach one (it has no network).
            raise ValueError(f"server {self.id!r}: agent tools need a remote server (EXT-10); "
                             f"agent on stdio: {', '.join(agents)}")
        return self

    @property
    def remote(self) -> bool:
        return self.transport == "streamable_http"

    def command(self) -> tuple[str, list[str]]:
        return sys.executable, ["-m", self.module or ""]


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

    def env_hash(self, server_ids: list[str], provider_model_id: str) -> str:
        """Identity of the tool environment a run sees, plus the concrete provider model. Part of RunKey."""
        from harness_factory.hashing import content_hash
        specs = {t.ref(s.id): t.hashed() for s in self.servers if s.id in server_ids for t in s.tools}
        data = {"tools": specs, "provider_model_id": provider_model_id}
        remote = {s.id: f"{s.url}@{s.version}" for s in self.servers if s.id in server_ids and s.remote}
        if remote:                                   # only then, so local-only environments keep their hash
            data["remote"] = remote
        return content_hash(data, 12)

    def unhashed_tools(self) -> list[str]:
        return [t.ref(s.id) for s in self.servers for t in s.tools if not t.schema_hash or not t.description_hash]


def load_tool_catalog(path: Path) -> ToolCatalogFile:
    return ToolCatalogFile.model_validate(yaml.safe_load(path.read_text()))
