"""promptfoo prompt smoke checks (SKP-03): render the factory's real prompts for promptfoo to check.

`generate_tests(config)` is promptfoo's test generator. It builds one case per (recipe x task x part); a part is
one point where the harness sends a prompt: `messages` (system + task, every shape), `stage:<id>` (V3), `planner`,
`step:first`, `step:last`, `replan` (V4), `worker:first`, `worker:last` (V5). Each case carries two vars:

- `messages`: the chat messages the harness sends at that part, rendered by the real PromptAssembler /
  PromptRegistry from prompts/ (no template is copied here). render.js hands them to promptfoo as the prompt.
- `expect`: what that prompt must say, worked out from the recipe, the task and the tool catalog with the
  harness's own helpers (allowed_tools, model_name, the delegate context, the shapes' RETRY:/BLOCKED: constants).
  checks.js compares the prompt promptfoo sent against it.

Rendering happens once, in this one Python process; the checks run in promptfoo's Node process. (promptfoo starts
a new Python process for every Python prompt or assertion call, which made a Python check per case far slower.)

Canary cases (canaries.yaml) render a deliberately broken copy of one template (in memory: prompts/ is never
written) and must fail the check they target, so a check that stops biting fails the eval.

Needs the project environment: run promptfoo under `uv run` (see README, "Prompt smoke checks").
"""

from __future__ import annotations

import asyncio
import importlib
import json
import re
from functools import cache
from typing import Any

try:
    from jinja2 import ChoiceLoader, DictLoader

    from harness_factory import settings
    from harness_factory.env.mcp.servers import SideEffect, load_tool_catalog
    from harness_factory.provision.provisioner import allowed_tools
    from harness_factory.recipes.model import HarnessRecipe, Variant, load_recipes
    from harness_factory.shapes import delegate as delegation
    from harness_factory.shapes.v3 import RETRY_PREFIX
    from harness_factory.shapes.v4 import BLOCKED_PREFIX
    from harness_factory.substrate.prompts import PromptAssembler, PromptRegistry
    from harness_factory.substrate.tools import model_name, tool_ref
    from harness_factory.tasks.model import Task
    from harness_factory.tasks.registry import TaskRegistry
except ImportError as e:              # promptfoo started with a Python that doesn't have the project installed
    raise ImportError(f"{e}: run promptfoo under `uv run` so it uses the project's Python "
                      f"(uv run npx promptfoo@<version> eval -c promptfoo/promptfooconfig.yaml)") from e

# A fixed plan and worker reports for the V4/V5 parts: in a run they come from the planner model.
SAMPLE_PLAN = ["Look at what the task refers to.", "Make the change the task asks for.", "Check the result."]
SAMPLE_DONE = [{"step": SAMPLE_PLAN[0], "result": "found what the task refers to"}]
SAMPLE_BLOCKED = {"step": SAMPLE_PLAN[1], "result": "BLOCKED: the target was not where the plan expected it"}

DEFAULT_TASKS = ["fs-01", "sql-01", "mix-03"]
DEFAULT_BUDGETS = {"message_chars": 2500, "skills_message_chars": 8000, "total_chars": 10000}


# ---------------------------------------------------------------------------------------------------- catalog

@cache
def _recipes() -> dict[str, HarnessRecipe]:
    return load_recipes(settings.RECIPES_DIR)


@cache
def _tasks() -> TaskRegistry:
    return TaskRegistry()


@cache
def _declared():
    return load_tool_catalog(settings.TOOLS_FILE)


@cache
def _descriptions() -> dict[str, str]:
    """Tool ref -> description, discovered in process from each local MCP server module (what the pool discovers
    over stdio, without starting processes or a sandbox). Remote servers are skipped."""
    out: dict[str, str] = {}
    for s in _declared().servers:
        if s.transport != "stdio" or not s.module:
            continue
        app = importlib.import_module(s.module).app
        for t in asyncio.run(app.list_tools()):
            out[f"{s.id}.{t.name}"] = t.description or ""
    return out


# Canary mutations: one broken template each, and the check (checks.js) that must catch it.
MUTATIONS: dict[str, tuple[str, Any, str]] = {
    "drop-tool-list": ("system/default", lambda src: re.sub(r"\{% if tools %\}.*?\{% endif %\}", "", src,
                                                             flags=re.DOTALL), "tool-list"),
    "leak-braces": ("system/default", lambda src: src + "\n{% raw %}Use {{ tool }} here.{% endraw %}",
                    "no-residue"),
    "advertise-write-in-inspect": ("stage/inspect", lambda src: src + "\nYou may also call fs__write_file.",
                                   "tool-mentions"),
    "rename-blocked-marker": ("plan/step", lambda src: src.replace('"BLOCKED:"', '"STUCK:"'), "markers"),
    "bloat": ("system/default", lambda src: src + "\n" + "Be careful and thorough. " * 400, "length"),
}


@cache
def _registry(mutation: str | None = None) -> PromptRegistry:
    reg = PromptRegistry()
    if mutation:
        ref, mutate, _ = MUTATIONS[mutation]
        src = (reg.root / f"{ref}.j2").read_text()
        broken = mutate(src)
        assert broken != src, f"mutation {mutation!r} did not change {ref}"
        reg.env.loader = ChoiceLoader([DictLoader({f"{ref}.j2": broken}), reg.env.loader])
    return reg


# ---------------------------------------------------------------------------------------------------- render

def _offered(recipe: HarnessRecipe, task: Task, assembler: PromptAssembler) -> list[str]:
    """Model-facing tool names, exactly as provision() builds them."""
    names = sorted(model_name(r) for r in allowed_tools(recipe, task, _declared()))
    return names + ([delegation.DELEGATE_NAME] if delegation.context_for(recipe, task, assembler) else [])


def _planner_tools(recipe: HarnessRecipe, task: Task, assembler: PromptAssembler) -> list[dict[str, str]]:
    """What the planner is told about tools: V4 offered_tools(), V5 worker_tools() (name + description)."""
    allowed = allowed_tools(recipe, task, _declared())
    if recipe.variant == Variant.V5_ORCHESTRATOR:
        child = recipe.delegation.child
        allowed = {r for r in allowed if child.tools.allows(r) and r.split(".")[0] in child.tools.mcp_servers}
    tools = [{"name": model_name(r), "description": _descriptions().get(r, "")} for r in sorted(allowed)]
    d = delegation.context_for(recipe, task, assembler)
    if recipe.variant != Variant.V5_ORCHESTRATOR and d is not None and d.depth < d.limits.max_depth:
        tools.append({"name": delegation.DELEGATE_NAME, "description": delegation.DESCRIPTION})
    return tools


def _worker_names(recipe: HarnessRecipe, task: Task, assembler: PromptAssembler) -> list[str]:
    """A V5 worker's model-facing tool names, as run_delegate builds them."""
    d = delegation.context_for(recipe, task, assembler)
    allowed = {r for r in allowed_tools(recipe, task, _declared())
               if d.child.tools.allows(r) and r.split(".")[0] in d.child.tools.mcp_servers}
    grand = delegation.context_for(d.child, task, assembler, d.limits, d.depth + 1, d.chain)
    return sorted(model_name(r) for r in allowed) + (
        [delegation.DELEGATE_NAME] if grand and grand.depth < d.limits.max_depth else [])


def _worker(recipe: HarnessRecipe, task: Task, assembler: PromptAssembler, last: bool) -> list[dict[str, Any]]:
    """What a V5 worker receives: the worker prompt as its task, assembled for the child recipe (run_delegate)."""
    done, step = (SAMPLE_DONE, SAMPLE_PLAN[-1]) if last else ([], SAMPLE_PLAN[0])
    sub = assembler.registry.render(recipe.planning.worker_prompt, goal=task.instruction, done=done, step=step,
                                    last=last)
    child = delegation.context_for(recipe, task, assembler).child
    return assembler.assemble(child, task.model_copy(update={"instruction": sub}),
                              _worker_names(recipe, task, assembler))


def render(recipe_id: str, task_id: str, part: str, mutation: str | None = None) -> list[dict[str, Any]]:
    """The chat messages the harness sends at `part`, rendered from prompts/ (or a canary's broken copy)."""
    recipe, task = _recipes()[recipe_id], _tasks().get(task_id)
    assembler = PromptAssembler(_registry(mutation))
    reg = assembler.registry
    names = _offered(recipe, task, assembler)
    messages = assembler.assemble(recipe, task, names)
    if part == "messages":
        return messages
    kind, _, arg = part.partition(":")
    if kind == "stage":
        return messages + [{"role": "user", "content": assembler.stage_prompts(recipe, task, names)[arg]}]
    pl = recipe.planning
    if kind == "step":                                           # V4 PlannerShape.enter_step
        last = arg == "last"
        index = len(SAMPLE_PLAN) if last else 1
        text = reg.render(pl.step_prompt, plan=[] if last else SAMPLE_PLAN, index=index, total=len(SAMPLE_PLAN),
                          step=SAMPLE_PLAN[index - 1], last=last, tools=names)
        return messages + [{"role": "user", "content": text}]
    if kind == "worker":
        return _worker(recipe, task, assembler, arg == "last")
    # planner / replan: v4.Planner
    system = reg.render(pl.planner_prompt, max_steps=pl.max_plan_steps,
                        tools=_planner_tools(recipe, task, assembler))
    planner = [{"role": "system", "content": system}, *[m for m in messages if m.get("role") != "system"]]
    if kind == "planner":
        return planner
    if kind == "replan":
        text = reg.render(pl.replan_prompt, done=SAMPLE_DONE, blocked=SAMPLE_BLOCKED, max_steps=pl.max_plan_steps)
        return planner + [{"role": "user", "content": text}]
    raise ValueError(f"unknown part {part!r}")


# ---------------------------------------------------------------------------------------------------- expectations

def expectations(recipe_id: str, task_id: str, part: str, n_messages: int, mutation: str | None = None,
                 budgets: dict[str, int] | None = None) -> dict[str, Any]:
    """What the prompt at `part` must say, worked out from the recipe, the task, the tool catalog and the shapes'
    contracts (not by reading the templates): the tool lines and the tools each instruction may name, the markers
    the shapes parse or rely on, the task instruction, and the length caps. checks.js compares the sent prompt
    against this."""
    b = {**DEFAULT_BUDGETS, **(budgets or {})}
    recipe, task = _recipes()[recipe_id], _tasks().get(task_id)
    assembler = PromptAssembler(_registry(mutation))
    kind, _, arg = part.partition(":")
    offered = _offered(recipe, task, assembler)
    pl, n = recipe.planning, len(SAMPLE_PLAN)
    exp: dict[str, Any] = {"lines": [], "planner_tools": None, "read_tools": None, "markers": [], "unwanted": [],
                           "marker_message": 0 if kind == "planner" else -1,
                           "instruction": task.instruction.strip()}
    system_tools, part_tools = offered, offered
    if kind in ("messages", "stage", "step"):
        exp["lines"].append({"message": 0, "prefix": "Available tools: ", "tools": offered})
    if kind == "stage":
        st = next(s for s in recipe.workflow.stages if s.id == arg)
        part_tools = [t for t in offered if st.allows(tool_ref(t))]
        exp["lines"].append({"message": -1, "prefix": "Tools in this stage: ", "tools": part_tools,
                             "none": "No tools in this stage."})
        exp["read_tools"] = [t for t in offered if t != delegation.DELEGATE_NAME
                             and _declared().spec(tool_ref(t)).side_effect == SideEffect.READ]
        if arg == "verify":
            exp["markers"] = [RETRY_PREFIX]
    elif kind == "step":
        exp["lines"].append({"message": -1, "prefix": "Tools: ", "tools": offered})
        last = arg == "last"
        exp["markers"] = [f"Step {n if last else 1} of {n}: {SAMPLE_PLAN[-1 if last else 0]}", f'"{BLOCKED_PREFIX}"']
        exp["markers"] += ["This is the last step"] if last else [f"{i}. {s}" for i, s in enumerate(SAMPLE_PLAN, 1)]
        exp["unwanted"] = [] if last else ["This is the last step"]
    elif kind in ("planner", "replan"):
        exp["planner_tools"] = _planner_tools(recipe, task, assembler)
        system_tools = part_tools = [t["name"] for t in exp["planner_tools"]]
        exp["markers"] = [f"at most {pl.max_plan_steps} steps", "Reply with the plan only."]
        if kind == "replan":
            exp["markers"] += [f"Blocked at: {SAMPLE_BLOCKED['step']}", f"1. {SAMPLE_DONE[0]['step']}"]
    elif kind == "worker":
        system_tools = part_tools = _worker_names(recipe, task, assembler)
        exp["lines"].append({"message": 0, "prefix": "Available tools: ", "tools": system_tools})
        last = arg == "last"
        exp["markers"] = [f"Your part: {SAMPLE_PLAN[-1 if last else 0]}", f'"{BLOCKED_PREFIX}"']
        exp["markers"] += ["Yours is the last part", f"- {SAMPLE_DONE[0]['step']} -> {SAMPLE_DONE[0]['result']}"] \
            if last else []
        exp["unwanted"] = [] if last else ["Yours is the last part", "Earlier workers reported"]
    # The task text is the task author's; the harness's own instructions are the system prompt and, after the task
    # message, the part's instruction (stage, step or replan prompt), which may name only that part's tools.
    exp["mentions"] = [{"message": 0, "allowed": system_tools}]
    if n_messages > 2:
        exp["mentions"].append({"message": -1, "allowed": part_tools})
    skills_system = recipe.skills.loading == "upfront" and kind in ("messages", "stage", "step")
    exp["caps"] = [b["skills_message_chars"] if i == 0 and skills_system else b["message_chars"]
                   for i in range(n_messages)]
    exp["total_chars"] = b["total_chars"]
    return exp


# ---------------------------------------------------------------------------------------------------- tests

def _parts(recipe: HarnessRecipe) -> list[str]:
    parts = ["messages"]
    if recipe.workflow is not None:
        parts += [f"stage:{st.id}" for st in recipe.workflow.stages]
    if recipe.variant == Variant.V4_PLANNER:
        parts += ["planner", "step:first", "step:last", "replan"]
    if recipe.variant == Variant.V5_ORCHESTRATOR:
        parts += ["planner", "worker:first", "worker:last", "replan"]
    return parts


CHECK_FUNCTIONS = {"verbatim": "verbatim", "no-residue": "noResidue", "tool-list": "toolList",   # metric -> checks.js
                   "tool-mentions": "toolMentions", "markers": "markers", "task": "task", "length": "length"}


def _case(description: str, rid: str, tid: str, part: str, mutation: str | None,
          budgets: dict[str, int]) -> dict[str, Any]:
    messages = render(rid, tid, part, mutation)
    exp = expectations(rid, tid, part, len(messages), mutation, budgets)
    # vars are strings: promptfoo expands a list-valued var into one test per element.
    return {"description": description,
            "vars": {"recipe": rid, "task": tid, "part": part, "mutation": mutation or "",
                     "messages": json.dumps(messages), "expect": json.dumps(exp)}}


def generate_tests(config: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """promptfoo test generator. config: recipes ("all" or ids), tasks (ids), budgets (chars); canaries: true
    returns only the canary cases (canaries.yaml), each with the negated check it must fail."""
    config = config or {}
    budgets = {**DEFAULT_BUDGETS, **(config.get("budgets") or {})}
    task_ids = list(config.get("tasks") or DEFAULT_TASKS)
    if config.get("canaries"):
        where = {"system/default": ("v2-base", "messages"), "stage/inspect": ("v3-staged", "stage:inspect"),
                 "plan/step": ("v4-planner", "step:first")}
        return [_case(f"canary: {name} ({ref}) must fail {check}", where[ref][0], task_ids[0], where[ref][1],
                      name, budgets)
                | {"assert": [{"type": "not-javascript", "value": f"file://checks.js:{CHECK_FUNCTIONS[check]}",
                               "metric": f"canary:{check}"}]}
                for name, (ref, _, check) in MUTATIONS.items()]
    wanted = config.get("recipes", "all")
    recipe_ids = sorted(_recipes()) if wanted == "all" else list(wanted)
    return [_case(f"{rid} x {tid} : {part}", rid, tid, part, None, budgets)
            for rid in recipe_ids for tid in task_ids for part in _parts(_recipes()[rid])]
