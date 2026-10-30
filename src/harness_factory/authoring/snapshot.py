"""A catalog snapshot: everything authoring needs to know about one catalog, computed by the factory's own code.

`take()` reads the catalog the `settings` module points at. Authoring never points `settings` elsewhere inside a
running process (a campaign may be reading it), so it runs this module in a child process with HF_ROOT set to the
catalog to inspect: `python -m harness_factory.authoring.snapshot OUT.json`.

The snapshot holds identities (recipe hashes, server env hashes), the references between items (which recipe uses
which prompt, skill, server or base) and every load error and validation issue.
"""

from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path
from typing import Any

# Variables each prompt layer is rendered with (substrate/prompts.py, shapes/v4.py). Templates are rendered with
# StrictUndefined, so a template that uses anything else fails the run that renders it.
LAYER_VARS: dict[str, set[str]] = {
    "system": {"tools", "recipe"},
    "task": {"task"},
    "stage": {"task", "stage", "tools"},
    "skills": {"skills"},
}


def _rel(p: Path, root: Path) -> str:
    return p.relative_to(root).as_posix()


def take() -> dict[str, Any]:
    import jinja2
    import yaml
    from jinja2 import meta
    from pydantic import ValidationError

    import harness_factory.shapes.builtin  # noqa: F401  (registers built-in shapes)
    from harness_factory import settings
    from harness_factory.env.faults import load_fault_profiles
    from harness_factory.env.mcp.servers import load_tool_catalog
    from harness_factory.grading.base import default_pipeline
    from harness_factory.hashing import content_hash
    from harness_factory.recipes.model import default_prompt_sources, load_recipes
    from harness_factory.recipes.validate import validate_recipe
    from harness_factory.shapes.base import ShapeRegistry
    from harness_factory.skills.library import parse_skill
    from harness_factory.tasks.registry import TaskRegistry

    root = settings.ROOT
    out: dict[str, Any] = {"root": str(root), "errors": [], "prompts": {}, "skills": {}, "servers": {},
                           "recipes": {}, "faults": {}, "tasks": {}, "selector_refs": [],
                           "variants": sorted(v.value for v in ShapeRegistry.variants())}

    def err(kind: str, item: str, detail: str) -> None:
        out["errors"].append({"kind": kind, "id": item, "detail": detail})

    # Prompts: Jinja syntax, and the variables the layer is rendered with.
    sources = default_prompt_sources()
    env = jinja2.Environment()
    for ref, src in sources.items():
        entry: dict[str, Any] = {"path": f"prompts/{ref}.j2", "hash": content_hash(src, 12), "vars": []}
        try:
            entry["vars"] = sorted(meta.find_undeclared_variables(env.parse(src)))
        except jinja2.TemplateSyntaxError as e:
            err("prompt", ref, f"line {e.lineno}: {e.message}")
        allowed = LAYER_VARS.get(ref.split("/")[0])
        if allowed is not None and set(entry["vars"]) - allowed:
            err("prompt", ref, f"uses {', '.join(sorted(set(entry['vars']) - allowed))}; a {ref.split('/')[0]} "
                               f"prompt is rendered with {', '.join(sorted(allowed))} only")
        out["prompts"][ref] = entry

    # Skills, one file at a time so one bad header doesn't hide the others.
    skills = {}
    for p in sorted(settings.SKILLS_DIR.glob("*/SKILL.md")) if settings.SKILLS_DIR.is_dir() else []:
        try:
            s = parse_skill(p.parent.name, p.read_text())
        except ValueError as e:
            err("skill", p.parent.name, str(e))
            continue
        skills[s.id] = s
        out["skills"][s.id] = {"path": _rel(p, root), "name": s.name, "description": s.description,
                               "hash": content_hash(s.text, 12)}

    # Tool servers: the env-hash part of each server's tools.
    declared = None
    try:
        declared = load_tool_catalog(settings.TOOLS_FILE)
    except ValidationError as e:
        for x in e.errors():
            err("tools", "tools.yaml", x["msg"].removeprefix("Value error, "))
    except Exception as e:  # noqa: BLE001  (a finding, not a crash)
        err("tools", "tools.yaml", str(e))
    if declared is not None:
        for s in declared.servers:
            out["servers"][s.id] = {
                "transport": s.transport, "module": s.module, "url": s.url, "version": s.version,
                "auth_env": s.auth_env, "tenant": s.tenant is not None,
                "tools": [{"name": t.name, "side_effect": t.side_effect.value, "risk": t.risk,
                           "schema_hash": t.schema_hash, "granted_by": t.granted_by, "agent": t.agent is not None}
                          for t in s.tools],
                "env": content_hash({t.ref(s.id): t.hashed() for t in s.tools}, 12),
                "unhashed": [t.ref(s.id) for t in s.tools if not t.schema_hash],
            }

    # Recipes: raw files (paths, bases) and the resolved, validated recipes.
    raw: dict[str, dict[str, Any]] = {}
    for p in sorted(settings.RECIPES_DIR.rglob("*.yaml")) if settings.RECIPES_DIR.is_dir() else []:
        try:
            data = yaml.safe_load(p.read_text()) or {}
        except yaml.YAMLError as e:
            err("recipe", _rel(p, root), f"not YAML: {e}")
            continue
        raw[str(data.get("id"))] = {"path": _rel(p, root), "base": data.get("base"),
                                    "changes": sorted(k for k in data if k not in ("id", "version", "base"))}
    try:
        recipes = load_recipes(settings.RECIPES_DIR)
    except Exception as e:  # noqa: BLE001  (a finding, not a crash)
        err("recipe", "*", str(e))
        recipes = {}
    for rid, r in recipes.items():
        issues = []
        if declared is not None:
            try:
                issues = [i.model_dump() for i in validate_recipe(r, declared)]
            except Exception as e:  # noqa: BLE001  (a finding, not a crash)
                issues = [{"rule": "validator_crashed", "detail": f"{type(e).__name__}: {e}"}]
        out["recipes"][rid] = {
            **raw.get(rid, {}), "hash": r.content_hash(sources, skills), "variant": r.variant.value, "tier": r.tier,
            "servers": list(r.tools.mcp_servers), "prompt_refs": sorted(set(r.prompt_refs())),
            "skill_ids": list(r.skills.skill_ids), "delegate": r.delegation.recipe if r.delegation else None,
            "issues": issues, "resolved": r.model_dump(mode="json", exclude_none=True),
        }

    try:
        out["faults"] = {fid: {"hash": f.content_hash(), "description": f.description}
                         for fid, f in load_fault_profiles(settings.FAULTS_DIR).items()}
    except Exception as e:  # noqa: BLE001  (a finding, not a crash)
        err("fault", "*", str(e))

    try:
        tasks = TaskRegistry().all(include_held_out=True)
        out["tasks"] = {t.id: {"allowed_tools": list(t.allowed_tools), "split": t.split.value} for t in tasks}
        graders = default_pipeline()
        for t in tasks:                      # same rule as `hfx validate` and plan(): no unknown grader ids
            unknown = graders.unknown(t)
            if unknown:
                err("task", t.id, f"unknown grader(s) {', '.join(unknown)}; known: {', '.join(sorted(graders.graders))}")
    except Exception as e:  # noqa: BLE001  (a finding, not a crash)
        err("task", "*", str(e))

    sel = settings.CATALOG / "selector.yaml"
    if sel.is_file():
        data = yaml.safe_load(sel.read_text()) or {}
        refs = [data.get("hand_picked")] + [d.get("recipe") for d in data.get("defaults") or []]
        refs += list(data.get("candidates") or [])
        out["selector_refs"] = sorted({r for r in refs if r})
    return out


def main() -> None:
    target = Path(sys.argv[1])
    try:
        data = take()
    except Exception:  # noqa: BLE001  (reported to the parent as a crash, with its traceback)
        data = {"crash": traceback.format_exc()}
    target.write_text(json.dumps(data))


if __name__ == "__main__":
    main()
