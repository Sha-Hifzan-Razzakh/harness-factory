"""MCPSessionPool (B11): every MCP session a run has. The tool set is frozen for the run."""

from __future__ import annotations

from contextlib import AsyncExitStack
from typing import Any

from harness_factory.env.mcp.servers import ToolCatalogFile
from harness_factory.mcp.catalog import ToolCatalog
from harness_factory.mcp.client import Launcher, MCPClient, ToolResult
from harness_factory.substrate.errors import ErrorKind, HarnessError


class MCPSessionPool:
    def __init__(self, declared: ToolCatalogFile, servers: list[str], env: dict[str, str],
                 launcher: Launcher | None = None, tenant: str | None = None,
                 tenant_seeds: dict[str, Any] | None = None) -> None:
        """tenant: this run's test tenant id (EXT-07). Servers with a `tenant:` spec get it as a header and are reset
        to tenant_seeds[server] (the task's env_seed.mock_api_state) before the run. Without one (e.g. discovery for
        `hfx tools freeze`) nothing is reset."""
        self.tenant = tenant
        self.tenant_seeds = tenant_seeds or {}
        self.declared = declared
        self.servers = servers
        self.env = env
        self.launcher = launcher
        self.clients: dict[str, MCPClient] = {}
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
                client = await self._stack.enter_async_context(
                    MCPClient(spec, self.env, self.launcher, tenant=self.tenant if spec.tenant else None))
                self._stack.push_async_callback(client.tenant_drop)       # runs before the session closes
                discovered += await client.list_tools()
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

    def transports(self) -> dict[str, str]:
        return {sid: c.transport.value for sid, c in self.clients.items()}

    async def call(self, tool_ref: str, args: dict[str, Any]) -> ToolResult:
        server, _, name = tool_ref.partition(".")
        client = self.clients.get(server)
        if client is None:
            raise HarnessError(ErrorKind.TOOL_NOT_ALLOWED, f"no session for server {server!r}")
        return await client.call_tool(name, args)
