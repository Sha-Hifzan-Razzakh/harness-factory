"""WorkflowGraphShape (V3, B4): fixed stages declared in the recipe, each a bounded tool loop.

    enter_stage -> call_model <-> run_tools
                       |
                   stage_done -> next stage | back to retry_from ("RETRY: ...") | END

Each stage sees only its own tools (recipe stage patterns AND the run's allowance), and the executor refuses
anything else, so "inspect before you act" is enforced rather than suggested. Stage boundaries are recorded
as handoff spans, so the trace shows the path the run took through the graph.
"""

from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from harness_factory.observability.span import SpanKind
from harness_factory.recipes.model import Variant
from harness_factory.shapes.base import HarnessInstance, ShapeRegistry
from harness_factory.shapes.common import DONE, finish, model_turn, run_tool_calls
from harness_factory.shapes.result import RunResult, Violation

RETRY_PREFIX = "RETRY:"          # exactly what prompts/stage/verify.j2 asks for; "Retrying is not needed" is not one


class FlowState(TypedDict):
    messages: list[dict[str, Any]]
    stage: int
    stage_steps: int
    retries: int
    reply: str | None          # the current stage's closing reply
    trail: list[str]           # stage ids in the order they ran
    stop: str | None
    output: str | None
    error: str | None


@ShapeRegistry.register(Variant.V3_WORKFLOW_GRAPH)
class WorkflowGraphShape:
    variant: Variant

    def _build(self, inst: HarnessInstance):
        wf = inst.recipe.workflow
        assert wf is not None, "V3 recipe without workflow (the validator should have refused it)"
        slot = inst.recipe.model_slots["executor"]
        stages = wf.stages
        last = len(stages) - 1

        def enter_stage(state: FlowState) -> dict:
            st = stages[state["stage"]]
            with inst.tracer.span(SpanKind.handoff, f"stage:{st.id}", stage=st.id, attempt=state["retries"]):
                pass
            prompt = {"role": "user", "content": inst.stage_prompts[st.id]}
            return {"messages": state["messages"] + [prompt], "stage_steps": 0, "reply": None,
                    "trail": state["trail"] + [st.id]}

        async def call_model(state: FlowState) -> dict:
            st = stages[state["stage"]]
            if state["stage_steps"] >= st.max_steps:
                # Stage cap: end the stage without a reply, and record it like a step cap in any other shape.
                inst.tools.violations.append(Violation(kind="stage_cap", detail=f"{st.id}: {st.max_steps} calls"))
                return {"reply": ""}
            tools = inst.tools.tools_for_model(restrict=st.allows) if st.tools else []
            turn = await model_turn(inst, slot, state["messages"], tools)
            if turn.stop:
                return {"messages": state["messages"] + turn.added, "stop": turn.stop, "error": turn.error}
            msg = turn.message
            update: dict[str, Any] = {"messages": state["messages"] + turn.added,
                                      "stage_steps": state["stage_steps"] + turn.calls}   # rejected replies count
            if not msg.get("tool_calls"):
                update["reply"] = msg.get("content") or ""
            return update

        async def run_tools(state: FlowState) -> dict:
            st = stages[state["stage"]]
            results, stop = await run_tool_calls(inst, state["messages"][-1], restrict=st.allows,
                                                 scope=f"stage:{st.id}")
            return {"messages": state["messages"] + results, "stop": stop}

        def stage_done(state: FlowState) -> dict:
            reply = state["reply"] or ""
            if state["stage"] < last:
                return {"stage": state["stage"] + 1}
            wants_retry = reply.lstrip().upper().startswith(RETRY_PREFIX)
            if wants_retry and wf.retry_from is not None and state["retries"] < wf.max_retries:
                return {"stage": wf.index(wf.retry_from), "retries": state["retries"] + 1}
            return {"stop": DONE, "output": reply}

        def after_model(state: FlowState) -> str:
            if state.get("stop"):
                return END
            return "stage_done" if state.get("reply") is not None else "run_tools"

        def after_tools(state: FlowState) -> str:
            return END if state.get("stop") else "call_model"

        def after_stage(state: FlowState) -> str:
            return END if state.get("stop") else "enter_stage"

        g = StateGraph(FlowState)
        g.add_node("enter_stage", enter_stage)
        g.add_node("call_model", call_model)
        g.add_node("run_tools", run_tools)
        g.add_node("stage_done", stage_done)
        g.add_edge(START, "enter_stage")
        g.add_edge("enter_stage", "call_model")
        g.add_conditional_edges("call_model", after_model,
                                {"run_tools": "run_tools", "stage_done": "stage_done", END: END})
        g.add_conditional_edges("run_tools", after_tools, {"call_model": "call_model", END: END})
        g.add_conditional_edges("stage_done", after_stage, {"enter_stage": "enter_stage", END: END})
        return g.compile()

    async def run(self, inst: HarnessInstance) -> RunResult:
        wf = inst.recipe.workflow
        per_pass = sum(st.max_steps * 2 + 4 for st in wf.stages)
        final: FlowState = await self._build(inst).ainvoke(
            {"messages": inst.messages, "stage": 0, "stage_steps": 0, "retries": 0, "reply": None, "trail": [],
             "stop": None, "output": None, "error": None},
            config={"recursion_limit": per_pass * (wf.max_retries + 1) + 10},
        )
        result = finish(inst, final["messages"], final.get("stop"), final.get("output"), final.get("error"))
        inst.snapshot["stage_trail"] = final["trail"]
        return result
