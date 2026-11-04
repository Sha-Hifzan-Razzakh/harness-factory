"""`ms-agent-framework` runner: Microsoft Agent Framework's own `Agent` (docs/adr/0016-runner-axis.md).

Built from the framework's own components (agent-framework-core, the package the `agent-framework` meta package
installs with every provider connector; the connectors aren't needed here):
- the agent: `agent_framework.Agent` (the successor of Semantic Kernel's `ChatCompletionAgent` and AutoGen's
  `AssistantAgent`; called `ChatAgent` before 1.0), at its defaults: no name, no middleware, no context providers
  (so the run gets the framework's `InMemoryHistoryProvider`), `tool_choice` "auto". Its tool loop is the framework's
  `FunctionInvocationLayer`, which every Agent Framework chat client composes, at its defaults: 40 model round trips
  (`DEFAULT_MAX_ITERATIONS`) then one last call with tools disabled, at most 3 consecutive failing tool batches
  before tools are disabled, concurrent invocation of the calls of one reply, no detailed errors;
- the MCP tools: `agent_framework.MCPStdioTool` given a caller-owned client (`mcp_client=`, the non-deprecated way
  to hand an MCP tool an existing connection), `ExecutorMCPClient`. The framework's `MCPTool` lists the tools, builds
  one `FunctionTool` per MCP tool (name, description, input schema), filters each call's arguments to the schema's
  properties, calls `call_tool`, and turns the `CallToolResult` into what the model sees (its `structured_first`
  default: the JSON of `structuredContent` when there is one, else the text blocks), or, for an `isError` result,
  into an exception that its function-invocation layer reports to the model as "Error: Function failed.";
- the model client: `GatewayChatClient`, a `BaseChatClient` composed with the framework's `FunctionInvocationLayer`,
  `ChatMiddlewareLayer` and `ChatTelemetryLayer` in the order the framework's own clients use (measurement point a).
  Messages, tools and replies are converted the way agent-framework-openai's `OpenAIChatCompletionClient` converts
  them for the Chat Completions API: instructions prepended as a system message, tools as
  `FunctionTool.to_json_schema_spec()`, tool calls and results by `call_id`.

What the framework adds to the conversation: nothing in text. The recipe's system message is the agent's
`instructions` (sent first, as a system message, on every call), its task message the run's input. The framework
decides the tool schemas sent, how tool results and tool errors read, what happens to invalid calls, and when the
loop ends (a reply without tool calls, or its iteration cap).
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any, ClassVar

from agent_framework import (
    Agent,
    BaseChatClient,
    ChatMiddlewareLayer,
    ChatResponse,
    ChatResponseUpdate,
    Content,
    FunctionInvocationLayer,
    FunctionTool,
    MCPStdioTool,
    Message,
    prepend_instructions_to_messages,
)
from agent_framework.observability import ChatTelemetryLayer

from harness_factory.runners.base import ExecutorSession, RunnerContext, RunnerStop
from harness_factory.shapes.base import HarnessInstance
from harness_factory.shapes.result import RunResult

MCP_TOOL_NAME = "harness-factory"            # the MCPTool's own name: not shown to the model (no tool_name_prefix)


# ------------------------------------------------------------------ measurement point (a): the chat client

def _text(message: Message) -> str:
    return "\n".join(c.text for c in message.contents if c.type == "text" and c.text)


def to_wire(messages: Sequence[Message]) -> list[dict[str, Any]]:
    """Agent Framework messages as OpenAI Chat Completions messages, following `OpenAIChatCompletionClient`
    (`_build_openai_messages`) for the content a tool-calling run has: text, function calls (joined to the assistant
    message of the same turn), function results (one tool message each, their text items joined). Approval content is
    framework state and is skipped, as there; anything else is refused rather than silently dropped."""
    out: list[dict[str, Any]] = []
    for m in messages:
        role = str(m.role)
        if role in ("system", "developer"):
            if text := _text(m):
                out.append({"role": role, "content": text})
            continue
        turn: dict[str, Any] | None = None          # the assistant message this turn's text and calls share
        for c in m.contents:
            if c.type in ("function_approval_request", "function_approval_response"):
                continue
            if c.type == "function_call":
                args = json.dumps(c.arguments) if isinstance(c.arguments, Mapping) else c.arguments
                call = {"id": c.call_id, "type": "function", "function": {"name": c.name, "arguments": args or ""}}
                if turn is None:
                    turn = {"role": role, "content": None}
                    out.append(turn)
                turn.setdefault("tool_calls", []).append(call)
            elif c.type == "function_result":
                if c.items:
                    text = "\n".join(i.text or "" for i in c.items if i.type == "text")
                else:
                    text = c.result if c.result is not None else ""
                out.append({"role": "tool", "tool_call_id": c.call_id,
                            "content": text if isinstance(text, str) else json.dumps(text, default=str)})
                turn = None
            elif c.type == "text":
                if turn is not None and turn.get("content") is None:
                    turn["content"] = c.text or ""
                elif turn is not None:
                    turn["content"] += "\n" + (c.text or "")
                else:
                    turn = {"role": role, "content": c.text or ""}
                    out.append(turn)
                if role != "assistant":
                    turn = None
            else:
                raise ValueError(f"ms-agent-framework runner: unsupported {c.type!r} content in a {role} message")
    return out


def from_wire(msg: dict[str, Any]) -> ChatResponse:
    """The gateway's OpenAI-format assistant message as a ChatResponse, as `OpenAIChatCompletionClient` parses a
    completion: text, then one function_call content per tool call (arguments kept as the raw JSON string; the
    framework parses them when it invokes the tool and reports a parse failure to the model)."""
    contents = [Content.from_text(text=msg["content"])] if msg.get("content") else []
    for tc in msg.get("tool_calls") or []:
        fn = tc.get("function") or {}
        contents.append(Content.from_function_call(call_id=tc.get("id") or "", name=fn.get("name") or "",
                                                   arguments=fn.get("arguments") or ""))
    calls = any(c.type == "function_call" for c in contents)
    return ChatResponse(messages=[Message(role="assistant", contents=contents)],
                        finish_reason="tool_calls" if calls else "stop")


class GatewayChatClient(FunctionInvocationLayer[Any], ChatMiddlewareLayer[Any], ChatTelemetryLayer[Any],
                        BaseChatClient[Any]):
    """An Agent Framework chat client whose every call goes through the run's ModelGateway (RunnerContext.model).

    The layers are the framework's, in the order its own clients compose them (function invocation outside chat
    middleware outside telemetry): the tool loop is `FunctionInvocationLayer.get_response`, and each of its model
    round trips reaches `_inner_get_response`, the one place a call leaves the framework."""

    OTEL_PROVIDER_NAME: ClassVar[str] = "harness-factory-gateway"

    def __init__(self, ctx: RunnerContext, model: str = "") -> None:
        super().__init__()                                # every layer at its defaults
        self._ctx = ctx
        self.model = model
        self.calls = 0                                    # model round trips the framework asked for
        self.forced_end: str | None = None                # why the framework disabled its tools, if it did

    def _inner_get_response(self, *, messages: Sequence[Message], stream: bool, options: Mapping[str, Any],
                            **kwargs: Any):
        async def call() -> ChatResponse:
            opts = await self._validate_options(options)
            msgs = list(messages)
            if instructions := opts.get("instructions"):
                msgs = prepend_instructions_to_messages(msgs, instructions, role="system")
            tool_choice = opts.get("tool_choice")
            mode = tool_choice.get("mode") if isinstance(tool_choice, Mapping) else tool_choice
            if mode not in (None, "auto", "none"):
                raise NotImplementedError(f"the gateway sends no tool_choice; {mode!r} is a framework option the "
                                          f"runner leaves at its default")
            # "none" is the framework's own way to end its loop (its iteration cap, or consecutive tool errors):
            # the gateway has no tool_choice, so the request offers no tools, which leaves the model the same
            # choice (answer in text). The runner records it as a harness stop.
            if mode == "none" and self.forced_end is None:
                cap = self.function_invocation_configuration["max_iterations"]
                self.forced_end = "max_iterations" if self.calls >= cap else "max_consecutive_errors"
            self.calls += 1
            tools = [] if mode == "none" else [t.to_json_schema_spec() for t in opts.get("tools") or []
                                              if isinstance(t, FunctionTool)]
            return from_wire(await self._ctx.model(to_wire(msgs), tools))

        if stream:
            async def updates():
                r = await call()                          # the gateway answers whole: one update
                yield ChatResponseUpdate(role="assistant", contents=list(r.messages[0].contents),
                                         finish_reason=r.finish_reason)
            return self._build_response_stream(updates(), response_format=options.get("response_format"))
        return call()


# ------------------------------------------------------------------ measurement point (b): the MCP client

class ExecutorMCPClient(ExecutorSession):
    """`ExecutorSession` with the parts of a high-level `mcp.Client` that Agent Framework's `MCPTool` reads from a
    caller-owned client: `list_tools(cursor=)`, `call_tool(name, arguments=, meta=)`, `server_capabilities` and
    `session.initialize_result`. The capabilities announce tools only (no prompts, no logging, no list-change
    notifications) and there is no legacy initialize result, so the framework never pings, never subscribes and never
    lists prompts: it lists the tools once and calls them."""

    class _NoLegacySession:
        initialize_result = None

    session = _NoLegacySession()

    @property
    def server_capabilities(self):
        from mcp.types import ServerCapabilities, ToolsCapability
        return ServerCapabilities(tools=ToolsCapability(list_changed=False))


def build_agent(ctx: RunnerContext, system: str) -> Agent:
    """The framework's agent at its defaults: its MCP tool over the executor client, the gateway client, the recipe's
    system prompt as instructions."""
    tools = MCPStdioTool(name=MCP_TOOL_NAME, mcp_client=ExecutorMCPClient(ctx))
    return Agent(client=GatewayChatClient(ctx, model=ctx.slot.canonical_model or ""), instructions=system,
                 tools=tools)


class MsAgentFrameworkRunner:
    runtime = "ms-agent-framework"

    async def run(self, inst: HarnessInstance) -> RunResult:
        ctx = RunnerContext(inst, inst.recipe.model_slots["executor"])
        system, task = ctx.system_and_task()
        agent = build_agent(ctx, system)
        try:
            async with agent:                             # connects the MCP tool: lists the tools once
                inst.snapshot["runner"] = {"runtime": self.runtime, "agent": type(agent).__name__,
                                           "tools": len(agent.mcp_tools[0].functions)}
                response = await agent.run(task)
        except RunnerStop:
            return ctx.finish()
        except Exception:
            if ctx.stop is not None:                      # the framework wrapped our stop: the first stop decides
                return ctx.finish()
            raise
        if ctx.stop is not None:
            return ctx.finish()
        if agent.client.forced_end:                       # the framework's own loop bound: a harness stop
            ctx.halt(f"budget:framework {agent.client.forced_end}", response.text[:300])
            return ctx.finish()
        return ctx.finish(response.text)
