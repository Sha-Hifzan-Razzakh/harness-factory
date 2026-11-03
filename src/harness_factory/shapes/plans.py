"""Structured plans (SUB-08, SHP-04): the planner's JSON reply, validated before any step runs.

    {"steps": [{"id": "s1", "instruction": "...", "tools": ["fs__read_file"], "depends_on": [], "done_when": "..."}]}

A plan is valid when it parses, has 1..max_plan_steps steps with unique ids, names only tools the team has, and its
`depends_on` form an acyclic graph over its own ids and the ids of steps already done (a replan may build on them; it
may not reuse them). The steps then run in a fixed topological order: of the steps whose dependencies are met, the
first in the planner's order goes next. V4 runs them one after another in that order; V5 runs the ready ones
concurrently (`planning.max_parallel`). A plan that doesn't validate is a malformed output: the planner is told
exactly why and asked again (shapes/v4.Planner), never silently repaired.
"""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

JSON_MODE = {"type": "json_object"}
_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL | re.IGNORECASE)


class PlanStep(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = Field(min_length=1, max_length=40)
    instruction: str = Field(min_length=1)
    tools: list[str] = Field(default_factory=list)          # tool hints (the names the planner was shown)
    depends_on: list[str] = Field(default_factory=list)
    done_when: str = Field(min_length=1)

    @field_validator("id", "instruction", "done_when")
    @classmethod
    def _stripped(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("must not be blank")
        return v

    def text(self) -> str:
        """One line, as the step prompt, the worker prompt and the plan trail show it: "[s2] Read x (after s1;
        tools: fs__read_file; done when: its text is known)". The id leads, so later steps can refer to it."""
        parts = ([f"after {', '.join(self.depends_on)}"] if self.depends_on else []) + \
                ([f"tools: {', '.join(self.tools)}"] if self.tools else []) + [f"done when: {self.done_when}"]
        return f"[{self.id}] {self.instruction} ({'; '.join(parts)})"


class StructuredPlan(BaseModel):
    model_config = ConfigDict(extra="ignore")

    steps: list[PlanStep] = Field(min_length=1)


class PlanInvalid(ValueError):
    """Why a reply is not a valid plan, in words the planner can act on."""


def _load(text: str) -> Any:
    raw = (text or "").strip()
    m = _FENCE.match(raw)
    if m:
        raw = m.group(1)
    try:
        return json.loads(raw)
    except ValueError:
        start, end = raw.find("{"), raw.rfind("}")      # prose around one JSON object
        if 0 <= start < end:
            try:
                return json.loads(raw[start:end + 1])
            except ValueError:
                pass
    raise PlanInvalid("the reply is not a JSON object")


def _errors(e: ValidationError) -> str:
    return "; ".join(f"{'.'.join(str(p) for p in err['loc']) or 'plan'}: {err['msg']}" for err in e.errors()[:5])


def parse(text: str, max_steps: int, tools: list[str] | None = None, done: set[str] | frozenset = frozenset()
          ) -> list[PlanStep]:
    """The plan's steps in run order (see `order`). Raises PlanInvalid. `tools`: the names the planner was shown
    (hints may also use the dotted form, "fs.read_file", and are stored as shown); `done`: ids of steps already
    done in this run."""
    data = _load(text)
    if isinstance(data, list):                          # a bare list of steps is the same plan
        data = {"steps": data}
    try:
        plan = StructuredPlan.model_validate(data)
    except ValidationError as e:
        raise PlanInvalid(_errors(e)) from None
    steps = plan.steps
    if len(steps) > max_steps:
        raise PlanInvalid(f"{len(steps)} steps; at most {max_steps}")
    ids = [s.id for s in steps]
    dup = sorted({i for i in ids if ids.count(i) > 1})
    if dup:
        raise PlanInvalid(f"duplicate step ids: {', '.join(dup)}")
    reused = sorted(set(ids) & set(done))
    if reused:
        raise PlanInvalid(f"ids already used by finished steps: {', '.join(reused)}")
    if tools:
        shown = {t: t for t in tools} | {t.replace("__", ".", 1): t for t in tools}
        for s in steps:
            unknown = [t for t in s.tools if t not in shown]
            if unknown:
                raise PlanInvalid(f"step {s.id}: unknown tools {', '.join(unknown)}; the tools are {', '.join(tools)}")
            s.tools = [shown[t] for t in s.tools]
    known = set(ids) | set(done)
    for s in steps:
        if s.id in s.depends_on:
            raise PlanInvalid(f"step {s.id} depends on itself")
        missing = [d for d in s.depends_on if d not in known]
        if missing:
            raise PlanInvalid(f"step {s.id} depends on unknown steps: {', '.join(missing)}")
    return order(steps, done)


def order(steps: list[PlanStep], done: set[str] | frozenset = frozenset()) -> list[PlanStep]:
    """Topological order, deterministic: repeatedly the first step (in the planner's order) whose dependencies are
    done or already placed. Raises PlanInvalid on a cycle."""
    placed, out, todo = set(done), [], list(steps)
    while todo:
        nxt = next((s for s in todo if set(s.depends_on) <= placed), None)
        if nxt is None:
            raise PlanInvalid(f"dependency cycle among steps {', '.join(s.id for s in todo)}")
        todo.remove(nxt)
        out.append(nxt)
        placed.add(nxt.id)
    return out


def closure(step: PlanStep, by_id: dict[str, PlanStep]) -> set[str]:
    """Ids of every step this one depends on, directly or not."""
    seen: set[str] = set()
    stack = list(step.depends_on)
    while stack:
        i = stack.pop()
        if i in seen:
            continue
        seen.add(i)
        if i in by_id:
            stack += by_id[i].depends_on
    return seen
