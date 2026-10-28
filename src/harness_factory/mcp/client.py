"""MCPClient, CapabilitySet, Transport (B11, harness side): one MCP session to one server."""

from __future__ import annotations

import json
from contextlib import AsyncExitStack
from enum import Enum
from typing import Any

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from pydantic import BaseModel

from harness_factory.env.mcp.servers import MCPServerSpec


class Transport(str, Enum):
    STDIO = "STDIO"                      # server inside the sandbox
    STREAMABLE_HTTP = "STREAMABLE_HTTP"  # shared server on the internal network (P1)


class CapabilitySet(BaseModel):
    tools: bool = False
    resources: bool = False
    prompts: bool = False                # always off in campaigns: prompts come from the registry
    sampling: bool = False
    elicitation: bool = False
    list_changed: bool = False


class DiscoveredTool(BaseModel):
    server: str
    name: str
    description: str = ""
    input_schema: dict[str, Any]

    @property
    def ref(self) -> str:
        return f"{self.server}.{self.name}"


class ToolResult(BaseModel):
    text: str
    is_error: bool = False
    structured: Any = None


class MCPClient:
    """Async context manager. Elicitation and sampling are refused in P0 (MCP-06, MCP-07 arrive in P3)."""

    def __init__(self, spec: MCPServerSpec, env: dict[str, str]) -> None:
        self.spec = spec
        self.env = env
        self.transport = Transport.STDIO
        self.protocol_version: str | None = None
        self.capabilities = CapabilitySet()
        self._stack = AsyncExitStack()
        self._session: ClientSession | None = None

    async def __aenter__(self) -> MCPClient:
        cmd, args = self.spec.command()
        import os
        params = StdioServerParameters(command=cmd, args=args, env={**os.environ, **self.env})
        read, write = await self._stack.enter_async_context(stdio_client(params))
        self._session = await self._stack.enter_async_context(ClientSession(read, write))
        init = await self._session.initialize()
        self.protocol_version = str(init.protocol_version)
        caps = init.capabilities
        self.capabilities = CapabilitySet(
            tools=caps.tools is not None,
            resources=caps.resources is not None,
            prompts=False,
            list_changed=bool(caps.tools and getattr(caps.tools, "list_changed", False)),
        )
        return self

    async def __aexit__(self, *exc) -> None:
        await self._stack.aclose()

    async def list_tools(self) -> list[DiscoveredTool]:
        assert self._session is not None
        res = await self._session.list_tools()
        return [DiscoveredTool(server=self.spec.id, name=t.name, description=t.description or "",
                               input_schema=t.input_schema) for t in res.tools]

    async def call_tool(self, name: str, args: dict[str, Any]) -> ToolResult:
        assert self._session is not None
        res = await self._session.call_tool(name, args)
        texts = [c.text for c in res.content if getattr(c, "type", "") == "text"]
        text = "\n".join(texts)
        if not text and res.structured_content is not None:
            text = json.dumps(res.structured_content)
        return ToolResult(text=text, is_error=bool(res.is_error), structured=res.structured_content)
