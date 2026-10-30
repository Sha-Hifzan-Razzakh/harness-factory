"""PlannerShape (V4): plan first, then carry the plan out one step at a time.

    plan (planner slot, no tools) -> step 1 -> step 2 -> ... -> END
                                       |  "BLOCKED: ..."
                                       +-> replan (remaining work) -> new steps ...

The planner sees the task and the tool list (names and descriptions) but calls nothing. Each step is a bounded tool
loop of the executor, which keeps one conversation across steps, so it sees what earlier steps found. A step that
replies "BLOCKED: ..." hands the remaining work back to the planner, at most `planning.max_replans` times; the new
plan replaces the steps not yet run. Model turns, tool batches and the stop-to-status mapping are the shared ones
(shapes/common.py), so V4 differs from V2 and V3 only in control flow.
"""

from __future__ import annotations

import re
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from harness_factory.hashing import content_hash
from harness_factory.observability.span import SpanKind
from harness_factory.recipes.model import ModelSlot, Variant
from harness_factory.shapes.base import HarnessInstance, ShapeRegistry
from harness_factory.shapes.common import DONE, finish, model_turn, offered_tools, run_tool_calls
from harness_factory.shapes.result import RunResult, Violation

BLOCKED_PREFIX = "BLOCKED:"      # exactly what prompts/plan/step.j2 asks for
_STEP_LINE = re.compile(r"^\s*(?:step\s*)?(\d+)\s*[.):-]\s*(.+?)\s*$", re.IGNORECASE)


def parse_plan(text: str, max_steps: int) -> list[str]:
    """Numbered lines ("1. ...", "2) ...", "Step 3: ...") in order. A longer plan keeps its first max_steps - 1
    steps and merges the rest into the last one, so no planned work is silently dropped."""
    steps = [m.group(2) for line in (text or "").splitlines() if (m := _STEP_LINE.match(line))]
    if len(steps) > max_steps:
        steps = steps[:max_steps - 1] + ["; then ".join(steps[max_steps - 1:])]
    return steps


def planner_slot(inst: HarnessInstance) -> ModelSlot:
    """The planner slot, on the run's model (validation keeps planner models unpinned); the executor slot when the
    recipe has none."""
    executor = inst.recipe.model_slots["executor"]
    planner = inst.recipe.model_slots.get("planner")
    if planner is None:
        return executor
    return planner.model_copy(update={"canonical_model": executor.canonical_model, "seed": executor.seed})


class Planner:
    """The planner side of V4 and V5: one system prompt (the task's tools, by name and description), a plan call and
    replan calls, each a model turn of the planner slot with no tools offered."""

    def __init__(self, inst: HarnessInstance, tools: list[dict[str, Any]]) -> None:
        self.inst = inst
        self.pl = inst.recipe.planning
        self.slot = planner_slot(inst)
        self.system = inst.prompts.render(self.pl.planner_prompt, max_steps=self.pl.max_plan_steps,
                                          tools=[{"name": t["function"]["name"],
                                                  "description": t["function"]["description"]} for t in tools])
        self.task_messages = [m for m in inst.messages if m.get("role") != "system"]
        inst.snapshot["planner_prompt_hash"] = content_hash([self.system])
        inst.snapshot["plans"] = []

    async def plan(self) -> tuple[list[str] | None, dict]:
        return await self._ask([], "plan")

    async def replan(self, done: list[dict[str, str]], blocked: dict[str, str]) -> tuple[list[str] | None, dict]:
        text = self.inst.prompts.render(self.pl.replan_prompt, done=done, blocked=blocked,
                                        max_steps=self.pl.max_plan_steps)
        return await self._ask([{"role": "user", "content": text}], "replan")

    def current(self) -> list[str]:
        return self.inst.snapshot["plans"][-1]["steps"]

    async def _ask(self, extra: list[dict[str, Any]], kind: str) -> tuple[list[str] | None, dict]:
        """Returns (steps, state update); steps is None when the run stops."""
        inst = self.inst
        with inst.tracer.span(SpanKind.handoff, kind, slot=self.slot.role):
            pass
        turn = await model_turn(inst, self.slot, [{"role": "system", "content": self.system}, *self.task_messages,
                                                  *extra], [])
        if turn.stop:
            return None, {"stop": turn.stop, "error": turn.error}
        text = (turn.message or {}).get("content") or ""
        steps = parse_plan(text, self.pl.max_plan_steps)
        if not steps:
            # Nothing numbered: carry on with the whole task as one step, and record it.
            inst.tools.violations.append(Violation(kind="plan_unparsed", detail=text[:200]))
            steps = ["Complete the task."]
        inst.snapshot["plans"].append({"kind": kind, "steps": steps})
        return steps, {}


class PlanState(TypedDict):
    messages: list[dict[str, Any]]     # the executor's conversation
    steps: list[str]                   # the current plan's steps not yet run (the head is running)
    shown: bool                        # the executor has been shown the current plan
    step_no: int                       # steps run so far, across replans (1-based once one starts)
    step_calls: int
    replans: int
    reply: str | None                  # the current step's closing reply
    done: list[dict[str, str]]         # {"step", "result"} for every finished step
    stop: str | None
    output: str | None
    error: str | None


@ShapeRegistry.register(Variant.V4_PLANNER)
class PlannerShape:
    variant: Variant

    def _build(self, inst: HarnessInstance):
        pl = inst.recipe.planning
        assert pl is not None, "V4 recipe without planning (the validator should have refused it)"
        assert inst.prompts is not None, "V4 needs the prompt registry on the instance"
        executor = inst.recipe.model_slots["executor"]
        tools = offered_tools(inst)
        names = [t["function"]["name"] for t in tools]
        planner = Planner(inst, tools)

        async def plan(state: PlanState) -> dict:
            steps, update = await planner.plan()
            return update if steps is None else {"steps": steps, "shown": False}

        def enter_step(state: PlanState) -> dict:
            no = state["step_no"] + 1
            remaining = state["steps"]
            with inst.tracer.span(SpanKind.handoff, f"step:{no}", step=no, replans=state["replans"]):
                pass
            total = len(planner.current())
            index = total - len(remaining) + 1
            prompt = inst.prompts.render(pl.step_prompt, plan=[] if state["shown"] else planner.current(),
                                         index=index, total=total, step=remaining[0], last=len(remaining) == 1,
                                         tools=names)
            return {"messages": state["messages"] + [{"role": "user", "content": prompt}], "step_no": no,
                    "step_calls": 0, "reply": None, "shown": True}

        async def call_model(state: PlanState) -> dict:
            if state["step_calls"] >= pl.step_max_steps:
                inst.tools.violations.append(Violation(kind="step_cap",
                                                       detail=f"step {state['step_no']}: {pl.step_max_steps} calls"))
                return {"reply": ""}
            turn = await model_turn(inst, executor, state["messages"], tools)
            if turn.stop:
                return {"messages": state["messages"] + turn.added, "stop": turn.stop, "error": turn.error}
            msg = turn.message
            update: dict[str, Any] = {"messages": state["messages"] + turn.added,
                                      "step_calls": state["step_calls"] + turn.calls}     # rejected replies count
            if not msg.get("tool_calls"):
                update["reply"] = msg.get("content") or ""
            return update

        async def run_tools(state: PlanState) -> dict:
            results, stop = await run_tool_calls(inst, state["messages"][-1], scope=f"step:{state['step_no']}")
            return {"messages": state["messages"] + results, "stop": stop}

        async def step_done(state: PlanState) -> dict:
            reply, (current, *rest) = state["reply"] or "", state["steps"]
            done = state["done"] + [{"step": current, "result": reply[:300]}]
            blocked = reply.lstrip().upper().startswith(BLOCKED_PREFIX)
            if blocked and state["replans"] < pl.max_replans:
                steps, update = await planner.replan(state["done"], done[-1])
                if steps is None:
                    return {**update, "done": done}
                return {"steps": steps, "shown": False, "replans": state["replans"] + 1, "done": done}
            if rest:
                return {"steps": rest, "done": done}
            return {"steps": [], "done": done, "stop": DONE, "output": reply}

        def after_plan(state: PlanState) -> str:
            return END if state.get("stop") else "enter_step"

        def after_model(state: PlanState) -> str:
            if state.get("stop"):
                return END
            return "step_done" if state.get("reply") is not None else "run_tools"

        def after_tools(state: PlanState) -> str:
            return END if state.get("stop") else "call_model"

        g = StateGraph(PlanState)
        g.add_node("plan", plan)
        g.add_node("enter_step", enter_step)
        g.add_node("call_model", call_model)
        g.add_node("run_tools", run_tools)
        g.add_node("step_done", step_done)
        g.add_edge(START, "plan")
        g.add_conditional_edges("plan", after_plan, {"enter_step": "enter_step", END: END})
        g.add_edge("enter_step", "call_model")
        g.add_conditional_edges("call_model", after_model,
                                {"run_tools": "run_tools", "step_done": "step_done", END: END})
        g.add_conditional_edges("run_tools", after_tools, {"call_model": "call_model", END: END})
        g.add_conditional_edges("step_done", after_plan, {"enter_step": "enter_step", END: END})
        return g.compile()

    async def run(self, inst: HarnessInstance) -> RunResult:
        pl = inst.recipe.planning
        per_plan = 2 + pl.max_plan_steps * (pl.step_max_steps * 2 + 4)
        final: PlanState = await self._build(inst).ainvoke(
            {"messages": inst.messages, "steps": [], "shown": False, "step_no": 0, "step_calls": 0, "replans": 0,
             "reply": None, "done": [], "stop": None, "output": None, "error": None},
            config={"recursion_limit": per_plan * (pl.max_replans + 1) + 10},
        )
        result = finish(inst, final["messages"], final.get("stop"), final.get("output"), final.get("error"))
        inst.snapshot["plan_trail"] = final["done"]
        inst.snapshot["replans"] = final["replans"]
        return result
