"""MCPSessionPool (B11): every MCP session a run has. The tool set is frozen for the run."""

from __future__ import annotations

import asyncio
import time
from contextlib import AsyncExitStack
from typing import Any

from harness_factory import settings
from harness_factory.env.mcp.servers import MCPServerSpec, ToolCatalogFile
from harness_factory.mcp.catalog import ToolCatalog
from harness_factory.mcp.client import Launcher, MCPClient, ToolResult
from harness_factory.substrate.errors import ErrorKind, HarnessError


class MCPSessionPool:
    def __init__(self, declared: ToolCatalogFile, servers: list[str], env: dict[str, str],
                 launcher: Launcher | None = None, tenant: str | None = None,
                 tenant_seeds: dict[str, Any] | None = None, recording_scopes: dict[str, str] | None = None) -> None:
        """tenant: this run's test tenant id (EXT-07). Servers with a `tenant:` spec get it as a header and are reset
        to tenant_seeds[server] (the task's env_seed.mock_api_state) before the run. Without one (e.g. discovery for
        `hfx tools freeze`) nothing is reset. recording_scopes: per remote server, what its recorded answers are keyed
        by besides the call (EXT-08; see mcp.recording)."""
        self.tenant = tenant
        self.recording_scopes = recording_scopes or {}
        self.tenant_seeds = tenant_seeds or {}
        self.declared = declared
        self.servers = servers
        self.env = env
        self.launcher = launcher
        self.clients: dict[str, MCPClient] = {}
        self.health: dict[str, dict[str, Any]] = {}     # ENV-06: each server's health probe, for the snapshot
        self.catalog: ToolCatalog | None = None
        self._stack = AsyncExitStack()

    async def __aenter__(self) -> MCPSessionPool:
        # A failed or cancelled __aenter__ gets no __aexit__: close every session already opened, or their
        # server processes outlive the run (and an interrupted campaign cannot exit).
        try:
            return await self._open()
        except BaseException:
            await self._stack.aclose()
            raise

    async def _open(self) -> MCPSessionPool:
        discovered = []
        for sid in self.servers:
            spec = self.declared.server(sid)
            try:
                client, tools = await self._start_healthy(spec)
                discovered += tools
                if spec.tenant is not None and self.tenant:
                    reset = await client.tenant_reset(self.tenant_seeds.get(sid, {}))
                    if reset.is_error or reset.infra:
                        raise HarnessError(ErrorKind.PROVISION, f"remote MCP server {sid!r} could not reset its "
                                                                f"test tenant: {reset.text[:300]}")
            except HarnessError:
                raise
            except Exception as e:
                if not spec.remote:
                    raise
                raise HarnessError(ErrorKind.PROVISION, f"remote MCP server {sid!r} at {spec.url}: "
                                                        f"{type(e).__name__}: {str(e)[:300]}") from e
            self.clients[sid] = client
        self.catalog = ToolCatalog.build(discovered, self.declared, self.servers)
        drift = self.catalog.drift()
        if drift:
            raise HarnessError(ErrorKind.PROVISION, f"tool catalog drift: {[d.model_dump() for d in drift]}")
        return self

    async def _start_healthy(self, spec: MCPServerSpec) -> tuple[MCPClient, list]:
        """ENV-06: start the server and probe it (initialize, list_tools, ping) within one deadline, before the run's
        clock starts. A server that hangs, dies or errors on the way is unhealthy: provisioning fails (the run is an
        ERROR, never scored) and whatever was started is closed (a cancelled __aenter__ closes itself)."""
        deadline = settings.health_timeout_s()
        t0 = time.monotonic()
        scope = asyncio.timeout(deadline)
        try:
            async with scope:
                client = await self._stack.enter_async_context(
                    MCPClient(spec, self.env, self.launcher, tenant=self.tenant if spec.tenant else None,
                              recording_scope=self.recording_scopes.get(spec.id)))
                self._stack.push_async_callback(client.tenant_drop)       # runs before the session closes
                tools = await client.list_tools()
                t_ping = time.monotonic()
                await client.ping()
        except (HarnessError, asyncio.CancelledError):
            if not scope.expired():
                raise
            raise self._unhealthy(spec, f"no answer within {deadline:g} s (start, initialize, list_tools, ping; "
                                        f"HF_MCP_HEALTH_TIMEOUT_S)") from None
        except Exception as e:
            if scope.expired():
                why = f"no answer within {deadline:g} s (start, initialize, list_tools, ping; HF_MCP_HEALTH_TIMEOUT_S)"
                raise self._unhealthy(spec, why) from None
            if spec.remote:                     # the remote path keeps its own message (the caller wraps it)
                raise
            raise self._unhealthy(spec, f"{type(e).__name__}: {str(e)[:300]}") from e
        done = time.monotonic()
        self.health[spec.id] = {"status": "ok", "probe": "recording" if client.mode == "replay" else "ping",
                                "ready_s": round(done - t0, 3), "ping_ms": round((done - t_ping) * 1000, 1)}
        return client, tools

    @staticmethod
    def _unhealthy(spec: MCPServerSpec, why: str) -> HarnessError:
        where = f" at {spec.url}" if spec.remote else ""
        return HarnessError(ErrorKind.PROVISION, f"MCP server {spec.id!r}{where} is unhealthy: {why}; the run did "
                                                 f"not start")

    async def __aexit__(self, *exc) -> None:
        await self._stack.aclose()

    def tenant_servers(self) -> list[str]:
        return [sid for sid, c in self.clients.items() if c.spec.tenant is not None and c.tenant]

    async def export_tenants(self) -> tuple[dict[str, Any], str | None]:
        """Every test tenant's state after the run, or the first reason one can't be read (the environment's
        failure: the run can't be graded)."""
        out: dict[str, Any] = {}
        for sid in self.tenant_servers():
            try:
                state, why = await self.clients[sid].tenant_export()
            except Exception as e:  # noqa: BLE001  (a server gone mid-run: reported, not raised)
                state, why = None, f"{type(e).__name__}: {str(e)[:200]}"
            if why is not None:
                return out, f"test tenant of {sid!r}: {why}"
            out[sid] = state
        return out, None

    def server_versions(self) -> dict[str, str]:
        """What each live remote server reported at initialize ('' = it reports none, so the pin is unchecked)."""
        return {sid: c.server_version for sid, c in self.clients.items() if c.server_version is not None}

    def transports(self) -> dict[str, str]:
        return {sid: c.transport.value for sid, c in self.clients.items()}

    async def call(self, tool_ref: str, args: dict[str, Any]) -> ToolResult:
        server, _, name = tool_ref.partition(".")
        client = self.clients.get(server)
        if client is None:
            raise HarnessError(ErrorKind.TOOL_NOT_ALLOWED, f"no session for server {server!r}")
        return await client.call_tool(name, args)
