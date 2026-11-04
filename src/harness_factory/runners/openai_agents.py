"""`openai-agents` runner: the OpenAI Agents SDK's `Agent` run by its `Runner` (docs/adr/0016-runner-axis.md, #56).

Built from the framework's own components, at their defaults:
- the agent: `agents.Agent` (openai-agents 0.20.0) with the recipe's system message as its `instructions`, run with
  `agents.Runner.run`: `max_turns=10` (`DEFAULT_MAX_TURNS`), text output, no handoffs, guardrails or sessions, no model
  retries (`ModelSettings.retry` unset), `tool_use_behavior="run_llm_again"`, `reset_tool_choice=True`;
- the MCP tools: the SDK's `MCPServer` interface (`agents.mcp.MCPServer`, the abstract base of its stdio, SSE and
  Streamable HTTP servers). `ExecutorMCPServer` implements it over `ExecutorSession` (measurement point b): no
  connection to open, `list_tools` and `call_tool` answered by the session. The SDK's `MCPUtil` builds the
  `FunctionTool`s from it (the server's names, descriptions and schemas; `MCPConfig` defaults: no strict schema
  conversion, no server prefix in names), invokes them, turns results into tool output (text blocks; an error result
  is sent as its text, unmarked) and turns exceptions into "An error occurred while running the tool. Please try
  again. Error: ..." (`default_tool_error_function`);
- the model client: `GatewayModel`, an `agents.models.interface.Model` (measurement point a). It converts requests and
  replies with the SDK's own Chat Completions `Converter` (`agents.models.chatcmpl_converter`) the way the SDK's
  `OpenAIChatCompletionsModel` does: `items_to_messages`, the instructions as the first system message,
  `tool_to_openai` / `convert_handoff_tool`, and `message_to_output_items` for the reply.

One default is changed: tracing. The SDK exports a trace of every run to OpenAI's tracing backend by default. That
would send run data outside the sandbox, so each run is given `RunConfig(tracing_disabled=True)` (per run, not the
process-wide `set_tracing_disabled`). Tracing changes nothing the model sees.

What the framework adds to the conversation: no prompt text; tool schemas with `properties: {}` added where an MCP
schema has none, and `strict: false` on every tool; the error message above for a tool exception (a refused or
failed call through ToolExecutor is an error *result*, so its text reaches the model unchanged); the turn limit,
recorded as `budget:framework max_turns`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterable, Mapping
from typing import Any

from agents.exceptions import MaxTurnsExceeded
from agents.items import ModelResponse
from agents.mcp import MCPServer
from agents.models.chatcmpl_converter import Converter
from agents.models.interface import Model
from agents.usage import Usage

from harness_factory.runners.base import ExecutorSession, RunnerContext, RunnerStop
from harness_factory.shapes.base import HarnessInstance
from harness_factory.shapes.result import RunResult


def plain(value: Any) -> Any:
    """Converter output as plain JSON values (the SDK's `_to_dump_compatible` does the same before sending)."""
    if isinstance(value, Mapping):
        return {k: plain(v) for k, v in value.items()}
    if isinstance(value, Iterable) and not isinstance(value, str | bytes | bytearray):
        return [plain(v) for v in value]
    return value


class GatewayModel(Model):
    """An Agents SDK model whose every call goes through the run's ModelGateway (via RunnerContext.model)."""

    def __init__(self, ctx: RunnerContext, model_name: str) -> None:
        self._ctx = ctx
        self.model = model_name

    async def get_response(self, system_instructions, input, model_settings, tools, output_schema, handoffs,
                           tracing, *, previous_response_id=None, conversation_id=None, prompt=None) -> ModelResponse:
        from openai.types.chat import ChatCompletionMessage

        messages = Converter.items_to_messages(input, model=self.model)
        if system_instructions:
            messages.insert(0, {"content": system_instructions, "role": "system"})
        wire_tools = [Converter.tool_to_openai(t) for t in tools] + [Converter.convert_handoff_tool(h)
                                                                     for h in handoffs]
        reply = await self._ctx.model(plain(messages), plain(wire_tools))
        message = ChatCompletionMessage.model_validate({"role": "assistant", "content": reply.get("content"),
                                                        "tool_calls": reply.get("tool_calls") or None})
        items = Converter.message_to_output_items(message, provider_data={"model": self.model})
        return ModelResponse(output=items, usage=Usage(requests=1), response_id=None)

    def stream_response(self, *args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        raise NotImplementedError("runner runs use Runner.run, which asks for whole responses")


class ExecutorMCPServer(MCPServer):
    """The SDK's MCP server interface over ExecutorSession: the tools the native runtime offers, run by ToolExecutor."""

    def __init__(self, session: ExecutorSession) -> None:
        super().__init__()
        self._session = session

    @property
    def name(self) -> str:
        return "harness-factory"

    async def connect(self) -> None:
        return None

    async def cleanup(self) -> None:
        return None

    async def list_tools(self, run_context=None, agent=None) -> list:
        return list((await self._session.list_tools()).tools)

    async def call_tool(self, tool_name: str, arguments: dict[str, Any] | None, meta: dict[str, Any] | None = None):
        return await self._session.call_tool(tool_name, arguments)

    async def list_prompts(self):
        from mcp.types import ListPromptsResult
        return ListPromptsResult(prompts=[])

    async def get_prompt(self, name: str, arguments: dict[str, Any] | None = None):
        raise KeyError(f"no prompt {name!r}: the factory's servers offer tools only")


def build_agent(ctx: RunnerContext, system: str):
    """The framework's agent at its defaults: the gateway model, the MCP server, the recipe's system prompt."""
    from agents import Agent
    return Agent(name="harness-factory", instructions=system, model=GatewayModel(ctx, ctx.slot.canonical_model or ""),
                 mcp_servers=[ExecutorMCPServer(ExecutorSession(ctx))])


class OpenAIAgentsRunner:
    runtime = "openai-agents"

    async def run(self, inst: HarnessInstance) -> RunResult:
        from agents import RunConfig, Runner

        ctx = RunnerContext(inst, inst.recipe.model_slots["executor"])
        system, task = ctx.system_and_task()
        agent = build_agent(ctx, system)
        inst.snapshot["runner"] = {"runtime": self.runtime, "agent": type(agent).__name__,
                                   "mcp_servers": [type(s).__name__ for s in agent.mcp_servers]}
        try:
            result = await Runner.run(agent, task, run_config=RunConfig(tracing_disabled=True))
        except RunnerStop:
            return ctx.finish()
        except MaxTurnsExceeded as e:        # the framework's own turn limit: a harness stop
            if ctx.stop is None:
                ctx.halt("budget:framework max_turns", str(e)[:300])
            return ctx.finish()
        except Exception:
            if ctx.stop is not None:          # the framework wrapped our stop: the first stop recorded decides
                return ctx.finish()
            raise
        if ctx.stop is not None:
            return ctx.finish()
        return ctx.finish(str(result.final_output))
