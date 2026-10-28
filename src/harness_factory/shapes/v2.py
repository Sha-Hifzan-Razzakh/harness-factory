"""ToolLoopShape (V2, B4): the model picks the next tool until it answers. Built as a LangGraph StateGraph.

call_model -> [tool calls?] -> run_tools -> call_model ... -> END
Budget is checked before every model call and every tool call.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from harness_factory.recipes.model import Variant
from harness_factory.shapes.base import HarnessInstance, ShapeRegistry
from harness_factory.shapes.result import RunResult, RunStatus
from harness_factory.substrate.errors import ErrorKind, HarnessError


class LoopState(TypedDict):
    messages: list[dict[str, Any]]
    stop: str | None          # done | budget:<limit> | error:<kind>
    output: str | None


@ShapeRegistry.register(Variant.V2_TOOL_LOOP)
class ToolLoopShape:
    variant: Variant

    def _build(self, inst: HarnessInstance):
        slot = inst.recipe.model_slots["executor"]
        tools = inst.tools.tools_for_model()

        async def call_model(state: LoopState) -> dict:
            limit = inst.budget.exceeded_by()
            if limit:
                return {"stop": f"budget:{limit}"}
            try:
                comp = await inst.gateway.complete(slot, state["messages"], tools)
            except HarnessError as e:
                return {"stop": f"error:{e.kind.value}", "output": e.message}
            inst.budget.used_steps += 1
            inst.budget.used_tokens += comp.tokens_in + comp.tokens_out
            msgs = state["messages"] + [comp.message]
            if comp.message.get("tool_calls"):
                return {"messages": msgs}
            return {"messages": msgs, "stop": "done", "output": comp.message.get("content") or ""}

        async def run_tools(state: LoopState) -> dict:
            calls = state["messages"][-1].get("tool_calls") or []
            msgs = list(state["messages"])
            for call in calls:
                limit = inst.budget.exceeded_by()
                if limit:
                    return {"messages": msgs, "stop": f"budget:{limit}"}
                result = await inst.tools.execute(call["function"]["name"], call["function"]["arguments"])
                msgs.append({"role": "tool", "tool_call_id": call["id"], "content": result.text})
            return {"messages": msgs}

        def after_model(state: LoopState) -> str:
            if state.get("stop"):
                return END
            return "run_tools"

        def after_tools(state: LoopState) -> str:
            return END if state.get("stop") else "call_model"

        g = StateGraph(LoopState)
        g.add_node("call_model", call_model)
        g.add_node("run_tools", run_tools)
        g.add_edge(START, "call_model")
        g.add_conditional_edges("call_model", after_model, {"run_tools": "run_tools", END: END})
        g.add_conditional_edges("run_tools", after_tools, {"call_model": "call_model", END: END})
        return g.compile()

    async def run(self, inst: HarnessInstance) -> RunResult:
        graph = self._build(inst)
        result = RunResult(run_key=inst.run_key)
        # The runner owns the root "run" span; everything here nests under it.
        final: LoopState = await graph.ainvoke(
            {"messages": inst.messages, "stop": None, "output": None},
            config={"recursion_limit": inst.recipe.config.max_steps * 2 + 10},
        )
        stop = final.get("stop") or "done"
        result.messages = final["messages"]
        result.output = final.get("output")
        if stop == "done":
            result.finished = True
            result.status = RunStatus.FAIL   # grading upgrades to SUCCESS or PARTIAL
        elif stop.startswith("budget:"):
            result.status, result.error = RunStatus.BUDGET_EXCEEDED, stop
        elif stop == f"error:{ErrorKind.QUOTA_EXHAUSTED.value}":
            result.status, result.error = RunStatus.INCOMPLETE, stop
        else:
            result.status, result.error = RunStatus.ERROR, f"{stop}: {final.get('output')}"
        result.violations = list(inst.tools.violations)
        result.ended_at = datetime.now(UTC)
        return result
