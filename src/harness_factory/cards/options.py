"""Everything an experiment can be built from, in one structure: what the UI's "What we can test with" page and
`GET /api/options` show. Read from the loaded catalog and the pydantic models, so it is never out of date."""

from __future__ import annotations

import functools
import operator
import os
import typing
from enum import Enum

from pydantic import BaseModel

from harness_factory import settings

# What each shape is for (README, Shapes). The control flow is the only thing shapes differ in.
SHAPES = {
    "V1_SINGLE_CALL": ("v1-base", "One model response; its tool calls run; nothing follows.",
                       "single-step writes, cheaply", "anything that must read before acting"),
    "V2_TOOL_LOOP": ("v2-base", "The model calls tools until it answers.", "general tasks",
                     "over-spending on easy tasks"),
    "V3_WORKFLOW_GRAPH": ("v3-staged", ("Stages inspect (read-only) -> act -> verify (read-only); verify may "
                                       "send it back to act once."), "tasks where acting blind is costly",
                          "more model calls per task"),
    "V4_PLANNER": ("v4-planner", ("A planner writes a numbered plan; each step runs as a bounded tool loop; a "
                                 "blocked step replans once."), "long known-path tasks",
                   "tasks whose path only shows up while working"),
    "V5_ORCHESTRATOR": ("v5-orchestrator", "V4's plan, but each step goes to a fresh worker agent.",
                        "long tasks with independent steps", "steps that need what an earlier step saw"),
}

# Recipe fields people vary, with what they mean (the matrix dimensions).
FIELD_HELP = {
    "variant": "the shape (control flow)", "tier": "highest risk tier the harness may touch (T1-T4)",
    "model_slots.executor.temperature": "sampling temperature", "model_slots.executor.max_output_tokens":
    "reply budget per model call", "tools.tool_ids": "tool patterns the harness may offer, e.g. fs.*",
    "tools.mcp_servers": "MCP servers the run opens", "skills.loading": "none / upfront (in the prompt) / "
    "on_demand (skills.load tool)", "skills.skill_ids": "which skills", "prompts.system": "system prompt template",
    "prompts.task": "task message template", "prompts.operator": "optional operator layer",
    "config.max_steps": "model calls allowed", "config.max_tool_calls": "tool calls allowed",
    "config.max_tokens": "tokens allowed", "config.wall_time_s": "seconds allowed",
    "config.parallel_tool_calls": "run READ calls of one reply concurrently",
    "config.recover_malformed": "re-ask after a malformed reply, at most n times",
    "config.compaction": "summarise old turns near the context limit", "policies.permission_mode":
    "AUTO / ASK / DENY per side effect", "retrieval": "retrieve__search over the task's corpus (bm25, dense, "
    "hybrid, rerank)", "workflow": "V3 stages", "planning": "V4/V5 planner settings", "delegation":
    "delegate__run helper recipe", "runtime": "native or a framework's own agent"}


def _literal_values(tp) -> list[str]:
    return [str(a) for a in typing.get_args(tp)] if typing.get_origin(tp) is typing.Literal else []


def recipe_fields() -> list[dict]:
    """Every recipe field (nested), its type, default and allowed values: the full menu of dimensions."""
    from harness_factory.recipes.model import HarnessRecipe

    out: list[dict] = []

    def walk(model: type[BaseModel], prefix: str, depth: int) -> None:
        for name, fi in model.model_fields.items():
            key = f"{prefix}{name}"
            ann = fi.annotation
            inner = [a for a in typing.get_args(ann) if a is not type(None)] or [ann]
            sub = next((a for a in inner if isinstance(a, type) and issubclass(a, BaseModel)), None)
            enum = next((a for a in inner if isinstance(a, type) and issubclass(a, Enum)), None)
            values = [e.value for e in enum] if enum else functools.reduce(operator.iadd, (_literal_values(a) for a in inner), [])
            default = fi.get_default(call_default_factory=True) if not fi.is_required() else "(required)"
            if isinstance(default, BaseModel):
                default = "(object)"
            if sub is not None and depth < 2 and name not in ("delegation",):
                out.append({"field": key, "type": sub.__name__, "default": "-" if default is None else str(default),
                            "values": "", "meaning": FIELD_HELP.get(key, "")})
                walk(sub, key + ".", depth + 1)
                continue
            out.append({"field": key, "type": getattr(ann, "__name__", str(ann)).replace("typing.", ""),
                        "default": "-" if default is None else str(default), "values": ", ".join(values),
                        "meaning": FIELD_HELP.get(key, "")})

    walk(HarnessRecipe, "", 0)
    return out


def options(f) -> dict:
    from harness_factory.cards.build import recipe_changes
    from harness_factory.provision.provisioner import task_class
    from harness_factory.recipes.model import RuntimeName
    from harness_factory.skills.library import load_skills
    from harness_factory.tasks.model import PERTURBATION_TYPES, CheckKind

    recipes = []
    for rid, r in sorted(f.recipes.items()):
        ch = recipe_changes(f, rid)
        recipes.append({"id": rid, "kind": "matrix" if r.base else "base", "base": r.base or "",
                        "shape": r.variant.value, "runtime": r.runtime or "native",
                        "servers": ", ".join(r.tools.mcp_servers),
                        "changes": "; ".join(f"{k} = {v}" for k, _, v in ch[:4]) + (" ..." if len(ch) > 4 else ""),
                        "hash": r.content_hash()})
    models = []
    for p in f.providers.providers:
        if p.id == "scripted-judge":
            continue
        for m in p.models:
            models.append({"provider": p.id, "model": m.canonical, "provider model id": m.provider_model_id,
                           "context": m.caps.context_len, "tool calling": m.caps.tool_calling,
                           "price in/out $/M": f"{m.price_in:g}/{m.price_out:g}", "free": m.free,
                           "key set": p.auth_env is None or bool(os.environ.get(p.auth_env))})
    tools = [{"server": s.id, "tool": t.name, "side effect": t.side_effect.value, "risk": t.risk,
              "timeout s": t.timeout_s, "granted by": ", ".join(t.granted_by or [])}
             for s in f.declared.servers for t in s.tools]
    prompts = []
    root = settings.PROMPTS
    for p in sorted(root.rglob("*.j2")):
        ref = p.relative_to(root).with_suffix("").as_posix()
        prompts.append({"template": ref, "layer": ref.split("/")[0],
                        "used by": ", ".join(rid for rid, r in f.recipes.items()
                                             if ref in (r.prompts.system, r.prompts.task, r.prompts.operator))})
    tasks = [{"id": t.id, "class": task_class(t, f.declared), "domain": t.domain, "split": t.split.value,
              "checks": len(t.oracle.checks), "tags": ", ".join(t.tags), "instruction": t.instruction[:140]}
             for t in f.tasks.all(include_held_out=True)]
    return {
        "shapes": [{"shape": k, "base recipe": v[0], "control flow": v[1], "good at": v[2], "weak at": v[3]}
                   for k, v in SHAPES.items()],
        "recipes": recipes,
        "recipe_fields": recipe_fields(),
        "models": models,
        "tools": tools,
        "skills": [{"id": s.id, "name": s.name, "description": s.description} for s in load_skills().values()],
        "prompts": prompts,
        "faults": [{"id": fid, "description": fc.description, "rules": len(fc.rules)}
                   for fid, fc in sorted(f.faults.items())],
        "runtimes": [{"runtime": r} for r in typing.get_args(RuntimeName) for r in
                     (typing.get_args(r) if typing.get_origin(r) is typing.Literal else (r,))],
        "tasks": tasks,
        "check_kinds": [{"kind": k} for k in typing.get_args(CheckKind)],
        "perturbations": [{"type": t} for t in PERTURBATION_TYPES],
        "retrieval_modes": [{"mode": m} for m in ("bm25", "dense", "hybrid")],
    }
