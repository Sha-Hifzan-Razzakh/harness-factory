"""OrchestratorShape (V5): a planner that hands each step to a worker agent.

    plan (planner slot, no tools) -> worker 1 -> worker 2 -> ... -> END
                                        |  "BLOCKED: ..." or the worker stopped
                                        +-> replan (remaining work) -> new workers ...

V4 with one difference: each step runs as a delegate call to the recipe's `delegation` child (EXT-09), so every
worker starts from a fresh conversation that holds only the overall task, what earlier workers reported, and its own
part (`planning.worker_prompt`). The orchestrator itself calls no tools. The delegate machinery supplies the rest:
workers run in the same sandbox through the same gateway, their grant is a subset of the orchestrator's, their
budget is capped by what the run has left and charged back to it, and each worker is a `delegate.run` tool span.
"""

from __future__ import annotations

import json
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from harness_factory.observability.span import SpanKind
from harness_factory.recipes.model import Variant
from harness_factory.shapes import delegate
from harness_factory.shapes.base import HarnessInstance, ShapeRegistry
from harness_factory.shapes.common import DONE, finish
from harness_factory.shapes.result import RunResult
from harness_factory.shapes.v4 import BLOCKED_PREFIX, Planner

FINISHED = "Helper finished. Its answer: "      # what delegate.run_delegate returns for a worker that finished


def worker_tools(inst: HarnessInstance) -> list[dict[str, Any]]:
    """The tools a worker will have (the orchestrator's allowance AND the worker recipe's slice): what the planner is
    told the team can use."""
    child = inst.delegate.child
    return inst.tools.tools_for_model(lambda r: child.tools.allows(r) and r.split(".")[0] in child.tools.mcp_servers)


class OrchestraState(TypedDict):
    steps: list[str]                   # the current plan's steps not yet run (the head is next)
    step_no: int
    replans: int
    done: list[dict[str, str]]         # {"step", "result"} for every finished step
    stop: str | None
    output: str | None
    error: str | None


@ShapeRegistry.register(Variant.V5_ORCHESTRATOR)
class OrchestratorShape:
    variant: Variant

    def _build(self, inst: HarnessInstance):
        pl = inst.recipe.planning
        assert pl is not None and pl.worker_prompt, "V5 recipe without planning.worker_prompt (validation refuses it)"
        assert inst.delegate is not None, "V5 recipe without a resolved delegation (validation refuses it)"
        planner = Planner(inst, worker_tools(inst))
        goal = inst.task.instruction                   # raw: the worker's own task template wraps it again

        async def plan(state: OrchestraState) -> dict:
            steps, update = await planner.plan()
            return update if steps is None else {"steps": steps}

        async def run_step(state: OrchestraState) -> dict:
            no, (current, *rest) = state["step_no"] + 1, state["steps"]
            with inst.tracer.span(SpanKind.handoff, f"step:{no}", step=no, replans=state["replans"]):
                pass
            # Out of budget for another worker: the run's own budget stop, whatever max_replans says (a refused
            # worker is not a blocked step: replanning could not run it either).
            limit = inst.budget.exceeded_by(before="tool_call") or delegate.shortfall(inst.delegate.child.config,
                                                                                      inst.budget)
            if limit:
                return {"stop": f"budget:{limit}", "step_no": no}
            sub = inst.prompts.render(pl.worker_prompt, goal=goal, done=state["done"], step=current,
                                      last=not rest)
            text, stop = await delegate.run_delegate(inst, json.dumps({"task": sub}), scope=f"step:{no}")
            if stop:                # the worker ended on infrastructure or a setup failure: so does the run
                return {"stop": stop, "step_no": no}
            answer = text.removeprefix(FINISHED) if text.startswith(FINISHED) else ""
            done = state["done"] + [{"step": current, "result": (answer or text)[:300]}]
            blocked = not text.startswith(FINISHED) or answer.lstrip().upper().startswith(BLOCKED_PREFIX)
            if blocked and state["replans"] < pl.max_replans:
                steps, update = await planner.replan(state["done"], done[-1])
                if steps is None:
                    return {**update, "done": done, "step_no": no}
                return {"steps": steps, "replans": state["replans"] + 1, "done": done, "step_no": no}
            if rest:
                return {"steps": rest, "done": done, "step_no": no}
            return {"steps": [], "done": done, "step_no": no, "stop": DONE, "output": answer}

        def after(state: OrchestraState) -> str:
            return END if state.get("stop") else "run_step"

        g = StateGraph(OrchestraState)
        g.add_node("plan", plan)
        g.add_node("run_step", run_step)
        g.add_edge(START, "plan")
        g.add_conditional_edges("plan", after, {"run_step": "run_step", END: END})
        g.add_conditional_edges("run_step", after, {"run_step": "run_step", END: END})
        return g.compile()

    async def run(self, inst: HarnessInstance) -> RunResult:
        pl = inst.recipe.planning
        final: OrchestraState = await self._build(inst).ainvoke(
            {"steps": [], "step_no": 0, "replans": 0, "done": [], "stop": None, "output": None, "error": None},
            config={"recursion_limit": (pl.max_plan_steps + 2) * (pl.max_replans + 1) + 10},
        )
        # The orchestrator's own conversation is its plans; each worker's is in snapshot.delegations.
        messages = [{"role": "assistant", "content": "\n".join(f"{i}. {s}" for i, s in enumerate(p["steps"], 1))}
                    for p in inst.snapshot.get("plans", [])]
        result = finish(inst, inst.messages + messages, final.get("stop"), final.get("output"), final.get("error"))
        inst.snapshot["plan_trail"] = final["done"]
        inst.snapshot["replans"] = final["replans"]
        return result
