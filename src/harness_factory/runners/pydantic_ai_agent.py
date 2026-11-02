"""`pydantic-ai` runner: Pydantic AI's `Agent` (docs/adr/0016-runner-axis.md, #56).

Built from the framework's own components, at their defaults:
- the agent: `pydantic_ai.Agent` (pydantic-ai-slim 2.64, on pydantic-graph), run with `Agent.run`: text output, one
  retry per tool and for output (`retries=1`), the default `UsageLimits` (`request_limit=50`), parallel tool calls
  allowed, no history processors, no capabilities. The recipe's system message is its `instructions` (the idiom the
  Pydantic AI docs recommend over `system_prompt`), the task message the run's user prompt;
- the MCP tools: `pydantic_ai.mcp.MCPToolset`, Pydantic AI's MCP toolset. It builds a `ToolDefinition` per MCP tool
  (the server's name, description and input schema, unchanged), validates the model's arguments against that schema
  before calling, maps results (`_map_mcp_call_tool_result`: text, or the structured content when every part is
  text) and turns an error result into `ModelRetry` (the default `tool_error_behavior="retry"`);
- the model client: `GatewayModel`, a `pydantic_ai.models.Model` (measurement point a). Its profile (which tells the
  framework how the model's API behaves: tool-schema transformer, whether system prompts may appear mid-conversation)
  is the one Pydantic AI's own `LiteLLMProvider.model_profile` gives the LiteLLM model string the gateway sends
  (`<litellm_prefix>/<provider_model_id>`), as a team calling the same model through LiteLLM with Pydantic AI gets.

The toolset's seam. `MCPToolset` normally wraps a FastMCP `Client`, built from a transport, and has no session
parameter. The runner builds the toolset with a transport that never connects, then gives it, as its `client`,
`ExecutorClient`: the part of the FastMCP client surface the toolset's own code uses (async context manager,
`initialize_result`/`server_capabilities`, `list_tools()`, `call_tool(name=, arguments=, meta=, raise_on_error=)`),
over `ExecutorSession` (measurement point b). Replacing `client` is what the toolset itself does for a per-run copy
(`MCPToolset.for_run`). Everything the toolset does with the tools and their results stays its own; the FastMCP
client's protocol code (handshake, protocol-era negotiation, result validation) isn't run, as there is no server.

`GatewayModel` converts messages the way Pydantic AI's `OpenAIChatModel` does for Chat Completions (its
`_map_messages`, `_map_user_message`, `_map_tool_definition`, `_MapModelResponseContext`), for the parts a text agent
with function tools produces: instructions as system messages after any leading system message, system and user
prompt parts, tool returns via `ToolReturnPart.model_response_str_and_user_content` (retries reach the adapter
already translated by the agent's `Model.prepare_messages`), assistant texts joined with blank lines and tool calls
with `args_as_json_str`. Any other part (files, thinking replay, native tools) raises, so a run that needs it is an
ERROR, not a silent mismatch. Pydantic AI's `OpenAIChatModel` itself needs `openai>=3.31`, which the factory's
LiteLLM (`openai<3`) excludes, so it is mirrored, not subclassed.

What the framework adds to the conversation:
- prompt text: none from the agent. Where the profile doesn't say the API takes system prompts mid-conversation
  (for `groq/...` names: the Groq family profile leaves it unset), `Model.prepare_messages` adds one system message
  before the instructions explaining its `<system>` tags (about 60 tokens per call);
- tool schemas: as the profile's JSON schema transformer (`OpenAIJsonSchemaTransformer`) rewrites them;
- a tool error, a policy refusal or invalid arguments come back as a retry prompt: the error text, then "Fix the
  errors and try again.", in a tool message; a second failure of the same tool, or a second unusable final answer,
  ends the run (`retries=1`), recorded as `budget:framework max_retries`; the request limit as
  `budget:framework request_limit`. Arguments that fail the schema are refused by the toolset before a call, so
  they never reach ToolExecutor (no tool_call span), like LangChain's invalid tool calls (ADR 0016, "Limits").
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import AsyncGenerator
from typing import Any

from pydantic_ai import exceptions as pai_exceptions
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models import Model, ModelRequestParameters
from pydantic_ai.settings import ModelSettings
from pydantic_ai.tools import ToolDefinition

from harness_factory.runners.base import ExecutorSession, RunnerContext, RunnerStop
from harness_factory.shapes.base import HarnessInstance
from harness_factory.shapes.result import RunResult

TOOLSET_ID = "harness-factory"       # the toolset's id (Pydantic AI derives one from a URL; there is none)
# The framework's own messages when a tool, or the output, failed more often than `retries` allows.
RETRY_CAP_MARKS = ("exceeded max retries count", "Exceeded maximum output retries")


# ------------------------------------------------------------------ measurement point (a): the model client

def tool_param(t: ToolDefinition) -> dict[str, Any]:
    """`OpenAIChatModel._map_tool_definition`, for a profile without strict tool definitions."""
    return {"type": "function",
            "function": {"name": t.name, "description": t.description or "", "parameters": t.parameters_json_schema}}


def user_text(part: UserPromptPart) -> str:
    if isinstance(part.content, str):
        return part.content
    if all(isinstance(c, str) for c in part.content):
        return "\n".join(part.content)            # the run's user prompt is text; files are not sent by the factory
    raise NotImplementedError("non-text user content: the gateway sends text messages only")


def to_wire(messages: list[ModelMessage], instructions: list[str]) -> list[dict[str, Any]]:
    """Pydantic AI messages as Chat Completions messages, as `OpenAIChatModel._map_messages` maps them."""
    out: list[dict[str, Any]] = []
    for message in messages:
        if isinstance(message, ModelRequest):
            for part in message.parts:
                if isinstance(part, SystemPromptPart):
                    out.append({"role": "system", "content": part.content})
                elif isinstance(part, UserPromptPart):
                    out.append({"role": "user", "content": user_text(part)})
                elif isinstance(part, ToolReturnPart):
                    text, files = part.model_response_str_and_user_content()
                    if files:
                        raise NotImplementedError("file content in a tool result")
                    out.append({"role": "tool", "tool_call_id": part.tool_call_id, "content": text})
                else:
                    raise NotImplementedError(f"request part {type(part).__name__}")
        elif isinstance(message, ModelResponse):
            texts, calls = [], []
            for part in message.parts:
                if isinstance(part, TextPart):
                    texts.append(part.content)
                elif isinstance(part, ToolCallPart):
                    calls.append({"id": part.tool_call_id, "type": "function",
                                  "function": {"name": part.tool_name, "arguments": part.args_as_json_str()}})
                else:
                    raise NotImplementedError(f"response part {type(part).__name__}")
            if texts or calls:                     # an empty response is not sent back (as OpenAIChatModel)
                msg: dict[str, Any] = {"role": "assistant", "content": "\n\n".join(texts) if texts else None}
                if calls:
                    msg["tool_calls"] = calls
                out.append(msg)
    lead = next((i for i, m in enumerate(out) if m["role"] != "system"), len(out))
    out[lead:lead] = [{"role": "system", "content": text} for text in instructions]
    return out


def from_wire(msg: dict[str, Any], model_name: str) -> ModelResponse:
    """The gateway's assistant message as a ModelResponse, as `OpenAIChatModel._process_response` builds one: the text,
    then one ToolCallPart per call with the provider's arguments string and id."""
    parts: list[Any] = []
    if msg.get("content"):
        parts.append(TextPart(content=msg["content"]))
    for call in msg.get("tool_calls") or []:
        fn = call.get("function") or {}
        args = fn.get("arguments")
        parts.append(ToolCallPart(tool_name=fn.get("name") or "", tool_call_id=call.get("id") or "",
                                  args=args if isinstance(args, str | dict) or args is None else json.dumps(args)))
    return ModelResponse(parts=parts, model_name=model_name)


class GatewayModel(Model):
    """A Pydantic AI model whose every request goes through the run's ModelGateway (via RunnerContext.model)."""

    def __init__(self, ctx: RunnerContext, model_name: str, profile: Any = None) -> None:
        super().__init__(profile=profile)
        self._ctx = ctx
        self._model_name = model_name

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def system(self) -> str:
        return "harness-factory"

    async def request(self, messages: list[ModelMessage], model_settings: ModelSettings | None,
                      model_request_parameters: ModelRequestParameters) -> ModelResponse:
        model_settings, params = self.prepare_request(model_settings, model_request_parameters)
        instructions = [p.content for p in self._get_instruction_parts(messages, params) or []]
        tools = [tool_param(t) for t in [*params.function_tools, *params.output_tools]]
        reply = await self._ctx.model(to_wire(messages, instructions), tools)
        return from_wire(reply, self._model_name)


# ------------------------------------------------------------------ measurement point (b): the tool callables

class ExecutorClient:
    """The FastMCP client surface `MCPToolset` uses, answered by ExecutorSession (see the module docstring)."""

    initialize_result = None             # read as a session without the legacy handshake: metadata from below
    server_info = None                   # no single server: the tools come from several
    instructions = None

    def __init__(self, session: ExecutorSession) -> None:
        from mcp.types import ServerCapabilities, ToolsCapability
        self._session = session
        self.server_capabilities = ServerCapabilities(tools=ToolsCapability())

    async def __aenter__(self) -> ExecutorClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def list_tools(self) -> list:
        return list((await self._session.list_tools()).tools)

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None, *, meta: Any = None,
                        raise_on_error: bool = True, **_: Any):
        return await self._session.call_tool(name, arguments)


def build_toolset(ctx: RunnerContext):
    from fastmcp.client.transports import ClientTransport
    from pydantic_ai.mcp import MCPToolset

    class NoTransport(ClientTransport):
        """Never connects: the toolset's client is replaced before use."""

        @contextlib.asynccontextmanager
        async def connect_session(self, **_: Any) -> AsyncGenerator[Any, None]:
            raise RuntimeError("tools run through ToolExecutor, not an MCP connection")
            yield

    toolset = MCPToolset(NoTransport(), id=TOOLSET_ID)
    toolset.client = ExecutorClient(ExecutorSession(ctx))  # type: ignore[assignment]
    return toolset


def litellm_model(inst: HarnessInstance, canonical: str) -> str:
    """The model string the gateway sends to LiteLLM for this run (`substrate/gateway.py`)."""
    provider = inst.gateway.provider
    served = inst.gateway.catalog.served(provider.id, canonical)
    return f"{provider.litellm_prefix}/{served.provider_model_id}" if provider.litellm_prefix \
        else served.provider_model_id


def build_agent(ctx: RunnerContext, system: str):
    """The framework's agent at its defaults: the gateway model, its MCP toolset, the recipe's system prompt."""
    import pydantic_ai
    from pydantic_ai import Agent
    from pydantic_ai.providers.litellm import LiteLLMProvider

    pydantic_ai.BANNER_ENABLED = False       # its first-run console banner; the factory owns its output
    canonical = ctx.slot.canonical_model or ""
    model = GatewayModel(ctx, canonical, profile=LiteLLMProvider.model_profile(litellm_model(ctx.inst, canonical)))
    return Agent(model, instructions=system, toolsets=[build_toolset(ctx)])


class PydanticAIRunner:
    runtime = "pydantic-ai"

    async def run(self, inst: HarnessInstance) -> RunResult:
        ctx = RunnerContext(inst, inst.recipe.model_slots["executor"])
        system, task = ctx.system_and_task()
        agent = build_agent(ctx, system)
        inst.snapshot["runner"] = {"runtime": self.runtime, "agent": type(agent).__name__,
                                   "toolsets": [type(t).__name__ for t in agent.toolsets]}
        try:
            result = await agent.run(task)
        except RunnerStop:
            return ctx.finish()
        except pai_exceptions.UsageLimitExceeded as e:     # the framework's own request limit: a harness stop
            if ctx.stop is None:
                ctx.halt("budget:framework request_limit", str(e)[:300])
            return ctx.finish()
        except pai_exceptions.UnexpectedModelBehavior as e:
            if ctx.stop is None and any(m in str(e) for m in RETRY_CAP_MARKS):   # the framework's retry caps
                ctx.halt("budget:framework max_retries", str(e)[:300])
            if ctx.stop is not None:
                return ctx.finish()
            raise
        except Exception:
            if ctx.stop is not None:          # the framework wrapped our stop: the first stop recorded decides
                return ctx.finish()
            raise
        if ctx.stop is not None:
            return ctx.finish()
        return ctx.finish(str(result.output))
