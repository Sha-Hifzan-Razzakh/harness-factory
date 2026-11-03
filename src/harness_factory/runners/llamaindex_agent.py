"""`llamaindex-agent` runner: LlamaIndex's `FunctionAgent` (docs/adr/0016-runner-axis.md).

Built from the framework's own components:
- the agent: `llama_index.core.agent.workflow.FunctionAgent`, a LlamaIndex `Workflow`, at its defaults: streaming on,
  parallel tool calls allowed, `ChatMemoryBuffer` memory sized from the model's context window, 20 iterations
  (`DEFAULT_MAX_ITERATIONS`) with early stopping "force", no workflow timeout;
- the MCP tools: `llama_index.tools.mcp.McpToolSpec` (llama-index-tools-mcp), which builds one `FunctionTool` per MCP
  tool: a pydantic argument model generated from the tool's JSON schema, and an async function returning the
  session's `CallToolResult`, which `FunctionTool` turns into the text the model sees (`str()` of the result object,
  the framework's default);
- the model client: `GatewayLLM`, a `FunctionCallingLLM` (measurement point a). Tools are formatted with the
  framework's `ToolMetadata.to_openai_tool`; messages and tool calls are converted the way llama-index-llms-openai's
  OpenAI class converts them (tool calls in `additional_kwargs["tool_calls"]`, tool results matched by
  `tool_call_id`).

What the framework adds to the conversation: nothing in text by default. The recipe's system message is the agent's
`system_prompt` (prepended to every model call), its task message the run's `user_msg`. The framework decides the tool
schemas sent (its pydantic models), how tool results read, how errors from tools reach the model, how much history is
kept, and when the loop ends (a reply without tool calls, or the iteration cap).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import Sequence
from typing import Any

from llama_index.core.base.llms.types import (
    ChatMessage,
    ChatResponse,
    ChatResponseAsyncGen,
    ChatResponseGen,
    CompletionResponse,
    CompletionResponseAsyncGen,
    CompletionResponseGen,
    LLMMetadata,
    MessageRole,
)
from llama_index.core.llms.callbacks import llm_chat_callback, llm_completion_callback
from llama_index.core.llms.function_calling import FunctionCallingLLM
from llama_index.core.llms.llm import ToolSelection
from pydantic import PrivateAttr

from harness_factory.runners.base import ExecutorSession, RunnerContext, RunnerStop
from harness_factory.shapes.base import HarnessInstance
from harness_factory.shapes.result import RunResult

MAX_ITERATIONS_MARK = "Max iterations"           # FunctionAgent's own message when its iteration cap is reached


def to_wire(messages: Sequence[ChatMessage]) -> list[dict[str, Any]]:
    """LlamaIndex chat messages as the OpenAI-format messages the gateway sends."""
    out: list[dict[str, Any]] = []
    for m in messages:
        role = m.role.value if isinstance(m.role, MessageRole) else str(m.role)
        if role == "tool":
            out.append({"role": "tool", "tool_call_id": m.additional_kwargs.get("tool_call_id"),
                        "content": m.content or ""})
        elif role == "assistant" and m.additional_kwargs.get("tool_calls"):
            out.append({"role": "assistant", "content": m.content or None,
                        "tool_calls": m.additional_kwargs["tool_calls"]})
        else:
            out.append({"role": role, "content": m.content or ""})
    return out


def from_wire(msg: dict[str, Any]) -> ChatMessage:
    kwargs = {"tool_calls": msg["tool_calls"]} if msg.get("tool_calls") else {}
    return ChatMessage(role=MessageRole.ASSISTANT, content=msg.get("content") or "", additional_kwargs=kwargs)


class GatewayLLM(FunctionCallingLLM):
    """A LlamaIndex LLM whose every call goes through the run's ModelGateway (via RunnerContext.model)."""

    model_name: str = ""
    context_window: int = 32_768
    num_output: int = 4096
    _ctx: Any = PrivateAttr(default=None)             # RunnerContext

    def __init__(self, ctx: RunnerContext, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._ctx = ctx

    @classmethod
    def class_name(cls) -> str:
        return "HarnessFactoryGatewayLLM"

    @property
    def metadata(self) -> LLMMetadata:
        return LLMMetadata(context_window=self.context_window, num_output=self.num_output, is_chat_model=True,
                           is_function_calling_model=True, model_name=self.model_name)

    # --------------------------------------------------------------- tools, as llama-index-llms-openai does it

    def _prepare_chat_with_tools(self, tools, user_msg=None, chat_history=None, verbose: bool = False,
                                 allow_parallel_tool_calls: bool = False, tool_required: bool = False,
                                 **kwargs: Any) -> dict[str, Any]:
        messages = list(chat_history or [])
        if user_msg is not None:
            messages.append(ChatMessage(role=MessageRole.USER, content=user_msg) if isinstance(user_msg, str)
                            else user_msg)
        return {"messages": messages,
                "tools": [t.metadata.to_openai_tool(skip_length_check=True) for t in tools], **kwargs}

    def get_tool_calls_from_response(self, response: ChatResponse, error_on_no_tool_call: bool = True,
                                     **kwargs: Any) -> list[ToolSelection]:
        calls = response.message.additional_kwargs.get("tool_calls") or []
        if not calls and error_on_no_tool_call:
            raise ValueError(f"Expected at least one tool call, but got {len(calls)} tool calls.")
        out = []
        for c in calls:
            try:
                args = json.loads(c["function"].get("arguments") or "{}")
            except ValueError:
                args = {}
            out.append(ToolSelection(tool_id=c["id"], tool_name=c["function"]["name"],
                                     tool_kwargs=args if isinstance(args, dict) else {}))
        return out

    # --------------------------------------------------------------- the one place a call leaves the framework

    async def _call(self, messages: Sequence[ChatMessage], **kwargs: Any) -> ChatResponse:
        reply = await self._ctx.model(to_wire(messages), list(kwargs.get("tools") or []))
        return ChatResponse(message=from_wire(reply), delta=reply.get("content") or "", raw=reply)

    @llm_chat_callback()
    async def achat(self, messages: Sequence[ChatMessage], **kwargs: Any) -> ChatResponse:
        return await self._call(messages, **kwargs)

    @llm_chat_callback()
    async def astream_chat(self, messages: Sequence[ChatMessage], **kwargs: Any) -> ChatResponseAsyncGen:
        async def gen() -> ChatResponseAsyncGen:
            yield await self._call(messages, **kwargs)      # the gateway answers whole: one chunk
        return gen()

    @llm_completion_callback()
    async def acomplete(self, prompt: str, formatted: bool = False, **kwargs: Any) -> CompletionResponse:
        r = await self._call([ChatMessage(role=MessageRole.USER, content=prompt)], **kwargs)
        return CompletionResponse(text=r.message.content or "", raw=r.raw)

    async def astream_complete(self, prompt: str, formatted: bool = False, **kwargs: Any) -> CompletionResponseAsyncGen:
        async def gen() -> CompletionResponseAsyncGen:
            yield await self.acomplete(prompt, formatted, **kwargs)
        return gen()

    def chat(self, messages: Sequence[ChatMessage], **kwargs: Any) -> ChatResponse:
        raise NotImplementedError("runner runs are async")

    def stream_chat(self, messages: Sequence[ChatMessage], **kwargs: Any) -> ChatResponseGen:
        raise NotImplementedError("runner runs are async")

    def complete(self, prompt: str, formatted: bool = False, **kwargs: Any) -> CompletionResponse:
        raise NotImplementedError("runner runs are async")

    def stream_complete(self, prompt: str, formatted: bool = False, **kwargs: Any) -> CompletionResponseGen:
        raise NotImplementedError("runner runs are async")


async def build_agent(ctx: RunnerContext, system: str):
    """The framework's agent at its defaults: tools from its MCP tool spec, the gateway LLM, the recipe's system
    prompt."""
    from llama_index.core.agent.workflow import FunctionAgent
    from llama_index.tools.mcp import McpToolSpec

    inst = ctx.inst
    served = inst.gateway.catalog.served(inst.gateway.provider.id, ctx.slot.canonical_model)
    tools = await McpToolSpec(client=ExecutorSession(ctx)).to_tool_list_async()
    llm = GatewayLLM(ctx, model_name=ctx.slot.canonical_model or "", context_window=served.caps.context_len,
                     num_output=ctx.slot.max_output_tokens)
    return FunctionAgent(tools=tools, llm=llm, system_prompt=system)


class LlamaIndexAgentRunner:
    runtime = "llamaindex-agent"

    async def run(self, inst: HarnessInstance) -> RunResult:
        ctx = RunnerContext(inst, inst.recipe.model_slots["executor"])
        system, task = ctx.system_and_task()
        agent = await build_agent(ctx, system)
        inst.snapshot["runner"] = {"runtime": self.runtime, "agent": type(agent).__name__,
                                   "tools": len(agent.tools or [])}
        handler = agent.run(user_msg=task)
        try:
            result = await handler
        except asyncio.CancelledError:        # the workflow runs in its own tasks: stop them before teardown
            with contextlib.suppress(Exception):
                await asyncio.shield(handler.cancel_run())
            raise
        except RunnerStop:
            return ctx.finish()
        except Exception as e:
            if ctx.stop is not None:          # the workflow wrapped our stop: the first stop recorded decides
                return ctx.finish()
            if MAX_ITERATIONS_MARK in str(e):  # the framework's own iteration cap: a harness stop
                ctx.halt("budget:framework max_iterations", str(e)[:300])
                return ctx.finish()
            raise
        if ctx.stop is not None:
            return ctx.finish()
        return ctx.finish(result.response.content or "")
