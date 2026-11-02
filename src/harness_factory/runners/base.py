"""Runner interface and the two measurement points every framework runner shares (RUN-03, FWK-02).

A framework runner hands the run to a framework's own ready-made agent. The factory wraps exactly two things:

(a) the model client: every call the framework makes to "its model" goes through `RunnerContext.model`, which is
    `shapes.common.model_turn`, the same turn the native shapes take: BudgetGuard check first, then
    `ModelGateway.complete` (quota slot, timeout, retries of transient provider errors, error classification in
    `classify_provider_error`, token and cost accounting, the model_call span);
(b) the tool callables: the framework's MCP adapter builds its tool objects (names, descriptions, schemas, result
    formatting) from an MCP session, `ExecutorSession`, whose `call_tool` goes to `RunnerContext.tool`: a budget
    check, then `ToolExecutor.execute` (allowance, permission policy, injected faults, violations, the tool_call
    span). The adapter never talks to an MCP server directly.

Everything in between (the agent loop, the framework's prompts and scaffolding, tool formatting, call parsing,
retries, history) is the framework's, at its defaults.

A stop the factory decides (a budget limit, a provider error) is raised into the framework as `RunnerStop`, recorded
first on the context: some frameworks catch exceptions from tools, and whatever the framework does with the
exception, the run ends with the first stop recorded, mapped by `shapes.common.finish` exactly as a native shape's.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from typing import Any, ClassVar, Protocol

from harness_factory.mcp.client import ToolResult
from harness_factory.recipes.model import HarnessRecipe, ModelSlot
from harness_factory.shapes.base import HarnessInstance, ShapeRegistry
from harness_factory.shapes.common import DONE, finish, model_turn
from harness_factory.shapes.result import RunResult


class RunnerStop(Exception):
    """Raised into the framework when the factory ends the run (budget, provider error). Never caught by us inside
    the framework; the runner maps the recorded stop to a RunResult after the framework returns or raises."""

    def __init__(self, stop: str, error: str | None = None) -> None:
        super().__init__(f"{stop}: {error}" if error else stop)
        self.stop = stop
        self.error = error


@dataclass
class RunnerContext:
    """Per-run state the two wrappers share. Lives in the run, never at module level (campaigns run many at once)."""
    inst: HarnessInstance
    slot: ModelSlot
    stop: str | None = None                 # first stop the factory decided; DONE when the framework answered
    error: str | None = None
    transcript: list[dict[str, Any]] = field(default_factory=list)   # last conversation sent, plus the reply
    model_calls: int = 0                    # calls the framework made into the model client
    tool_calls: int = 0                     # calls the framework made into tool callables
    tools_in_flight: int = 0                # started, not yet charged to the budget (frameworks run calls at once)

    def halt(self, stop: str, error: str | None = None) -> RunnerStop:
        if self.stop is None:
            self.stop, self.error = stop, error
        return RunnerStop(self.stop, self.error)

    async def model(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        """Measurement point (a). `messages` and `tools` are OpenAI-format, converted by the framework's own
        converters in the runner's model class. Returns the assistant message the gateway produced."""
        self.model_calls += 1
        if self.stop is not None:
            raise RunnerStop(self.stop, self.error)
        self.transcript = list(messages)
        turn = await model_turn(self.inst, self.slot, messages, tools)
        self.transcript += turn.added
        if turn.stop:
            raise self.halt(turn.stop, turn.error)
        assert turn.message is not None
        return turn.message

    async def tool(self, name: str, arguments: dict[str, Any] | str | None) -> ToolResult:
        """Measurement point (b): the same order as the native `run_tool_calls`: budget, then ToolExecutor.

        Native runs a reply's calls one after another; several frameworks start them all at once (Agent Framework,
        AutoGen). A call is charged to the budget when it ends, so calls still running count against max_tool_calls
        here: a batch never runs more calls than the budget has left, as natively."""
        self.tool_calls += 1
        if self.stop is not None:
            raise RunnerStop(self.stop, self.error)
        budget = self.inst.budget
        limit = budget.exceeded_by(before="tool_call")
        if not limit and budget.used_tool_calls + self.tools_in_flight >= budget.limits.max_tool_calls:
            limit = f"max_tool_calls {budget.limits.max_tool_calls}"
        if limit:
            raise self.halt(f"budget:{limit}")
        self.tools_in_flight += 1
        try:
            return await self.inst.tools.execute(name, arguments if arguments is not None else {},
                                                 scope=self.inst.scope)
        finally:
            self.tools_in_flight -= 1

    def system_and_task(self) -> tuple[str, str]:
        """The recipe's assembled prompt: its system message becomes the framework's system prompt / instructions,
        its user message the agent's input. Nothing else is given to the framework."""
        msgs = self.inst.messages
        if [m.get("role") for m in msgs] != ["system", "user"]:
            raise ValueError(f"a runner expects the assembled [system, user] prompt, got {[m.get('role') for m in msgs]}")
        return str(msgs[0]["content"]), str(msgs[1]["content"])

    def finish(self, output: Any = None) -> RunResult:
        """The RunResult, as a native shape's: `shapes.common.finish` maps the stop (or DONE). What the framework did
        is recorded on the snapshot: how many calls it made into each wrapper. Each became a gateway call or a
        ToolExecutor call, except calls refused because the run had already stopped."""
        self.inst.snapshot.setdefault("runner", {}).update(framework_model_calls=self.model_calls,
                                                           framework_tool_calls=self.tool_calls)
        return finish(self.inst, self.transcript, self.stop or DONE, output if self.stop in (None, DONE) else None,
                      self.error)


class ExecutorSession:
    """The MCP session a framework's MCP adapter is given (measurement point b).

    `list_tools` announces exactly the tools the native runtime offers this run (`ToolExecutor.tools_for_model`: the
    task allowance on the recipe's servers, as the servers described them, model-facing names `server__tool`), as
    MCP `Tool`s. `call_tool` runs the call through `RunnerContext.tool` and answers with an MCP `CallToolResult`, the
    type a `ClientSession` returns, so the adapter's own result conversion is what the model sees.

    It implements the parts of the client surface the adapters use: `list_tools` / `call_tool` (the duck-typed
    `ClientSession` llama-index-tools-mcp documents), the async context manager, `call_tool(..., raise_on_error=False)`
    and `server_info` (what `langchain.mcp.as_langchain_tool` reads from a FastMCP client)."""

    server_info = None                       # no server identity to report: the tools come from several servers

    def __init__(self, ctx: RunnerContext) -> None:
        self.ctx = ctx

    async def __aenter__(self) -> ExecutorSession:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def list_tools(self, cursor: str | None = None, **_: Any):
        from mcp.types import ListToolsResult, Tool
        tools = [Tool(name=t["function"]["name"], description=t["function"]["description"] or None,
                      input_schema=t["function"]["parameters"])
                 for t in self.ctx.inst.tools.tools_for_model(None)]
        return ListToolsResult(tools=tools)

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None, *_: Any, **__: Any):
        from mcp.types import CallToolResult, TextContent
        result = await self.ctx.tool(name, arguments)
        return CallToolResult(content=[TextContent(type="text", text=result.text)] if result.text else [],
                              structured_content=result.structured if isinstance(result.structured, dict) else None,
                              is_error=result.is_error)


class Runner(Protocol):
    runtime: str

    async def run(self, instance: HarnessInstance) -> RunResult: ...


class RunnerRegistry:
    """Runtime id -> runner class, imported on first use so the frameworks load only for runs that need them."""
    _modules: ClassVar[dict[str, str]] = {
        "langgraph-prebuilt": "harness_factory.runners.langgraph_prebuilt:LangGraphPrebuiltRunner",
        "llamaindex-agent": "harness_factory.runners.llamaindex_agent:LlamaIndexAgentRunner",
        "ms-agent-framework": "harness_factory.runners.ms_agent_framework:MsAgentFrameworkRunner",
        "autogen-agentchat": "harness_factory.runners.autogen_agentchat:AutoGenAgentChatRunner",
    }

    @classmethod
    def runtimes(cls) -> list[str]:
        return list(cls._modules)

    @classmethod
    def get(cls, runtime: str) -> Runner:
        if runtime not in cls._modules:
            raise KeyError(f"no runner for runtime {runtime!r}; known: {cls.runtimes()}")
        module, _, name = cls._modules[runtime].partition(":")
        return getattr(importlib.import_module(module), name)()


def harness_for(recipe: HarnessRecipe):
    """What executes a run of this recipe: its framework runner, or the native shape for its variant."""
    if recipe.runner is not None:
        return RunnerRegistry.get(recipe.runner)
    return ShapeRegistry.get(recipe.variant)
