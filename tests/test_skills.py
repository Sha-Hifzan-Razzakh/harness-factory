"""Skills dimension: the library, how skills reach the model (upfront / on demand), the skills.load tool, and
identity (a skill's text is part of the recipe hash)."""

import json
from pathlib import Path

import pytest

from harness_factory.env.sandbox import LocalSandbox
from harness_factory.mcp.pool import MCPSessionPool
from harness_factory.provision.provisioner import allowed_tools, install_skills
from harness_factory.recipes.validate import validate_recipe
from harness_factory.runner.runner import run_one
from harness_factory.skills.library import load_skills, parse_skill
from harness_factory.substrate.gateway import ScriptedBackend

ALL = ["sql-analysis", "sql-changes", "editing-files", "file-exports", "requests-and-policies"]


def system_prompt(rec) -> str:
    return json.loads(Path(rec.transcript_path).read_text())["messages"][0]["content"]


def test_library_parses_and_refuses_a_bad_header():
    lib = load_skills()
    assert sorted(lib) == sorted(ALL) and all(s.name and s.description and s.body for s in lib.values())
    with pytest.raises(ValueError, match="header"):
        parse_skill("x", "no header here")
    with pytest.raises(ValueError, match="description"):
        parse_skill("x", "---\nname: X\n---\nbody")


async def test_upfront_puts_every_body_in_the_prompt_on_demand_only_the_index(factory):
    task = factory.tasks.get("sql-01")
    up = await run_one(factory, task, factory.recipes["v2-skills-upfront"], "scripted/replay", "scripted")
    od = await run_one(factory, task, factory.recipes["v2-skills-on-demand"], "scripted/replay", "scripted")
    base = await run_one(factory, task, factory.recipes["v2-base"], "scripted/replay", "scripted")
    lib = load_skills()
    assert all(lib[s].body in system_prompt(up) for s in ALL)
    assert all(f"- {s}: {lib[s].description}" in system_prompt(od) for s in ALL)
    assert not any(lib[s].body in system_prompt(od) for s in ALL)
    assert "Skills:" not in system_prompt(base)
    assert up.status.value == od.status.value == "SUCCESS"


async def test_skills_load_reads_an_installed_skill_and_names_the_choices(factory):
    recipe = factory.recipes["v2-skills-on-demand"]
    with LocalSandbox("skills-test") as sb:
        sb.reset(factory.tasks.get("sql-01"))
        install_skills(sb, recipe)
        assert not any("SKILL" in p.name for p in sb.workspace.rglob("*"))      # outside the workspace
        async with MCPSessionPool(factory.declared, ["skills"], sb.env()) as pool:
            ok = await pool.call("skills.load", {"name": "sql-changes"})
            assert not ok.is_error and ok.text.startswith("1. Write the WHERE clause") and "---" not in ok.text
            bad = await pool.call("skills.load", {"name": "nope"})
            assert bad.is_error and "available: editing-files, file-exports" in bad.text


async def test_a_model_loading_a_skill_on_demand(factory):
    script = [{"tool_calls": [{"name": "skills__load", "arguments": {"name": "sql-analysis"}}]},
              {"tool_calls": [{"name": "sql__query", "arguments": {"sql": "SELECT COUNT(*) FROM customers "
                                                                          "WHERE city = 'Dubai'"}}]},
              {"content": "3"}]
    rec = await run_one(factory, factory.tasks.get("sql-01"), factory.recipes["v2-skills-on-demand"],
                        "scripted/replay", "scripted", backend=ScriptedBackend(script))
    loaded = next(m for m in json.loads(Path(rec.transcript_path).read_text())["messages"] if m["role"] == "tool")
    assert "List the tables and describe" in loaded["content"]
    assert rec.status.value == "SUCCESS" and rec.scores.trajectory == 1.0 and rec.cost.tool_calls == 2


def test_skills_load_is_granted_to_every_task_but_only_where_the_recipe_opens_it(factory):
    od, base, d = factory.recipes["v2-skills-on-demand"], factory.recipes["v2-base"], factory.declared
    for tid in ("sql-01", "fs-01", "mix-03"):
        assert "skills.load" in allowed_tools(od, factory.tasks.get(tid), d)
        assert "skills.load" not in allowed_tools(base, factory.tasks.get(tid), d)


def test_skill_text_is_part_of_the_recipe_hash_and_recipes_without_skills_did_not_move(factory):
    up = factory.recipes["v2-skills-upfront"]
    lib = load_skills()
    edited = {**lib, "sql-analysis": parse_skill("sql-analysis", lib["sql-analysis"].text + "\n8. Be quick.\n")}
    assert up.content_hash(skills=edited) != up.content_hash()
    assert factory.recipes["v2-base"].content_hash() == "79abd03818943f65"
    assert factory.recipes["v2-base"].content_hash(skills=edited) == "79abd03818943f65"


def test_skill_recipes_are_validated(factory):
    base = factory.recipes["v2-base"]

    def with_skills(**kw):
        return base.model_copy(update={"skills": base.skills.model_copy(update=kw)})

    def rules(r):
        return [i.rule for i in validate_recipe(r, factory.declared)]

    assert rules(with_skills(skill_ids=["sql-analysis"], loading="upfront")) == []
    assert rules(with_skills(skill_ids=["no-such-skill"], loading="upfront")) == ["skill_exists"]
    assert rules(with_skills(skill_ids=["sql-analysis"])) == ["skills_loading"]           # loading: none
    assert rules(with_skills(loading="upfront")) == ["skills_loading"]                     # nothing to load
    assert rules(with_skills(skill_ids=["sql-analysis"], loading="on_demand")) == ["skills_tool_reachable"]
