"""ToolLoopShape (V2, B4): the model picks the next tool until it answers. Built as a LangGraph StateGraph.

call_model -> [tool calls?] -> run_tools -> call_model ... -> END
Budget is checked before every model call and every tool call.
"""

from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from harness_factory.recipes.model import Variant
from harness_factory.shapes.base import HarnessInstance, ShapeRegistry
from harness_factory.shapes.common import DONE, finish, model_turn, run_tool_calls
from harness_factory.shapes.result import RunResult


class LoopState(TypedDict):
    messages: list[dict[str, Any]]
    stop: str | None          # done | budget:<limit> | error:<kind>
    output: str | None
    error: str | None


@ShapeRegistry.register(Variant.V2_TOOL_LOOP)
class ToolLoopShape:
    variant: Variant

    def _build(self, inst: HarnessInstance):
        slot = inst.recipe.model_slots["executor"]
        tools = inst.tools.tools_for_model()

        async def call_model(state: LoopState) -> dict:
            turn = await model_turn(inst, slot, state["messages"], tools)
            msgs = state["messages"] + turn.added
            if turn.stop:
                return {"messages": msgs, "stop": turn.stop, "error": turn.error}
            msg = turn.message
            if msg.get("tool_calls"):
                return {"messages": msgs}
            return {"messages": msgs, "stop": DONE, "output": msg.get("content") or ""}

        async def run_tools(state: LoopState) -> dict:
            results, stop = await run_tool_calls(inst, state["messages"][-1])
            return {"messages": state["messages"] + results, "stop": stop}

        def after(state: LoopState) -> str:
            return END if state.get("stop") else "next"

        g = StateGraph(LoopState)
        g.add_node("call_model", call_model)
        g.add_node("run_tools", run_tools)
        g.add_edge(START, "call_model")
        g.add_conditional_edges("call_model", after, {"next": "run_tools", END: END})
        g.add_conditional_edges("run_tools", after, {"next": "call_model", END: END})
        return g.compile()

    async def run(self, inst: HarnessInstance) -> RunResult:
        # The runner owns the root "run" span; everything here nests under it.
        final: LoopState = await self._build(inst).ainvoke(
            {"messages": inst.messages, "stop": None, "output": None, "error": None},
            config={"recursion_limit": inst.recipe.config.max_steps * 2 + 10},
        )
        return finish(inst, final["messages"], final.get("stop"), final.get("output"), final.get("error"))
