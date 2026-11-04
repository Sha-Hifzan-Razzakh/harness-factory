"""`langgraph-prebuilt` runner: LangGraph's ready-made tool-calling agent (docs/adr/0016-runner-axis.md).

Built from the framework's own components:
- the agent: `langchain.agents.create_agent`, the successor of `langgraph.prebuilt.create_react_agent` (deprecated
  in LangGraph v1). It compiles a LangGraph `StateGraph` with a `model` node and a `tools` node, the latter
  `langgraph.prebuilt.ToolNode`, and runs it with its own recursion limit (9 999) and no middleware;
- the MCP tools: `langchain.mcp.as_langchain_tool`, the successor of langchain-mcp-adapters (whose releases pin
  `mcp<2`, so it cannot run beside the factory's MCP 2.x). It converts each MCP tool into a `StructuredTool` (name,
  description, JSON schema normalised by the adapter) whose results become LangChain content blocks, and turns an
  `isError` result into a `ToolMessage` with status "error";
- the model client: `GatewayChatModel`, a `BaseChatModel` (measurement point a). Its `bind_tools` formats tools with
  langchain-core's `convert_to_openai_tool`, its requests are converted with `convert_to_openai_messages`, its
  replies are parsed with `parse_tool_call` / `make_invalid_tool_call`, as langchain-openai's chat model does.

What the framework adds to the conversation: nothing in text. The recipe's system message is `system_prompt` (sent
first, unchanged), its task message the one input message. The framework decides the tool schemas sent (the
adapter's normalisation), how tool results and tool errors read, what happens to invalid tool calls, and when the
loop ends (a reply without tool calls).
"""

from __future__ import annotations

import warnings
from typing import Any

from langchain_core.callbacks import AsyncCallbackManagerForLLMRun, CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, convert_to_openai_messages
from langchain_core.output_parsers.openai_tools import make_invalid_tool_call, parse_tool_call
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import ConfigDict, Field

from harness_factory.runners.base import ExecutorSession, RunnerContext, RunnerStop
from harness_factory.shapes.base import HarnessInstance
from harness_factory.shapes.result import RunResult


def to_ai_message(msg: dict[str, Any]) -> AIMessage:
    """An OpenAI-format assistant message (what the gateway returns) as LangChain's AIMessage, parsed the way
    langchain-openai parses a chat completion: malformed tool calls become invalid_tool_calls, not exceptions."""
    calls, invalid = [], []
    for raw in msg.get("tool_calls") or []:
        try:
            calls.append(parse_tool_call(raw, return_id=True))
        except Exception as e:  # noqa: BLE001  (langchain-openai does the same: kept as an invalid call)
            invalid.append(make_invalid_tool_call(raw, str(e)))
    return AIMessage(content=msg.get("content") or "", tool_calls=calls, invalid_tool_calls=invalid)


class GatewayChatModel(BaseChatModel):
    """A LangChain chat model whose every call goes through the run's ModelGateway (via RunnerContext.model)."""

    model_config = ConfigDict(arbitrary_types_allowed=True)
    ctx: Any = Field(exclude=True)           # RunnerContext
    model_name: str = ""

    @property
    def _llm_type(self) -> str:
        return "harness-factory-gateway"

    @property
    def _identifying_params(self) -> dict[str, Any]:
        return {"model_name": self.model_name}

    def bind_tools(self, tools, *, tool_choice: str | None = None, **kwargs: Any):
        if tool_choice not in (None, "auto"):
            raise NotImplementedError("the gateway sends no tool_choice; forced tool use is a framework option "
                                      "the runner leaves at its default")
        return self.bind(tools=[convert_to_openai_tool(t) for t in tools], **kwargs)

    def _generate(self, messages: list[BaseMessage], stop: list[str] | None = None,
                  run_manager: CallbackManagerForLLMRun | None = None, **kwargs: Any) -> ChatResult:
        raise NotImplementedError("runner runs are async: the agent is invoked with ainvoke")

    async def _agenerate(self, messages: list[BaseMessage], stop: list[str] | None = None,
                         run_manager: AsyncCallbackManagerForLLMRun | None = None, **kwargs: Any) -> ChatResult:
        wire = convert_to_openai_messages(messages, text_format="string")
        reply = await self.ctx.model(wire, list(kwargs.get("tools") or []))
        return ChatResult(generations=[ChatGeneration(message=to_ai_message(reply))])


def build_agent(ctx: RunnerContext, tools: list, system: str):
    """The framework's agent graph, at its defaults: model, tools and the recipe's system prompt only."""
    from langchain.agents import create_agent
    return create_agent(GatewayChatModel(ctx=ctx, model_name=ctx.slot.canonical_model or ""), tools,
                        system_prompt=system)


async def load_tools(session: ExecutorSession) -> list:
    """LangChain tools built by langchain.mcp from the MCP tools the session announces."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")      # LangChainBetaWarning: `langchain.mcp` is a beta namespace
        from langchain.mcp import as_langchain_tool
    listed = await session.list_tools()
    return [await as_langchain_tool(t, session) for t in listed.tools]


class LangGraphPrebuiltRunner:
    runtime = "langgraph-prebuilt"

    async def run(self, inst: HarnessInstance) -> RunResult:
        from langgraph.errors import GraphRecursionError

        ctx = RunnerContext(inst, inst.recipe.model_slots["executor"])
        system, task = ctx.system_and_task()
        agent = build_agent(ctx, await load_tools(ExecutorSession(ctx)), system)
        inst.snapshot["runner"] = {"runtime": self.runtime, "agent": type(agent).__name__,
                                   "nodes": sorted(n for n in agent.nodes if not n.startswith("__"))}
        try:
            state = await agent.ainvoke({"messages": [HumanMessage(task)]})
        except RunnerStop:
            return ctx.finish()
        except GraphRecursionError as e:     # the framework's own step bound: a harness stop
            ctx.halt("budget:framework recursion_limit", str(e)[:300])
            return ctx.finish()
        if ctx.stop is not None:
            return ctx.finish()
        final = state["messages"][-1]
        return ctx.finish(final.text if isinstance(final, AIMessage) else str(final.content))
