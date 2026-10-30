"""Recipe matrix: recipes built on a base, and the first two dimensions (prompt strategy, malformed-reply recovery).

Each dimension needs an offline test that it changes behaviour, and that a recipe without it behaves as before."""

import json
from pathlib import Path

import pytest

from harness_factory.recipes.model import load_recipe, load_recipes
from harness_factory.runner.runner import run_one
from harness_factory.shapes.common import RECOVERY_NOTE
from harness_factory.substrate.gateway import ScriptedBackend

READ = {"tool_calls": [{"name": "fs__read_file", "arguments": {"path": "config/app.ini"}}]}
REJECT = {"reject": "tool call validation failed: attempted to call tool 'fs__search_file' which was not in "
                    "request.tools"}


def grade(rec, grader):
    return next(g for g in rec.grades if g["grader_id"] == grader)


# ------------------------------------------------------------------ base: inheritance

def test_matrix_recipes_change_one_dimension_and_leave_base_hashes_alone(factory):
    base, recover, plan = (factory.recipes[r] for r in ("v2-base", "v2-recover", "v2-plan-check"))
    assert base.content_hash() == "79abd03818943f65"                      # unchanged since before the matrix
    db = base.model_dump()
    for r, dim in ((recover, "config"), (plan, "prompts")):
        d = r.model_dump()
        assert sorted(k for k in db if db[k] != d[k]) == sorted(["id", "base", dim])
        assert r.content_hash() != base.content_hash()


def test_base_resolution_merges_maps_and_refuses_bad_bases(tmp_path):
    (tmp_path / "a.yaml").write_text(Path("catalog/recipes/base/v2_tool_loop.yaml").read_text())
    (tmp_path / "b.yaml").write_text("id: child\nbase: v2-base\nconfig: {max_steps: 4}\n")
    r = load_recipes(tmp_path)["child"]
    assert r.config.max_steps == 4 and r.config.max_tool_calls == 20       # merged, not replaced
    assert r.variant.value == "V2_TOOL_LOOP" and r.base == "v2-base"
    with pytest.raises(ValueError, match="load_recipes"):
        load_recipe(tmp_path / "b.yaml")
    (tmp_path / "c.yaml").write_text("id: orphan\nbase: nope\n")
    with pytest.raises(ValueError, match="unknown recipe 'nope'"):
        load_recipes(tmp_path)
    (tmp_path / "c.yaml").write_text("id: x\nbase: y\n")
    (tmp_path / "d.yaml").write_text("id: y\nbase: x\n")
    with pytest.raises(ValueError, match="cycle"):
        load_recipes(tmp_path)


# ------------------------------------------------------------------ config: recover from a rejected reply

async def test_without_recovery_a_rejected_reply_ends_the_run_and_costs_q(factory):
    rec = await run_one(factory, factory.tasks.get("fs-02"), factory.recipes["v2-base"], "scripted/replay",
                        "scripted", backend=ScriptedBackend([READ, REJECT]))
    assert rec.status.value != "SUCCESS" and any(v.kind == "malformed_output" for v in rec.violations)
    assert rec.scores.trajectory == 0.5                    # one good call, one rejected: before, this was 1.0


async def test_recovery_turns_an_invented_tool_into_a_finished_run(factory):
    task = factory.tasks.get("fs-02")
    plain = await run_one(factory, task, factory.recipes["v2-base"], "scripted/invent", "scripted")
    rec = await run_one(factory, task, factory.recipes["v2-recover"], "scripted/invent", "scripted")
    assert plain.status.value != "SUCCESS" and plain.scores.outcome == 0     # Phase 1's code-01, offline
    assert rec.status.value == "SUCCESS" and rec.scores.outcome == 1.0
    assert rec.scores.trajectory == pytest.approx(1 - 1 / 3)               # 2 good calls + 1 rejected
    assert rec.composite < 1.0                                               # never as good as a clean run
    notes = [m for m in json.loads(Path(rec.transcript_path).read_text())["messages"]
             if m["role"] == "user" and m["content"].startswith(RECOVERY_NOTE[:20])]
    assert len(notes) == 1 and "fs__write_file" in notes[0]["content"]     # the real tool names, in the transcript


async def test_recovery_is_capped_per_run(factory):
    recipe = factory.recipes["v2-recover"]                                   # recover_malformed: 2
    rec = await run_one(factory, factory.tasks.get("fs-02"), recipe, "scripted/replay", "scripted",
                        backend=ScriptedBackend([REJECT, REJECT, REJECT, READ]))
    assert any(v.kind == "malformed_output" for v in rec.violations)        # the third rejection ends the run
    assert rec.cost.tool_calls == 0


async def test_recovery_works_in_every_shape_and_counts_against_v3_stage_caps(factory):
    v3 = factory.recipes["v3-staged"]
    v3r = v3.model_copy(update={"config": v3.config.model_copy(update={"recover_malformed": 1})})
    rec = await run_one(factory, factory.tasks.get("fs-02"), v3r, "scripted/invent", "scripted")
    assert rec.status.value == "SUCCESS"
    v1 = factory.recipes["v1-base"]                                          # one step: no room to re-ask
    v1r = v1.model_copy(update={"config": v1.config.model_copy(update={"recover_malformed": 1})})
    one = await run_one(factory, factory.tasks.get("fs-01"), v1r, "scripted/invent", "scripted")
    assert one.status.value != "SUCCESS"


# ------------------------------------------------------------------ prompt: plan, act, check

async def test_plan_check_prompt_is_what_the_model_sees(factory):
    rec = await run_one(factory, factory.tasks.get("fs-02"), factory.recipes["v2-plan-check"], "scripted/replay",
                        "scripted")
    system = json.loads(Path(rec.transcript_path).read_text())["messages"][0]
    assert system["role"] == "system" and "numbered plan" in system["content"]
    assert "fs__write_file" in system["content"] or "fs.write_file" in system["content"]
    base = await run_one(factory, factory.tasks.get("fs-02"), factory.recipes["v2-base"], "scripted/replay",
                         "scripted")
    assert "numbered plan" not in json.loads(Path(base.transcript_path).read_text())["messages"][0]["content"]


# ------------------------------------------------------------------ every declared field does something or is refused

def test_a_recipe_setting_an_unimplemented_field_is_refused(factory):
    from harness_factory.recipes.validate import validate_recipe
    base = factory.recipes["v2-base"]
    assert not validate_recipe(base, factory.declared)
    skills = base.model_copy(update={"skills": base.skills.model_copy(update={"skill_ids": ["sql-analysis"],
                                                                              "loading": "upfront"})})
    memory = base.model_copy(update={"memory": base.memory.model_copy(update={"summary": True})})
    for r, field in ((skills, "skills"), (memory, "memory")):
        issues = validate_recipe(r, factory.declared)
        assert [i.rule for i in issues] == ["field_implemented"] and field in issues[0].detail
