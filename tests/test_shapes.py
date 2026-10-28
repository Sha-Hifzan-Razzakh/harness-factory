"""V1 single call and V3 workflow graph, with the tool-aware scripted model and hand-written scripts."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from harness_factory.recipes.model import WorkflowSpec
from harness_factory.recipes.validate import validate_recipe
from harness_factory.runner.runner import Factory, run_one
from harness_factory.substrate.gateway import ScriptedBackend

TASK_IDS = [t.id for t in Factory.load(store=False).tasks.all()]


def _spans(rec, kind):
    return [s for s in map(json.loads, Path(rec.trace_path).read_text().splitlines()) if s["kind"] == kind]


# ---------------------------------------------------------------- V1

async def test_v1_solves_single_step_write_in_one_call(factory):
    rec = await run_one(factory, factory.tasks.get("fs-01"), factory.recipes["v1-base"], "scripted/replay", "scripted")
    assert rec.status.value == "SUCCESS"
    assert rec.cost.model_calls == 1 and rec.cost.tool_calls == 1


async def test_v1_cannot_answer_what_it_must_read_first(factory):
    rec = await run_one(factory, factory.tasks.get("sql-01"), factory.recipes["v1-base"], "scripted/replay", "scripted")
    assert rec.status.value == "PARTIAL"          # grounded (queried) but never saw the result to answer
    assert rec.cost.model_calls == 1


async def test_v1_runs_every_tool_call_in_its_one_response(factory):
    csv = "id,name\n1,Al Noor Trading\n3,Falcon Systems\n5,Creek Analytics\n"
    backend = ScriptedBackend([{"tool_calls": [
        {"name": "sql__query", "arguments": {"sql": "SELECT id, name FROM customers WHERE city='Dubai'"}},
        {"name": "fs__write_file", "arguments": {"path": "reports/dubai.csv", "content": csv}},
    ]}])
    rec = await run_one(factory, factory.tasks.get("mix-01"), factory.recipes["v1-base"], "scripted/replay",
                        "scripted", backend=backend)
    assert rec.status.value == "SUCCESS"
    assert rec.cost.model_calls == 1 and rec.cost.tool_calls == 2


async def test_step_cap_does_not_block_the_last_steps_tools(factory):
    """max_steps gates model calls only; the tool calls of the final allowed step still run."""
    base = factory.recipes["v2-base"]
    capped = base.model_copy(update={"config": base.config.model_copy(update={"max_steps": 1})})
    rec = await run_one(factory, factory.tasks.get("fs-01"), capped, "scripted/replay", "scripted")
    assert rec.cost.tool_calls == 1                       # the write happened
    assert rec.status.value == "BUDGET_EXCEEDED"          # but the loop could not take a second step


# ---------------------------------------------------------------- V3

@pytest.mark.parametrize("task_id", TASK_IDS)
async def test_v3_replay_succeeds_through_all_stages(factory, task_id):
    rec = await run_one(factory, factory.tasks.get(task_id), factory.recipes["v3-staged"], "scripted/replay",
                        "scripted")
    assert rec.status.value == "SUCCESS", rec.grades
    assert [s["name"] for s in _spans(rec, "handoff")] == ["stage:inspect", "stage:act", "stage:verify"]
    assert not rec.violations


async def test_v3_refuses_writes_in_a_read_only_stage(factory):
    backend = ScriptedBackend([
        {"tool_calls": [{"name": "fs__write_file", "arguments": {"path": "notes/todo.txt", "content": "buy milk"}}]},
        {"content": "inspected"},                                          # inspect ends
        {"tool_calls": [{"name": "fs__write_file", "arguments": {"path": "notes/todo.txt", "content": "buy milk"}}]},
        {"content": "done"},                                               # act ends
        {"content": "Created notes/todo.txt."},                            # verify ends
    ])
    rec = await run_one(factory, factory.tasks.get("fs-01"), factory.recipes["v3-staged"], "scripted/replay",
                        "scripted", backend=backend)
    refused = [v for v in rec.violations if v.kind == "tool_not_allowed"]
    assert len(refused) == 1 and "stage:inspect" in refused[0].detail
    assert rec.status.value == "SUCCESS"                                  # the act-stage write still landed
    tool_spans = _spans(rec, "tool_call")
    assert [s["attributes"]["scope"] for s in tool_spans] == ["stage:inspect", "stage:act"]


async def test_v3_verify_can_send_the_run_back_to_act(factory):
    backend = ScriptedBackend([
        {"content": "nothing to read"},                                    # inspect
        {"content": "done"},                                               # act: forgot to write
        {"content": "RETRY: notes/todo.txt does not exist"},               # verify
        {"tool_calls": [{"name": "fs__write_file", "arguments": {"path": "notes/todo.txt", "content": "buy milk"}}]},
        {"content": "done"},                                               # act again
        {"content": "Created notes/todo.txt."},                            # verify again
    ])
    rec = await run_one(factory, factory.tasks.get("fs-01"), factory.recipes["v3-staged"], "scripted/replay",
                        "scripted", backend=backend)
    assert rec.status.value == "SUCCESS"
    assert [s["name"] for s in _spans(rec, "handoff")] == [
        "stage:inspect", "stage:act", "stage:verify", "stage:act", "stage:verify"]


async def test_v3_retries_are_bounded(factory):
    backend = ScriptedBackend([{"content": "RETRY: still wrong"}] * 10)
    rec = await run_one(factory, factory.tasks.get("fs-01"), factory.recipes["v3-staged"], "scripted/replay",
                        "scripted", backend=backend)
    stages = [s["name"] for s in _spans(rec, "handoff")]
    assert stages.count("stage:verify") == 2                               # max_retries: 1
    assert rec.status.value == "FAIL"


async def test_v3_stage_step_cap_moves_the_run_on(factory):
    looping = {"tool_calls": [{"name": "fs__list_dir", "arguments": {"path": "."}}]}
    backend = ScriptedBackend([looping] * 4 + [               # inspect hits its cap of 4
        {"tool_calls": [{"name": "fs__write_file", "arguments": {"path": "notes/todo.txt", "content": "buy milk"}}]},
        {"content": "done"}, {"content": "Created notes/todo.txt."}])
    rec = await run_one(factory, factory.tasks.get("fs-01"), factory.recipes["v3-staged"], "scripted/replay",
                        "scripted", backend=backend)
    assert rec.status.value == "SUCCESS"
    assert any(v.kind == "loop" for v in rec.violations)                  # trajectory grader saw the repeats


# ---------------------------------------------------------------- recipe contract

def test_unset_optional_fields_keep_recipe_identity(factory):
    """Optional fields (workflow, config.call_timeout_s) are omitted from the hash when unset. Pinned so an
    accidental identity change fails here; re-pin only with a PR note (CLAUDE.md, Run identity)."""
    r = factory.recipes["v2-base"]
    assert r.content_hash() == "79abd03818943f65"
    timed = r.model_copy(update={"config": r.config.model_copy(update={"call_timeout_s": 30})})
    assert timed.content_hash() != r.content_hash()


def test_workflow_changes_recipe_identity(factory):
    r = factory.recipes["v3-staged"]
    wf = r.workflow.model_copy(update={"max_retries": 0})
    assert r.model_copy(update={"workflow": wf}).content_hash() != r.content_hash()


def test_workflow_spec_rejects_bad_retry_target_and_duplicates():
    with pytest.raises(ValidationError):
        WorkflowSpec(stages=[{"id": "a", "prompt": "stage/act"}], retry_from="b")
    with pytest.raises(ValidationError):
        WorkflowSpec(stages=[{"id": "a", "prompt": "stage/act"}, {"id": "a", "prompt": "stage/act"}])


def test_validator_workflow_rules(factory):
    v2, v3 = factory.recipes["v2-base"], factory.recipes["v3-staged"]
    assert validate_recipe(v3, factory.declared) == []
    rules = lambda r: {i.rule for i in validate_recipe(r, factory.declared)}
    assert "workflow_required" in rules(v3.model_copy(update={"workflow": None}))
    assert "workflow_unused" in rules(v2.model_copy(update={"workflow": v3.workflow}))
    bad = v3.workflow.model_copy(deep=True)
    bad.stages[0].tools = ["web.fetch"]
    bad.stages[1].prompt = "stage/missing"
    assert {"stage_tool_resolves", "prompt_exists"} <= rules(v3.model_copy(update={"workflow": bad}))

async def test_v3_verification_reread_is_not_wasted(factory):
    """inspect reads, act writes, verify re-reads: the re-read follows a write, so it is verification (Q = 1)."""
    content = "[server]\nhost = 0.0.0.0\nport = 9090\n\n[logging]\nlevel = info\n"
    read = {"tool_calls": [{"name": "fs__read_file", "arguments": {"path": "config/app.ini"}}]}
    backend = ScriptedBackend([
        read, {"content": "port is 8080"},
        {"tool_calls": [{"name": "fs__write_file", "arguments": {"path": "config/app.ini", "content": content}}]},
        {"content": "Changed the port to 9090."},
        read, {"content": "Changed the port to 9090."},
    ])
    rec = await run_one(factory, factory.tasks.get("fs-02"), factory.recipes["v3-staged"], "scripted/replay",
                        "scripted", backend=backend)
    assert rec.status.value == "SUCCESS" and rec.scores.trajectory == 1.0


async def test_rereading_without_a_change_in_between_is_wasted(factory):
    read = {"tool_calls": [{"name": "fs__read_file", "arguments": {"path": "config/app.ini"}}]}
    content = "[server]\nhost = 0.0.0.0\nport = 9090\n\n[logging]\nlevel = info\n"
    backend = ScriptedBackend([read, read, {"tool_calls": [{"name": "fs__write_file", "arguments": {
        "path": "config/app.ini", "content": content}}]}, {"content": "done"}])
    rec = await run_one(factory, factory.tasks.get("fs-02"), factory.recipes["v2-base"], "scripted/replay",
                        "scripted", backend=backend)
    assert rec.scores.trajectory == pytest.approx(1 - 1 / 3)


async def test_stage_prompts_list_only_that_stages_tools(factory):
    from harness_factory.substrate.prompts import PromptAssembler
    task, recipe = factory.tasks.get("fs-02"), factory.recipes["v3-staged"]
    prompts = PromptAssembler().stage_prompts(recipe, task, ["fs__list_dir", "fs__read_file", "fs__write_file"])
    assert "fs__write_file" not in prompts["inspect"] and "fs__write_file" not in prompts["verify"]
    assert "fs__write_file" in prompts["act"]
