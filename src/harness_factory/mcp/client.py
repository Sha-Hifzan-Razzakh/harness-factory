"""MCPClient, CapabilitySet, Transport (B11, harness side): one MCP session to one server."""

from __future__ import annotations

import asyncio
import contextlib
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
    STREAMABLE_HTTP = "STREAMABLE_HTTP"  # remote server, called live (EXT-05)
    REPLAY = "REPLAY"                    # remote server answered from its recording (EXT-08)


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
    infra: str | None = None             # set when the environment failed, not the model (e.g. replay_miss)
    meta: dict[str, Any] | None = None   # the result's `_meta` (an agent tool reports its usage there, EXT-10)


class MCPClient:
    """Async context manager. Elicitation and sampling are refused in P0 (MCP-06, MCP-07 arrive in P3)."""

    def __init__(self, spec: MCPServerSpec, env: dict[str, str], launcher: Launcher | None = None,
                 tenant: str | None = None) -> None:
        """tenant: this run's test tenant id, sent as the spec's tenant header (EXT-07)."""
        self.spec = spec
        self.tenant = tenant
        self._seen: dict[str, int] = {}      # recording key (n=0) -> calls made so far in this session
        self._writes: list[str] = []         # recording keys of the calls that changed state, in order
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
        self.mode = "live"
        self.recording = None                # mcp.recording.Recording, for remote servers in record/replay mode

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
            from harness_factory import settings
            from harness_factory.mcp.recording import Recording
            self.mode = settings.remote_mode()
            if self.mode != "live":
                self.recording = Recording(settings.remote_recordings(), self.spec)
            if self.mode == "replay":
                return self._open_replay()
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

    def _open_replay(self) -> MCPClient:
        """No network, no token: tools and answers come from the recording of this server version."""
        from harness_factory.substrate.errors import ErrorKind, HarnessError
        if not self.recording.exists():
            raise HarnessError(ErrorKind.PROVISION, f"no recording of remote MCP server {self.spec.id!r} "
                                                    f"({self.spec.url}@{self.spec.version}) in {self.recording.dir}: "
                                                    f"record one with HF_REMOTE_MODE=record")
        self.transport = Transport.REPLAY
        self.capabilities = CapabilitySet(tools=True)
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
        if self.spec.tenant is not None and self.tenant:
            headers[self.spec.tenant.header] = self.tenant
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
        if self.mode == "replay":
            return [DiscoveredTool(server=self.spec.id, **t) for t in self.recording.load_tools()]
        assert self._session is not None
        res = await self._session.list_tools()
        admin = self.spec.tenant.admin_tools() if self.spec.tenant else set()   # the factory's, never the model's
        tools = [DiscoveredTool(server=self.spec.id, name=t.name, description=t.description or "",
                                input_schema=t.input_schema) for t in res.tools if t.name not in admin]
        if self.mode == "record":
            self.recording.save_tools([t.model_dump(exclude={"server"}) for t in tools])
        return tools

    def _repeat(self, name: str, args: dict[str, Any]) -> int:
        """How many identical calls this session made before this one (recordings key repeats apart)."""
        from harness_factory.mcp.recording import Recording
        k = Recording.key(name, args)
        n = self._seen.get(k, 0)
        self._seen[k] = n + 1
        return n

    def _changes_state(self, name: str) -> bool:
        from harness_factory.env.mcp.servers import SideEffect
        return any(t.name == name and t.side_effect != SideEffect.READ for t in self.spec.tools)

    async def call_tool(self, name: str, args: dict[str, Any]) -> ToolResult:
        n = self._repeat(name, args) if self.recording is not None else 0
        if self.recording is not None and self._changes_state(name):
            self._writes.append(self.recording.key(name, args, n))
        if self.mode == "replay":
            saved = self.recording.load_call(name, args, n)
            if saved is None and n and not self._changes_state(name):
                saved = self.recording.load_call(name, args)     # a READ answer asked again: same answer
            if saved is None:                               # the run asked something the live run never did
                return ToolResult(text=f"{self.spec.id}.{name} is unavailable right now (not in the recording).",
                                  is_error=True, infra="replay_miss")
            return ToolResult(text=saved["text"], is_error=saved["is_error"], structured=saved.get("structured"),
                              meta=saved.get("meta"))
        result = await self._live_call(name, args)
        if self.mode == "record":
            self.recording.save_call(name, args, result.model_dump(exclude={"infra"}), n)
        return result

    async def _live_call(self, name: str, args: dict[str, Any]) -> ToolResult:
        assert self._session is not None
        res = await self._session.call_tool(name, args)
        texts = [c.text for c in res.content if getattr(c, "type", "") == "text"]
        text = "\n".join(texts)
        if not text and res.structured_content is not None:
            text = json.dumps(res.structured_content)
        return ToolResult(text=text, is_error=bool(res.is_error), structured=res.structured_content,
                          meta=dict(res.meta) if res.meta else None)

    # ------------------------------------------------------------------ test tenant (EXT-07): the factory's calls

    async def tenant_reset(self, state: Any) -> ToolResult:
        """Put this run's tenant in the task's seed state. Recorded and replayed like any call."""
        assert self.spec.tenant is not None
        return await self.call_tool(self.spec.tenant.reset, {"state": state})

    async def tenant_export(self) -> tuple[Any, str | None]:
        """The tenant's state after the run, or (None, reason). In replay, the recorded end state counts only when
        this run changed state with exactly the calls the recorded run made (else `replay_diverged`)."""
        assert self.spec.tenant is not None
        name = self.spec.tenant.export
        if self.mode == "replay":
            saved = self.recording.load_call(name, {})
            if saved is None:
                return None, "replay_miss"
            if saved.get("writes", []) != self._writes:
                return None, "replay_diverged"
            result = ToolResult(text=saved["text"], is_error=saved["is_error"], structured=saved.get("structured"))
        else:
            result = await self._live_call(name, {})
            if self.mode == "record":
                self.recording.save_call(name, {}, {**result.model_dump(exclude={"infra", "meta"}),
                                                    "writes": list(self._writes)})
        if result.is_error:
            return None, f"export failed: {result.text[:200]}"
        if isinstance(result.structured, dict) and set(result.structured) != {"result"}:
            return result.structured, None
        try:
            return json.loads(result.text), None
        except ValueError:
            return None, f"export is not JSON: {result.text[:120]!r}"

    async def tenant_drop(self) -> None:
        """Best effort: the server may forget this run's tenant. Never recorded; nothing to do in replay."""
        if self.spec.tenant is None or not self.spec.tenant.drop or self.mode == "replay" or self._session is None:
            return
        with contextlib.suppress(Exception):
            await asyncio.wait_for(self._session.call_tool(self.spec.tenant.drop, {}), 10)
