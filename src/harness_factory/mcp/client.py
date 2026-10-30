"""MCPClient, CapabilitySet, Transport (B11, harness side): one MCP session to one server."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Callable
from contextlib import AsyncExitStack
from enum import Enum
from typing import Any

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from pydantic import BaseModel

from harness_factory.env.mcp.servers import MCPServerSpec

# What a tool-server process may inherit from the factory's environment. Everything else (API keys above all)
# stays out: a tool server only needs to start Python (or the docker CLI) and find its own files.
SAFE_ENV_KEYS = ("PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR", "TEMP", "TMP", "SYSTEMROOT",
                 "PYTHONPATH", "VIRTUAL_ENV", "SSL_CERT_FILE", "DOCKER_HOST", "DOCKER_CONFIG", "DOCKER_CONTEXT",
                 "DOCKER_CERT_PATH", "DOCKER_TLS_VERIFY", "XDG_RUNTIME_DIR")


def process_env(extra: dict[str, str]) -> dict[str, str]:
    return {**{k: v for k, v in os.environ.items() if k in SAFE_ENV_KEYS}, **extra}


Launcher = Callable[[MCPServerSpec], tuple[str, list[str], dict[str, str]]]


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

    def __init__(self, spec: MCPServerSpec, env: dict[str, str], launcher: Launcher | None = None) -> None:
        self.spec = spec
        self.env = env
        self.launcher = launcher
        self.transport = Transport.STDIO
        self.protocol_version: str | None = None
        self.capabilities = CapabilitySet()
        self._stack = AsyncExitStack()
        self._session: ClientSession | None = None
        self.close_error: str | None = None
        self._owner: asyncio.Task | None = None
        self._closing: asyncio.Event | None = None

    async def __aenter__(self) -> MCPClient:
        # Python never calls __aexit__ for a failed __aenter__: if anything below fails or is cancelled (Ctrl-C
        # mid-campaign), close what was already opened here, or the server process outlives the run.
        try:
            return await self._open()
        except BaseException:
            if self._owner is not None:                     # a remote session being opened: stop its owner too
                self._owner.cancel()
                await asyncio.gather(self._owner, return_exceptions=True)
            await self._stack.aclose()
            raise

    async def _open(self) -> MCPClient:
        if self.spec.remote:
            init = await self._open_remote()
        else:
            if self.launcher is not None:                   # the sandbox decides how the server process starts
                cmd, args, extra = self.launcher(self.spec)
            else:
                (cmd, args), extra = self.spec.command(), self.env
            params = StdioServerParameters(command=cmd, args=args, env=process_env(extra))
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

    async def _open_remote(self):
        """A remote server (EXT-05): connect from the factory process over streamable HTTP. The token comes from
        the environment variable the catalog names (EXT-13); it goes into this client's headers only, never into a
        trace, a transcript, or a sandbox.

        The session lives in its own owner task. The HTTP transport runs a task group; if the server goes away,
        that group cancels the task that entered it. Owned here, that is only the owner: the run's pending call
        then fails with "connection closed", which the tool executor records as the environment failing."""
        import httpx2
        from mcp.client.streamable_http import streamable_http_client

        from harness_factory.substrate.errors import ErrorKind, HarnessError

        headers = {}
        if self.spec.auth_env:
            token = os.environ.get(self.spec.auth_env)
            if not token:
                raise HarnessError(ErrorKind.PROVISION, f"remote MCP server {self.spec.id!r} needs "
                                                        f"{self.spec.auth_env} in .env or the environment")
            headers["Authorization"] = f"Bearer {token}"
        self.transport = Transport.STREAMABLE_HTTP
        ready, self._closing = asyncio.Event(), asyncio.Event()
        box: dict[str, Any] = {}

        async def owner() -> None:
            try:
                async with AsyncExitStack() as stack:
                    http = await stack.enter_async_context(httpx2.AsyncClient(headers=headers, timeout=30))
                    read, write = await stack.enter_async_context(streamable_http_client(self.spec.url, http_client=http))
                    session = await stack.enter_async_context(ClientSession(read, write))
                    box["init"] = await session.initialize()
                    self._session = session
                    ready.set()
                    await self._closing.wait()
            except BaseException as e:                      # noqa: BLE001  (reported to whoever waits on it)
                box.setdefault("error", e)
                if not isinstance(e, Exception):
                    box["error"] = RuntimeError(f"remote session ended: {type(e).__name__}")
            finally:
                ready.set()

        self._owner = asyncio.create_task(owner())
        await ready.wait()
        if "init" not in box:
            await asyncio.gather(self._owner, return_exceptions=True)
            raise box.get("error") or RuntimeError("remote session did not start")
        return box["init"]

    async def __aexit__(self, *exc) -> None:
        if self._owner is None:
            await self._stack.aclose()
            return
        # A remote server that went away can't take the session-end request; nothing of ours is left open, and the
        # run's result does not depend on it.
        self._closing.set()
        (res,) = await asyncio.gather(self._owner, return_exceptions=True)
        if isinstance(res, BaseException):
            self.close_error = f"{type(res).__name__}: {str(res)[:200]}"

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
