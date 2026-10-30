"""V5 orchestrator shape: the planner plans, and each step goes to a worker (a delegate call to the recipe's
`delegation` child) that starts from a fresh conversation.

Offline: `scripted/*` models plan in the planner slot and do one plan step per worker ("Your part: ..."). Planner and
workers share one backend, as they share one gateway, so ScriptedBackend scripts list their calls in order."""

import json
import shutil
from pathlib import Path

from harness_factory.recipes.model import load_recipes
from harness_factory.recipes.validate import validate_recipe
from harness_factory.runner.runner import run_one
from harness_factory.substrate.gateway import ScriptedBackend

READ = {"tool_calls": [{"name": "fs__read_file", "arguments": {"path": "notes/README.txt"}}]}
WRITE = {"tool_calls": [{"name": "fs__write_file", "arguments": {"path": "notes/todo.txt", "content": "buy milk"}}]}
PLAN2 = {"content": "1. Read notes/README.txt with fs.read_file\n2. Write notes/todo.txt with fs.write_file"}


def recipes(tmp_path, **files) -> dict:
    for f in ("v5_orchestrator", "v2_tool_loop", "v4_planner"):
        shutil.copy(f"catalog/recipes/base/{f}.yaml", tmp_path / f"{f}.yaml")
    for name, text in files.items():
        (tmp_path / f"{name}.yaml").write_text(text)
    return load_recipes(tmp_path)


def spans(rec):
    return [json.loads(line) for line in Path(rec.trace_path).read_text().splitlines()]


def snapshot(rec):
    return json.loads(Path(rec.transcript_path).read_text())["snapshot"]


def workers(rec):
    return [s for s in spans(rec) if s["kind"] == "tool_call" and s["name"] == "delegate.run"]


# ------------------------------------------------------------------ identity and validation

def test_v5_needs_a_plan_a_worker_prompt_and_enough_workers(factory, tmp_path):
    assert factory.recipes["v2-base"].content_hash() == "79abd03818943f65"            # unchanged
    assert factory.recipes["v4-planner"].content_hash() == "c7a8990da8b06ddf"         # worker_prompt omitted
    rs = recipes(tmp_path,
                 noworkers="id: noworkers\nbase: v5-orchestrator\ndelegation: null\n",
                 noprompt="id: noprompt\nbase: v5-orchestrator\nplanning: {worker_prompt: null}\n",
                 few="id: few\nbase: v5-orchestrator\ndelegation: {recipe: v2-base, max_calls: 4}\n",
                 v4worker="id: v4worker\nbase: v4-planner\nplanning: {max_plan_steps: 8, step_max_steps: 3, "
                          "max_replans: 1, worker_prompt: plan/worker}\n")
    rules = lambda r: {i.rule for i in validate_recipe(rs[r], factory.declared)}
    assert "workers_required" in rules("noworkers")
    assert "worker_prompt_required" in rules("noprompt")
    assert "workers_fit_plan" in rules("few")                    # 8 steps x (1 + 1 replan) = 16 calls
    assert "worker_prompt_unused" in rules("v4worker")
    assert not rules("v5-orchestrator")


# ------------------------------------------------------------------ behaviour

async def test_each_step_is_a_worker_with_a_fresh_conversation(factory):
    rec = await run_one(factory, factory.tasks.get("fs-02"), factory.recipes["v5-orchestrator"], "scripted/replay",
                        "scripted")
    assert rec.status.value == "SUCCESS" and rec.scores.trajectory == 1.0
    ws = workers(rec)
    assert [w["attributes"]["scope"] for w in ws] == ["step:1", "step:2"]
    assert all(w["attributes"]["child_status"] == "finished" for w in ws)
    work = [(s["name"], s["attributes"]["scope"]) for s in spans(rec)
            if s["kind"] == "tool_call" and s["name"] != "delegate.run"]
    assert work == [("fs.read_file", "delegate:1"), ("fs.write_file", "delegate:2")]     # the orchestrator calls none
    snap = snapshot(rec)
    first, second = (d["messages"] for d in snap["delegations"])
    assert len(first) < len(second) + 2 and first[1]["content"] != second[1]["content"]
    assert "Earlier workers reported" in second[1]["content"]           # what worker 1 said, not its conversation
    assert "fs__read_file" not in json.dumps(second[2:])                 # worker 2 never saw worker 1's calls
    assert rec.output == snap["delegations"][-1]["output"]
    assert rec.cost.model_calls == 1 + sum(w["attributes"]["child_steps"] for w in ws)   # workers charged to the run


async def test_a_blocked_worker_sends_the_rest_back_to_the_planner(factory):
    script = [PLAN2, READ, {"content": "BLOCKED: the README says the todo comes first"},
              {"content": "1. Write notes/todo.txt with fs.write_file"}, WRITE, {"content": "Created it."}]
    rec = await run_one(factory, factory.tasks.get("fs-01"), factory.recipes["v5-orchestrator"], "scripted/replay",
                        "scripted", backend=ScriptedBackend(script))
    assert rec.status.value == "SUCCESS"
    snap = snapshot(rec)
    assert [p["kind"] for p in snap["plans"]] == ["plan", "replan"] and snap["replans"] == 1
    assert [s["name"] for s in spans(rec) if s["kind"] == "handoff" and not s["name"].startswith("delegate")] == \
        ["plan", "step:1", "replan", "step:2"]


async def test_a_worker_that_stops_early_counts_as_blocked(factory, tmp_path):
    rs = recipes(tmp_path, w1="id: w1\nbase: v2-base\nconfig: {max_steps: 1}\n",
                 orch="id: orch\nbase: v5-orchestrator\ndelegation: {recipe: w1, max_calls: 16}\n")
    script = [PLAN2, READ, {"content": "1. Write notes/todo.txt with fs.write_file"}, WRITE]
    rec = await run_one(factory, factory.tasks.get("fs-01"), rs["orch"], "scripted/replay", "scripted",
                        backend=ScriptedBackend(script))
    snap = snapshot(rec)
    assert [p["kind"] for p in snap["plans"]] == ["plan", "replan"]          # the stop triggered the replan
    assert snap["plan_trail"][0]["result"].startswith("Helper stopped early")
    assert any(v.kind == "delegate_stopped" for v in rec.violations)         # not the orchestrator's own overrun
    assert rec.scores.outcome > 0                                            # the second worker wrote the file


async def test_a_worker_never_gets_more_than_the_orchestrator_may_use(factory, tmp_path):
    rs = recipes(tmp_path, ro="id: ro\nbase: v5-orchestrator\n"
                              "policies: {permission_mode: {READ: AUTO, REVERSIBLE: DENY, IRREVERSIBLE: DENY}}\n")
    rec = await run_one(factory, factory.tasks.get("fs-01"), rs["ro"], "scripted/replay", "scripted",
                        backend=ScriptedBackend([{"content": "1. Write notes/todo.txt"}, WRITE, {"content": "done"}]))
    assert rec.scores.outcome == 0.0
    assert any(v.kind == "policy_denied" and v.detail.startswith("delegate 1") for v in rec.violations)


async def test_scripted_styles_behave_on_v5_as_they_do_on_v2(factory):
    for model, ok in (("scripted/replay", True), ("scripted/noisy", True), ("scripted/lazy", False)):
        rec = await run_one(factory, factory.tasks.get("fs-02"), factory.recipes["v5-orchestrator"], model, "scripted")
        assert (rec.status.value == "SUCCESS") is ok, model
