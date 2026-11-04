"""`langgraph-supervisor` runner: LangGraph's supervisor over one worker agent (docs/adr/0016-runner-axis.md, #56).

Built from the framework's own components, at their defaults:
- the team: `langgraph_supervisor.create_supervisor` (langgraph-supervisor 0.0.31). It builds the supervisor agent
  with LangGraph's prebuilt `create_react_agent` (the library still uses it; LangGraph v1 deprecates it), gives it
  one handoff tool per worker (`transfer_to_<name>`, a `ToolNode` tool that returns a `Command` to the worker node),
  and wires each worker as a node that returns to the supervisor. Defaults: `output_mode="last_message"` (the
  supervisor sees only the worker's last message), handoff messages on, no parallel handoffs, no supervisor tools;
- the worker: `langchain.agents.create_agent` with the run's MCP tools (`langgraph_prebuilt.load_tools`, i.e.
  `langchain.mcp.as_langchain_tool` over ExecutorSession, measurement point b), exactly as the `langgraph-prebuilt`
  runner builds its agent, plus the name "worker" the supervisor requires;
- the model client: `langgraph_prebuilt.GatewayChatModel`, one instance per agent, both calling the same
  RunnerContext (measurement point a).

The recipe shape is V2 (the tool loop), not V5. A V5 recipe's planner prompt, worker prompt, delegation child and
replan rule are the native orchestrator's control flow; running them inside the supervisor would label the factory's
loop with the framework's name (RUN-02). The supervisor pattern is the framework's own answer to "who does the work":
a V2 recipe gives it the task, the system prompt, the tools and the budget, and the framework splits the work.

What the framework adds to the conversation:
- prompt text: none of its own. The recipe's system message is the prompt of both agents (it is the harness's one
  system message), its task message the one input message;
- the supervisor's tool: `transfer_to_worker`, described "Ask agent 'worker' for help", and the messages around a
  handoff ("Successfully transferred to worker", "Transferring back to supervisor", "Successfully transferred back to
  supervisor"), plus the agents' names on their messages (`name`);
- one more model call per handoff (the supervisor's) and the supervisor's final answer after the worker's.

The handoff tool is the framework's control flow, not an environment tool: it moves control between graph nodes and
touches nothing, so it is not a ToolExecutor call (no tool_call span, not counted by max_tool_calls). Every model call
it causes is a gateway call, counted by max_steps.
"""

from __future__ import annotations

import warnings

from langchain_core.messages import AIMessage, HumanMessage

from harness_factory.runners.base import ExecutorSession, RunnerContext, RunnerStop
from harness_factory.runners.langgraph_prebuilt import GatewayChatModel, load_tools
from harness_factory.shapes.base import HarnessInstance
from harness_factory.shapes.result import RunResult

WORKER = "worker"                    # the worker agent's name: its node, and the handoff tool `transfer_to_worker`


def build_team(ctx: RunnerContext, tools: list, system: str):
    """The framework's supervisor over one worker, compiled with LangGraph's defaults."""
    from langchain.agents import create_agent

    model = ctx.slot.canonical_model or ""
    worker = create_agent(GatewayChatModel(ctx=ctx, model_name=model), tools, system_prompt=system, name=WORKER)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")      # LangGraphDeprecatedSinceV10: the library builds create_react_agent
        from langgraph_supervisor import create_supervisor
        team =create_supervisor([worker], model=GatewayChatModel(ctx=ctx, model_name=model), prompt=system)
    return team.compile(), worker


class LangGraphSupervisorRunner:
    runtime = "langgraph-supervisor"

    async def run(self, inst: HarnessInstance) -> RunResult:
        from langgraph.errors import GraphRecursionError

        ctx = RunnerContext(inst, inst.recipe.model_slots["executor"])
        system, task = ctx.system_and_task()
        team, worker = build_team(ctx, await load_tools(ExecutorSession(ctx)), system)
        inst.snapshot["runner"] = {"runtime": self.runtime, "agent": type(team).__name__,
                                   "nodes": sorted(n for n in team.nodes if not n.startswith("__")),
                                   "worker_nodes": sorted(n for n in worker.nodes if not n.startswith("__"))}
        try:
            state = await team.ainvoke({"messages": [HumanMessage(task)]})
        except RunnerStop:
            return ctx.finish()
        except GraphRecursionError as e:     # the framework's own step bound: a harness stop
            ctx.halt("budget:framework recursion_limit", str(e)[:300])
            return ctx.finish()
        if ctx.stop is not None:
            return ctx.finish()
        final = state["messages"][-1]
        return ctx.finish(final.text if isinstance(final, AIMessage) else str(final.content))
