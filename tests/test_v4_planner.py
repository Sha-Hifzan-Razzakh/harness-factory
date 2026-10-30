"""V4 planner shape: the planner slot writes a numbered plan (no tools), the executor carries it out one step at a
time, and a blocked step sends the remaining work back to the planner.

Offline: `scripted/*` models plan from the reference when called in the planner slot and run one plan step per V4
step; ScriptedBackend scripts (one list for planner and executor calls, in call order) drive the edge cases."""

import json
import shutil
from pathlib import Path

import pytest

from harness_factory.recipes.model import load_recipes
from harness_factory.recipes.validate import validate_recipe
from harness_factory.runner.runner import run_one
from harness_factory.shapes.v4 import parse_plan
from harness_factory.substrate.gateway import ScriptedBackend

V4 = Path("catalog/recipes/base/v4_planner.yaml")
READ = {"tool_calls": [{"name": "fs__read_file", "arguments": {"path": "notes/README.txt"}}]}
WRITE = {"tool_calls": [{"name": "fs__write_file", "arguments": {"path": "notes/todo.txt", "content": "buy milk"}}]}
PLAN2 = {"content": "1. Read notes/README.txt with fs.read_file\n2. Write notes/todo.txt with fs.write_file"}


def recipes(tmp_path, **files) -> dict:
    shutil.copy(V4, tmp_path / "v4.yaml")
    shutil.copy("catalog/recipes/base/v2_tool_loop.yaml", tmp_path / "v2.yaml")
    for name, text in files.items():
        (tmp_path / f"{name}.yaml").write_text(text)
    return load_recipes(tmp_path)


def spans(rec):
    return [json.loads(line) for line in Path(rec.trace_path).read_text().splitlines()]


def snapshot(rec):
    return json.loads(Path(rec.transcript_path).read_text())["snapshot"]


async def scripted(factory, recipe, script, task="fs-01"):
    return await run_one(factory, factory.tasks.get(task), recipe, "scripted/replay", "scripted",
                         backend=ScriptedBackend(script))


# ------------------------------------------------------------------ plan parsing

def test_a_plan_is_its_numbered_lines_and_a_long_tail_is_merged_not_dropped():
    text = "Here is the plan:\n1. Look\n2) Change\nStep 3: Check\n- not a step\n"
    assert parse_plan(text, 8) == ["Look", "Change", "Check"]
    assert parse_plan("1. a\n2. b\n3. c\n4. d", 2) == ["a", "b; then c; then d"]
    assert parse_plan("I would just do it.", 8) == []


# ------------------------------------------------------------------ identity and validation

def test_planning_is_omitted_from_other_hashes_and_only_v4_takes_it(factory, tmp_path):
    assert factory.recipes["v2-base"].content_hash() == "79abd03818943f65"            # unchanged
    assert factory.recipes["v2-base"].planning is None and factory.recipes["v4-planner"].planning is not None
    rs = recipes(tmp_path,
                 noplan="id: noplan\nbase: v4-planner\nplanning: null\n",
                 v2plan="id: v2plan\nbase: v2-base\nplanning: {max_plan_steps: 2}\n",
                 tight="id: tight\nbase: v4-planner\nconfig: {max_steps: 10}\n",
                 pinned="id: pinned\nbase: v4-planner\nmodel_slots: {planner: {role: planner, canonical_model: x/y}}\n",
                 noprompt="id: noprompt\nbase: v4-planner\nplanning: {step_prompt: plan/nothing}\n",
                 fewer="id: fewer\nbase: v4-planner\nplanning: {max_plan_steps: 8, step_max_steps: 3, max_replans: 0}\n")
    rules = lambda r: {i.rule for i in validate_recipe(rs[r], factory.declared)}
    assert "planning_required" in rules("noplan")
    assert "planning_unused" in rules("v2plan")
    assert "planning_fits_budget" in rules("tight")
    assert "planner_model_unpinned" in rules("pinned")
    assert "prompt_exists" in rules("noprompt")
    assert not rules("v4-planner")
    db, dd = rs["v4-planner"].model_dump(), rs["fewer"].model_dump()
    assert sorted(k for k in db if db[k] != dd[k]) == ["base", "id", "planning"]
    assert rs["fewer"].content_hash() != rs["v4-planner"].content_hash()       # planning is part of identity


# ------------------------------------------------------------------ behaviour

async def test_the_planner_plans_without_tools_and_each_step_runs_in_its_own_scope(factory):
    rec = await run_one(factory, factory.tasks.get("fs-02"), factory.recipes["v4-planner"], "scripted/replay",
                        "scripted")
    assert rec.status.value == "SUCCESS" and rec.scores.trajectory == 1.0
    ss = spans(rec)
    order = [(s["kind"], s["name"]) for s in ss if s["kind"] in ("handoff", "tool_call")]
    assert order == [("handoff", "plan"), ("handoff", "step:1"), ("tool_call", "fs.read_file"),
                     ("handoff", "step:2"), ("tool_call", "fs.write_file")]
    assert [s["attributes"]["scope"] for s in ss if s["kind"] == "tool_call"] == ["step:1", "step:2"]
    assert rec.cost.model_calls == 1 + 2 * 2                         # plan, then a call and a reply per step
    snap = snapshot(rec)
    (plan,) = snap["plans"]
    assert plan["kind"] == "plan" and len(plan["steps"]) == 2 and snap["replans"] == 0
    assert [d["step"] for d in snap["plan_trail"]] == plan["steps"]
    msgs = json.loads(Path(rec.transcript_path).read_text())["messages"]
    steps = [m["content"] for m in msgs if m["role"] == "user" and "Step " in (m.get("content") or "")]
    assert steps[0].startswith("The plan:\n1. ") and "Step 1 of 2:" in steps[0]
    assert "The plan:" not in steps[1] and "Step 2 of 2:" in steps[1]          # the plan is shown once


async def test_a_blocked_step_sends_the_remaining_work_back_to_the_planner(factory):
    script = [PLAN2, READ, {"content": "BLOCKED: the README says to write the todo first"},
              {"content": "1. Write notes/todo.txt with fs.write_file"}, WRITE, {"content": "Created it."}]
    rec = await scripted(factory, factory.recipes["v4-planner"], script)
    assert rec.status.value == "SUCCESS"
    snap = snapshot(rec)
    assert [p["kind"] for p in snap["plans"]] == ["plan", "replan"] and snap["replans"] == 1
    assert [d["result"][:8] for d in snap["plan_trail"]] == ["BLOCKED:", "Created "]
    assert [s["name"] for s in spans(rec) if s["kind"] == "handoff"] == ["plan", "step:1", "replan", "step:2"]
    assert rec.cost.model_calls == 6


async def test_without_replans_left_a_blocked_step_moves_on(factory, tmp_path):
    rs = recipes(tmp_path, once="id: once\nbase: v4-planner\nplanning: {max_plan_steps: 8, step_max_steps: 3, "
                                "max_replans: 0}\n")
    script = [PLAN2, {"content": "BLOCKED: no"}, WRITE, {"content": "Created it."}]
    rec = await scripted(factory, rs["once"], script)
    assert rec.status.value == "SUCCESS" and snapshot(rec)["replans"] == 0
    assert [p["kind"] for p in snapshot(rec)["plans"]] == ["plan"]


async def test_a_step_that_runs_past_its_cap_is_ended_and_recorded(factory):
    script = [{"content": "1. Write the file"}, READ, READ, READ, READ, {"content": "never asked"}]
    rec = await scripted(factory, factory.recipes["v4-planner"], script)
    assert any(v.kind == "step_cap" and v.detail.startswith("step 1") for v in rec.violations)
    assert rec.cost.model_calls == 1 + 3                              # step_max_steps: 3
    assert rec.scores.outcome == 0.0


async def test_an_unnumbered_plan_becomes_one_step_and_is_recorded(factory):
    rec = await scripted(factory, factory.recipes["v4-planner"],
                         [{"content": "Just write the file."}, WRITE, {"content": "Created it."}])
    assert rec.status.value == "SUCCESS"
    assert any(v.kind == "plan_unparsed" for v in rec.violations)
    assert snapshot(rec)["plans"][0]["steps"] == ["Complete the task."]


@pytest.mark.parametrize("model, expected", [("scripted/replay", "SUCCESS"), ("scripted/noisy", "SUCCESS"),
                                             ("scripted/lazy", "FAIL")])
async def test_scripted_styles_behave_on_v4_as_they_do_on_v2(factory, model, expected):
    rec = await run_one(factory, factory.tasks.get("fs-02"), factory.recipes["v4-planner"], model, "scripted")
    assert rec.status.value in (expected, "PARTIAL" if expected == "FAIL" else expected)
    if model == "scripted/noisy":
        assert rec.scores.trajectory < 1.0                            # the malformed call still costs Q


@pytest.mark.parametrize(("recipe", "script"), [
    ("v2-base", [WRITE] + [READ] * 20),                                       # until max_steps 15
    ("v3-staged", [{"content": "seen"}, WRITE] + [READ] * 5 + [{"content": "looks right"}]),   # act's 6-call cap
    ("v4-planner", [{"content": "1. Write notes/todo.txt"}, WRITE, READ, READ]),               # step_max_steps 3
])
async def test_running_on_to_a_cap_after_solving_is_an_overrun_in_every_shape(factory, recipe, script):
    """Write the file, then keep calling until a cap: V4's step cap counts like V3's stage cap and V2's budget, so
    no shape gets full credit for it (R-03)."""
    rec = await scripted(factory, factory.recipes[recipe], script)
    assert rec.status.value == "PARTIAL"
    assert rec.scores.outcome == 0.5                                           # OVERRUN_OUTCOME_FACTOR
