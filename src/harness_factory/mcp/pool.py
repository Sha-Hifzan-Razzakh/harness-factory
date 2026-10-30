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
                 launcher: Launcher | None = None) -> None:
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
                client = await self._stack.enter_async_context(MCPClient(spec, self.env, self.launcher))
                discovered += await client.list_tools()
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

    def transports(self) -> dict[str, str]:
        return {sid: c.transport.value for sid, c in self.clients.items()}

    async def call(self, tool_ref: str, args: dict[str, Any]) -> ToolResult:
        server, _, name = tool_ref.partition(".")
        client = self.clients.get(server)
        if client is None:
            raise HarnessError(ErrorKind.TOOL_NOT_ALLOWED, f"no session for server {server!r}")
        return await client.call_tool(name, args)
