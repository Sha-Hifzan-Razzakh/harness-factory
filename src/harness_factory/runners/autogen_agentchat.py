"""`autogen-agentchat` runner: AutoGen AgentChat's own `AssistantAgent` (docs/adr/0016-runner-axis.md, RUN-11).

AutoGen has been in maintenance mode (fixes only) since October 2025; Microsoft Agent Framework is its successor. This
runner is for teams with AutoGen deployments and for the migration check against `ms-agent-framework`, not a
default pick.

Built from the framework's own components (autogen-agentchat / autogen-core 0.7.5):
- the agent: `autogen_agentchat.agents.AssistantAgent` with the recipe's system prompt as `system_message` (which
  replaces AutoGen's default one) and nothing else set: no model context (so `UnboundedChatCompletionContext`), no
  memory, no handoffs, `max_tool_iterations` 1, `reflect_on_tool_use` off, no streaming. Each turn it makes one
  model call, runs the calls of the reply concurrently through its `StaticStreamWorkbench`, and answers with a
  `ToolCallSummaryMessage`, or with a `TextMessage` when the reply has no tool calls;
- the loop: `RoundRobinGroupChat([agent], termination_condition=TextMessageTermination(agent))`, the single-agent
  team AutoGen documents for running an agent until it answers in text (the McpWorkbench docstring's Playwright
  example), with no `max_turns`. So the framework has no turn cap of its own: the recipe's budget ends a run that
  never answers, as for native;
- the tools: one `ExecutorTool` (an autogen-core `BaseTool`) per tool `ExecutorSession.list_tools` announces. Its
  argument model is built by autogen-core's own `schema_to_pydantic_model` from the MCP input schema, as autogen-ext's
  `McpToolAdapter` builds it, and its `run` sends `args.model_dump(exclude_unset=True)` to `ExecutorSession.call_tool`
  (ToolExecutor), as that adapter sends them to its MCP session. autogen-ext's MCP tools themselves can't be used:
  autogen-ext 0.7.5 (the last release) imports `mcp.shared.context.RequestContext` and reads `Tool.inputSchema` /
  `CallToolResult.isError`, all gone or renamed in MCP 2.x, so `autogen_ext.tools.mcp` doesn't import next to the
  factory's MCP 2.3. Argument validation, invocation, error capture and result text are AutoGen's
  (`BaseTool.run_json`, `StaticStreamWorkbench`, `AssistantAgent._execute_tool_call`);
- the model client: `GatewayChatCompletionClient`, an autogen-core `ChatCompletionClient` (measurement point a).
  Requests are converted with autogen-ext's own OpenAI converters (`to_oai_type`, `convert_tools`) at the defaults of
  its `OpenAIChatCompletionClient`, and replies are parsed the way that client parses a chat completion. The OpenAI
  client itself isn't used: it posts through the OpenAI SDK.

What the framework adds to the conversation: no prompt text. The recipe's system message is sent first, unchanged,
on every call; the task is the one user message. The OpenAI conversion adds `"name": "user"` to that message (and
the agent's name to its text replies, which end the run), and `strict: false` to every tool. The framework decides
the tool schemas (pydantic's JSON schema of the generated argument model), how results and errors read, what happens
to invalid calls, and when the loop ends (a text reply).
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator, Mapping, Sequence
from typing import Any, Literal

from autogen_agentchat.agents import AssistantAgent
from autogen_agentchat.conditions import TextMessageTermination
from autogen_agentchat.messages import TextMessage
from autogen_agentchat.teams import RoundRobinGroupChat
from autogen_core import CancellationToken, FunctionCall
from autogen_core.models import (
    ChatCompletionClient,
    CreateResult,
    LLMMessage,
    ModelCapabilities,
    ModelFamily,
    ModelInfo,
    RequestUsage,
    SystemMessage,
)
from autogen_core.tools import BaseTool, Tool, ToolSchema
from autogen_core.utils import schema_to_pydantic_model
from autogen_ext.models.openai._openai_client import convert_tools, normalize_name, to_oai_type
from pydantic import BaseModel

from harness_factory.runners.base import ExecutorSession, RunnerContext, RunnerStop
from harness_factory.shapes.base import HarnessInstance
from harness_factory.shapes.result import RunResult

AGENT_NAME = "assistant"          # AssistantAgent requires a name; AutoGen's examples use this one

# What the client declares to AssistantAgent: a tool-calling text model. Nothing here is sent to the provider.
MODEL_INFO = ModelInfo(vision=False, function_calling=True, json_output=False, family=ModelFamily.UNKNOWN,
                       structured_output=False, multiple_system_messages=False)


# ------------------------------------------------------------------ measurement point (a): the model client

def merge_system_messages(messages: Sequence[LLMMessage]) -> list[LLMMessage]:
    """`BaseOpenAIChatCompletionClient._process_create_args` for a model without `multiple_system_messages`:
    consecutive system messages become one, at the first one's position (AssistantAgent sends one)."""
    system = [m for m in messages if isinstance(m, SystemMessage)]
    if len(system) <= 1:
        return list(messages)
    idx = [i for i, m in enumerate(messages) if isinstance(m, SystemMessage)]
    if idx != list(range(idx[0], idx[0] + len(idx))):
        raise ValueError("Multiple and Not continuous system messages are not supported")
    merged = SystemMessage(content="\n".join(m.content for m in system).rstrip())
    rest = [m for m in messages if not isinstance(m, SystemMessage)]
    return rest[:idx[0]] + [merged] + rest[idx[0]:]


def to_wire(messages: Sequence[LLMMessage], tools: Sequence[Tool | ToolSchema], model: str
            ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """AutoGen messages and tools as the OpenAI Chat Completions request `OpenAIChatCompletionClient` builds, with
    its converters at its defaults (no name prefixes, names included in messages)."""
    wire = [dict(item) for m in merge_system_messages(messages)
            for item in to_oai_type(m, prepend_name=False, model=model or "unknown", model_family=MODEL_INFO["family"],
                                    include_name_in_message=True)]
    return wire, [dict(t) for t in convert_tools(tools)]


def from_wire(msg: dict[str, Any], usage: RequestUsage) -> CreateResult:
    """The gateway's OpenAI-format assistant message as a CreateResult, as `BaseOpenAIChatCompletionClient.create`
    parses a completion: tool calls become FunctionCalls (names normalised, arguments kept as the raw string; the
    agent parses them and reports a parse failure to the model), text beside tool calls becomes the `thought`."""
    calls = msg.get("tool_calls") or []
    if calls:
        content: str | list[FunctionCall] = []
        for tc in calls:
            fn = tc.get("function") or {}
            args = fn.get("arguments")
            content.append(FunctionCall(id=tc.get("id") or "", name=normalize_name(fn.get("name") or ""),
                                        arguments=json.dumps(args) if isinstance(args, dict) else args or ""))
        return CreateResult(finish_reason="function_calls", content=content, usage=usage, cached=False,
                            thought=msg.get("content") or None)
    return CreateResult(finish_reason="stop", content=msg.get("content") or "", usage=usage, cached=False)


class GatewayChatCompletionClient(ChatCompletionClient):
    """An autogen-core model client whose every call goes through the run's ModelGateway (RunnerContext.model).

    Usage: tokens are counted where every run counts them, at the gateway; AutoGen's own usage fields report the
    budget's token delta as prompt tokens (the gateway's in/out split stays in the trace)."""

    def __init__(self, ctx: RunnerContext, model: str = "") -> None:
        self._ctx = ctx
        self._model = model
        self.calls = 0                                    # model calls the agent made
        self._usage = RequestUsage(prompt_tokens=0, completion_tokens=0)

    async def create(self, messages: Sequence[LLMMessage], *, tools: Sequence[Tool | ToolSchema] = [],
                     tool_choice: Tool | Literal["auto", "required", "none"] = "auto",
                     json_output: bool | type[BaseModel] | None = None, extra_create_args: Mapping[str, Any] = {},
                     cancellation_token: CancellationToken | None = None) -> CreateResult:
        if tool_choice != "auto" or json_output is not None or extra_create_args:
            raise NotImplementedError("the gateway sends no tool_choice, response format or extra arguments; the "
                                      "runner leaves these AssistantAgent options at their defaults")
        self.calls += 1
        wire, wire_tools = to_wire(messages, tools, self._model)
        before = self._ctx.inst.budget.used_tokens
        reply = await self._ctx.model(wire, wire_tools)
        usage = RequestUsage(prompt_tokens=self._ctx.inst.budget.used_tokens - before, completion_tokens=0)
        self._usage = RequestUsage(prompt_tokens=self._usage.prompt_tokens + usage.prompt_tokens, completion_tokens=0)
        return from_wire(reply, usage)

    async def create_stream(self, messages: Sequence[LLMMessage], *, tools: Sequence[Tool | ToolSchema] = [],
                            tool_choice: Tool | Literal["auto", "required", "none"] = "auto",
                            json_output: bool | type[BaseModel] | None = None,
                            extra_create_args: Mapping[str, Any] = {},
                            cancellation_token: CancellationToken | None = None,
                            ) -> AsyncGenerator[str | CreateResult, None]:
        # Not used at the defaults (model_client_stream=False); the gateway answers whole, so: one final result.
        yield await self.create(messages, tools=tools, tool_choice=tool_choice, json_output=json_output,
                                extra_create_args=extra_create_args, cancellation_token=cancellation_token)

    async def close(self) -> None:
        return None

    def actual_usage(self) -> RequestUsage:
        return self._usage

    def total_usage(self) -> RequestUsage:
        return self._usage

    def count_tokens(self, messages: Sequence[LLMMessage], *, tools: Sequence[Tool | ToolSchema] = []) -> int:
        raise NotImplementedError("tokens are counted at the gateway; the default model context never asks")

    def remaining_tokens(self, messages: Sequence[LLMMessage], *, tools: Sequence[Tool | ToolSchema] = []) -> int:
        raise NotImplementedError("tokens are counted at the gateway; the default model context never asks")

    @property
    def capabilities(self) -> ModelCapabilities:  # type: ignore[override]  (deprecated in autogen-core)
        return MODEL_INFO  # type: ignore[return-value]

    @property
    def model_info(self) -> ModelInfo:
        return MODEL_INFO


# ------------------------------------------------------------------ measurement point (b): the tools

class ExecutorTool(BaseTool[BaseModel, object]):
    """One MCP tool of `ExecutorSession` as an autogen-core tool, built the way autogen-ext's `McpToolAdapter` builds
    one (name, description, argument model from the input schema via `schema_to_pydantic_model`) and called the way
    it calls its session (`model_dump(exclude_unset=True)`; an `isError` result raises with the server's text, which
    AutoGen's workbench reports to the model as the error result). It returns the result's text."""

    def __init__(self, session: ExecutorSession, tool: Any) -> None:
        self._session = session
        super().__init__(schema_to_pydantic_model(tool.input_schema), object, tool.name, tool.description or "")

    async def run(self, args: BaseModel, cancellation_token: CancellationToken) -> str:
        result = await self._session.call_tool(self.name, args.model_dump(exclude_unset=True))
        text = "\n".join(c.text for c in result.content if getattr(c, "type", None) == "text")
        if result.is_error:
            raise Exception(text)  # noqa: TRY002  (McpToolAdapter raises a plain Exception, as here)
        return text


async def build_team(ctx: RunnerContext, system: str) -> RoundRobinGroupChat:
    """The framework's agent at its defaults, in AutoGen's single-agent team that runs it until it answers in text."""
    session = ExecutorSession(ctx)
    tools = [ExecutorTool(session, t) for t in (await session.list_tools()).tools]
    agent = AssistantAgent(AGENT_NAME, model_client=GatewayChatCompletionClient(ctx, ctx.slot.canonical_model or ""),
                           tools=tools, system_message=system)
    return RoundRobinGroupChat([agent], termination_condition=TextMessageTermination(AGENT_NAME))


class AutoGenAgentChatRunner:
    runtime = "autogen-agentchat"

    async def run(self, inst: HarnessInstance) -> RunResult:
        ctx = RunnerContext(inst, inst.recipe.model_slots["executor"])
        system, task = ctx.system_and_task()
        team = await build_team(ctx, system)
        (agent,) = team._participants
        inst.snapshot["runner"] = {"runtime": self.runtime, "agent": type(agent).__name__, "team": type(team).__name__,
                                   "tools": len(agent._tools)}
        try:
            result = await team.run(task=task)
        except RunnerStop:
            return ctx.finish()
        except Exception:
            if ctx.stop is not None:                      # the team re-raised our stop as its own error
                return ctx.finish()
            raise
        if ctx.stop is not None:                          # e.g. a stop inside a tool, reported to the model
            return ctx.finish()
        final = next((m for m in reversed(result.messages) if isinstance(m, TextMessage) and m.source == AGENT_NAME),
                     None)
        return ctx.finish(final.content if final else None)
