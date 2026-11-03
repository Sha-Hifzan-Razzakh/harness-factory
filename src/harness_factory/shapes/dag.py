"""V5 with structured plans: workers run as a dependency graph, the ready ones concurrently.

    plan (JSON, validated) -> ready steps start, at most `planning.max_parallel` at a time
                              a step is ready when every step it depends on has finished
                              "BLOCKED: ...", a stopped or failed worker -> no new starts; once the workers
                              still running have finished -> replan (remaining work) -> new graph ...

Everything else is V5 (ADR 0008): each step is a delegate call to the recipe's `delegation` child, with the worker
prompt as its task. What concurrency changes, and how it stays exact and reproducible:

  - Budget. A worker's whole budget is carved from what the run has left before it starts (delegate.capped) and held
    until its usage is charged back (BudgetGuard.reserve), so workers running at the same time can together never
    use more steps, tool calls or tokens than the run has. A ready step that can't get a budget waits for a running
    worker to give its share back; with none running, the run stops on its budget (as sequential V5 does).
  - Order. Steps are numbered by their position in the plan (`step:<n>`), not by when they started; ready steps
    start in plan order; results are handled, recorded (`snapshot.plan_trail`, `snapshot.delegations`) and shown to
    later workers in step order, never in the order they finished. A worker is shown what the steps it depends on
    (directly or not) reported, so its prompt doesn't depend on which unrelated sibling finished first.
  - Replans happen only when no worker is running, so the planner sees every result.
  - Endings: a worker that ends on infrastructure or a setup failure (a delegate stop, see delegate.py) ends the run
    the same way once its siblings have finished; the output is the answer of the plan's last step.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from harness_factory.observability.span import SpanKind
from harness_factory.shapes import delegate, plans
from harness_factory.shapes.base import HarnessInstance
from harness_factory.shapes.common import DONE, finish
from harness_factory.shapes.result import RunResult
from harness_factory.shapes.v4 import BLOCKED_PREFIX, Planner
from harness_factory.shapes.v5 import FINISHED, worker_tools
from harness_factory.substrate.errors import ErrorKind


def shortfall(inst: HarnessInstance) -> str | None:
    """The run limit that leaves no room for another worker now ("max_tool_calls 40"), or None."""
    limit = inst.budget.exceeded_by(before="tool_call")
    if limit:
        return limit
    child, left, lim = inst.delegate.child.config, inst.budget.remaining(), inst.budget.limits
    if min(child.max_steps, left["steps"]) <= 0:
        return f"max_steps {lim.max_steps}"
    if min(child.max_tool_calls, left["tool_calls"] - 1) <= 0:
        return f"max_tool_calls {lim.max_tool_calls}"
    if min(child.max_tokens, left["tokens"]) <= 0:
        return f"max_tokens {lim.max_tokens}"
    return None


class Orchestra:
    def __init__(self, inst: HarnessInstance) -> None:
        self.inst = inst
        self.pl = inst.recipe.planning
        self.planner = Planner(inst, worker_tools(inst))
        self.goal = inst.task.instruction
        self.by_id: dict[str, plans.PlanStep] = {}       # every step planned in this run
        self.no: dict[str, int] = {}                     # step id -> step number
        self.entries: dict[str, dict[str, Any]] = {}     # step id -> {"no", "step", "result", "blocked"}
        self.answers: dict[str, str] = {}
        self.replans = 0
        self.last_no = 0

    def _adopt(self) -> list[plans.PlanStep]:
        graph = list(self.planner.graph)
        base = self.last_no
        for i, s in enumerate(graph, 1):
            self.by_id[s.id], self.no[s.id] = s, base + i
        return graph

    def _start(self, step: plans.PlanStep, last: bool) -> asyncio.Task:
        inst, no = self.inst, self.no[step.id]
        self.last_no = max(self.last_no, no)
        with inst.tracer.span(SpanKind.handoff, f"step:{no}", step=no, step_id=step.id, replans=self.replans):
            pass
        deps = plans.closure(step, self.by_id)
        done = [{"step": e["step"], "result": e["result"]}
                for e in sorted((self.entries[i] for i in deps if i in self.entries), key=lambda e: e["no"])]
        sub = inst.prompts.render(self.pl.worker_prompt, goal=self.goal, done=done, step=step.text(), last=last)

        async def work() -> tuple[str, str | None]:
            res = await delegate.run_delegate(inst, json.dumps({"task": sub}), scope=f"step:{no}")
            return res if isinstance(res, tuple) else (res, None)     # (text, stop) once delegate reports stops

        return asyncio.create_task(work(), name=f"{inst.scope or 'run'}/step:{no}")

    async def _held(self, task: asyncio.Task, calls_before: int) -> None:
        """Let a worker just started carve and hold its budget before the next one is considered (it does so before
        its first await). Exactness never depends on this: each worker carves from what is actually left."""
        for _ in range(1000):                            # bounded: it takes one pass unless that changes
            if task.done() or self.inst.delegate.limits.calls > calls_before:
                return
            await asyncio.sleep(0)

    async def run(self) -> RunResult:
        inst = self.inst
        steps, update = await self.planner.plan()
        stop, error, output = update.get("stop"), update.get("error"), None
        if steps is not None:
            stop, error, output = await self._execute()
        inst.snapshot.setdefault("delegations", []).sort(key=lambda d: d["call"])
        messages = [{"role": "assistant", "content": "\n".join(f"{i}. {s}" for i, s in enumerate(p["steps"], 1))}
                    for p in inst.snapshot.get("plans", [])]
        result = finish(inst, inst.messages + messages, stop, output, error)
        inst.snapshot["plan_trail"] = [{"step": e["step"], "result": e["result"]}
                                       for e in sorted(self.entries.values(), key=lambda e: e["no"])]
        inst.snapshot["replans"] = self.replans
        return result

    async def _execute(self) -> tuple[str | None, str | None, Any]:
        graph = self._adopt()
        pending, passed = list(graph), set()             # passed: finished, or blocked with no replan left
        running: dict[asyncio.Task, plans.PlanStep] = {}
        blocked: list[plans.PlanStep] = []
        stop: str | None = None
        error: str | None = None
        try:
            while True:
                while stop is None and not blocked and len(running) < self.pl.parallel():
                    ready = next((s for s in pending if set(s.depends_on) <= passed), None)
                    if ready is None:
                        break
                    limit = shortfall(self.inst)
                    if limit:
                        if running:
                            break                        # wait for a running worker to hand its share back
                        stop = f"budget:{limit}"
                        break
                    pending.remove(ready)
                    before = self.inst.delegate.limits.calls
                    task = self._start(ready, last=ready is graph[-1])
                    running[task] = ready
                    await self._held(task, before)
                if not running:
                    if stop:
                        break
                    if blocked:
                        if self.replans < self.pl.max_replans:
                            done = [e for e in sorted(self.entries.values(), key=lambda e: e["no"])
                                    if not e["blocked"]]
                            stuck = [self.entries[s.id] for s in sorted(blocked, key=lambda s: self.no[s.id])]
                            steps, update = await self.planner.replan(
                                [{"step": e["step"], "result": e["result"]} for e in done],
                                [{"step": e["step"], "result": e["result"]} for e in stuck])
                            if steps is None:
                                return update.get("stop"), update.get("error"), None
                            self.replans += 1
                            graph = self._adopt()
                            pending, blocked = list(graph), []
                            continue
                        passed |= {s.id for s in blocked}    # no replan left: the run goes on, as in V5
                        blocked = []
                        continue
                    if not pending:
                        return DONE, None, self.answers.get(graph[-1].id, "")
                    # Unreachable for a validated plan (its order puts every dependency first); never spin.
                    return f"error:{ErrorKind.CONFIG.value}", f"no step of the plan can start: {pending[0].id}", None
                finished, _ = await asyncio.wait(running, return_when=asyncio.FIRST_COMPLETED)
                for task in sorted(finished, key=lambda t: self.no[running[t].id]):
                    step = running.pop(task)
                    text, child_stop = task.result()
                    answer = text.removeprefix(FINISHED) if text.startswith(FINISHED) else ""
                    self.answers[step.id] = answer
                    is_blocked = not text.startswith(FINISHED) or answer.lstrip().upper().startswith(BLOCKED_PREFIX)
                    self.entries[step.id] = {"no": self.no[step.id], "step": step.text(),
                                             "result": (answer or text)[:300], "blocked": is_blocked}
                    if child_stop and stop is None:      # infrastructure or setup: the run ends the same way
                        stop, error = child_stop, getattr(self.inst, "stop_error", None)
                    elif is_blocked:
                        blocked.append(step)
                    else:
                        passed.add(step.id)
            return stop, error, None
        finally:
            if running:                                  # an exception or a cancel: never leave a worker behind
                for task in running:
                    task.cancel()
                await asyncio.gather(*running, return_exceptions=True)

