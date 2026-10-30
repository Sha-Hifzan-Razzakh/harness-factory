"""Delegation dimension (EXT-09, EXT-11, EXT-12): a delegate tool that runs a child recipe on a sub-task.

Offline, with the scripted provider: `scripted/delegate` hands the task to its helper when offered the tool, and
ScriptedBackend scripts drive the limits. Parent and child share one backend, as they share one gateway."""

import json
import shutil
from pathlib import Path

import pytest

from harness_factory.recipes.model import load_recipes
from harness_factory.recipes.validate import validate_recipe
from harness_factory.runner.runner import run_one
from harness_factory.substrate.gateway import ScriptedBackend

BASE = Path("catalog/recipes/base/v2_tool_loop.yaml")
DELEGATE = {"tool_calls": [{"name": "delegate__run", "arguments": {"task": "Write notes/todo.txt saying buy milk."}}]}
WRITE = {"tool_calls": [{"name": "fs__write_file", "arguments": {"path": "notes/todo.txt", "content": "buy milk"}}]}
READ = {"tool_calls": [{"name": "fs__read_file", "arguments": {"path": "notes/README.txt"}}]}


def recipes(tmp_path, **files) -> dict:
    """v2-base plus the given recipes (name -> yaml text), loaded the way the factory loads its catalog."""
    shutil.copy(BASE, tmp_path / "base.yaml")
    for name, text in files.items():
        (tmp_path / f"{name}.yaml").write_text(text)
    return load_recipes(tmp_path)


def spans(rec):
    return [json.loads(line) for line in Path(rec.trace_path).read_text().splitlines()]


def transcript(rec):
    return json.loads(Path(rec.transcript_path).read_text())


def delegate_spans(rec):
    return [s for s in spans(rec) if s["kind"] == "tool_call" and s["name"] == "delegate.run"]


# ------------------------------------------------------------------ identity

def test_v2_delegate_changes_one_dimension_and_the_childs_hash_is_part_of_its_own(factory, tmp_path):
    base, deleg = factory.recipes["v2-base"], factory.recipes["v2-delegate"]
    assert base.content_hash() == "79abd03818943f65"                       # unchanged: delegation omitted when None
    db, dd = base.model_dump(), deleg.model_dump()
    assert sorted(k for k in db if db[k] != dd[k]) == ["base", "delegation", "id"]
    helper = "id: helper\nbase: v2-base\nconfig: {max_steps: 4}\n"
    parent = "id: parent\nbase: v2-base\ndelegation: {recipe: helper}\n"
    before = recipes(tmp_path, helper=helper, parent=parent)["parent"].content_hash()
    after = recipes(tmp_path, helper=helper.replace("4", "5"), parent=parent)["parent"].content_hash()
    assert before != after                                                  # editing the helper is a new experiment


def test_delegation_cycles_and_self_delegation_are_refused_at_load(tmp_path):
    with pytest.raises(ValueError, match="delegation cycle: me -> me"):
        recipes(tmp_path, me="id: me\nbase: v2-base\ndelegation: {recipe: me}\n")
    (tmp_path / "me.yaml").unlink()
    with pytest.raises(ValueError, match="delegation cycle"):
        recipes(tmp_path, a="id: a\nbase: v2-base\ndelegation: {recipe: b}\n",
                b="id: b\nbase: v2-base\ndelegation: {recipe: a}\n")


def test_validation_needs_a_known_child_that_opens_no_extra_server(factory, tmp_path):
    rs = recipes(tmp_path, ghost="id: ghost\nbase: v2-base\ndelegation: {recipe: nobody}\n",
                 narrow="id: narrow\nbase: v2-base\ntools: {tool_ids: ['fs.*'], mcp_servers: [fs]}\n"
                        "delegation: {recipe: v2-base}\n")
    rules = lambda r: {i.rule for i in validate_recipe(rs[r], factory.declared)}
    assert "delegate_resolves" in rules("ghost")
    assert "delegate_servers" in rules("narrow")                           # v2-base also opens sql
    assert not rules("v2-base")


# ------------------------------------------------------------------ behaviour

async def test_a_delegated_run_succeeds_and_the_helpers_work_counts_in_the_parent_run(factory):
    rec = await run_one(factory, factory.tasks.get("fs-02"), factory.recipes["v2-delegate"], "scripted/delegate",
                        "scripted")
    assert rec.status.value == "SUCCESS" and rec.scores.trajectory == 1.0
    (d,) = delegate_spans(rec)
    assert d["attributes"]["child_status"] == "finished" and d["attributes"]["delegate_recipe"] == "v2-base"
    child_calls = [s for s in spans(rec) if s["kind"] == "tool_call" and s["attributes"].get("scope") == "delegate:1"]
    assert [s["name"] for s in child_calls] == ["fs.read_file", "fs.write_file"]          # the helper did the work
    assert rec.cost.model_calls == 2 + d["attributes"]["child_steps"]                      # parent 2 + helper 3
    assert rec.cost.tool_calls == 1 + d["attributes"]["child_tool_calls"]
    assert any(s["kind"] == "handoff" and s["name"] == "delegate:v2-base" for s in spans(rec))
    t = transcript(rec)
    (log,) = t["snapshot"]["delegations"]
    assert log["status"] == "finished" and log["task"].startswith("In config/app.ini")
    assert "delegate__run" in t["messages"][0]["content"]                  # the parent's prompt lists the tool
    assert "delegate__run" not in log["messages"][0]["content"]            # max_depth 1: the helper can't delegate


async def test_without_a_delegation_the_same_model_just_does_the_work(factory):
    rec = await run_one(factory, factory.tasks.get("fs-02"), factory.recipes["v2-base"], "scripted/delegate",
                        "scripted")
    assert rec.status.value == "SUCCESS" and not delegate_spans(rec)
    assert "delegations" not in transcript(rec)["snapshot"]


async def test_every_shape_gets_the_tool_v1_delegates_its_one_response(factory, tmp_path):
    v1 = Path("catalog/recipes/base/v1_single_call.yaml").read_text()
    rs = recipes(tmp_path, v1=v1, v1d="id: v1-delegate\nbase: v1-base\ndelegation: {recipe: v2-base}\n",
                 v1droom="id: v1-delegate-room\nbase: v1-base\nconfig: {max_steps: 6}\ndelegation: {recipe: v2-base}\n")
    fs02 = factory.tasks.get("fs-02")
    rec = await run_one(factory, fs02, rs["v1-delegate-room"], "scripted/delegate", "scripted")
    assert rec.status.value == "SUCCESS" and len(delegate_spans(rec)) == 1   # V1 alone never solves fs-02
    rec = await run_one(factory, fs02, rs["v1-delegate"], "scripted/delegate", "scripted")
    (d,) = delegate_spans(rec)                                              # max_steps 1: nothing left for a helper
    assert d["status"] == "error" and "not enough budget" in d["attributes"]["result_preview"]


async def test_calls_past_max_calls_are_refused_and_count_as_invalid(factory):
    script = [DELEGATE, {"content": "done 1"}, DELEGATE, {"content": "done 2"}, DELEGATE, {"content": "gave up"}]
    rec = await run_one(factory, factory.tasks.get("fs-01"), factory.recipes["v2-delegate"], "scripted/replay",
                        "scripted", backend=ScriptedBackend(script))
    ds = delegate_spans(rec)
    assert [s["status"] for s in ds] == ["ok", "ok", "error"]              # max_calls: 2
    assert "limit reached" in ds[2]["attributes"]["result_preview"]
    assert rec.scores.trajectory < 1.0


async def test_the_childs_grant_is_a_subset_of_its_parents(factory, tmp_path):
    rs = recipes(tmp_path,
                 readonly="id: readonly\nbase: v2-base\ntools: {tool_ids: [fs.list_dir, fs.read_file], mcp_servers: [fs]}\n"
                          "delegation: {recipe: writer}\n",
                 writer="id: writer\nbase: v2-base\ntools: {tool_ids: ['fs.*'], mcp_servers: [fs]}\n",
                 nowrite="id: nowrite\nbase: v2-base\n"
                         "policies: {permission_mode: {READ: AUTO, REVERSIBLE: DENY, IRREVERSIBLE: DENY}}\n"
                         "delegation: {recipe: v2-base}\n")
    fs01 = factory.tasks.get("fs-01")
    for parent, kind in (("readonly", "tool_not_allowed"), ("nowrite", "policy_denied")):
        rec = await run_one(factory, fs01, rs[parent], "scripted/replay", "scripted",
                            backend=ScriptedBackend([DELEGATE, WRITE, {"content": "wrote it"}, {"content": "done"}]))
        assert rec.scores.outcome == 0.0, parent                           # the helper could not write
        assert any(v.kind == kind and v.detail.startswith("delegate 1") for v in rec.violations), parent


async def test_the_childs_budget_comes_out_of_the_parents(factory, tmp_path):
    rs = recipes(tmp_path, tight="id: tight\nbase: v2-base\nconfig: {max_steps: 3}\ndelegation: {recipe: v2-base}\n")
    rec = await run_one(factory, factory.tasks.get("fs-01"), rs["tight"], "scripted/replay", "scripted",
                        backend=ScriptedBackend([DELEGATE, READ, READ, READ, READ, {"content": "done"}]))
    (d,) = delegate_spans(rec)
    assert d["attributes"]["child_steps"] == 2                            # 3 steps, the parent used 1 to delegate
    assert rec.cost.model_calls == 3                                       # never more than the parent's own cap
    kinds = {v.kind for v in rec.violations}
    assert "delegate_stopped" in kinds                                     # the helper's stop is not the parent's


async def test_v3_offers_the_tool_only_in_stages_whose_tools_allow_it(factory, tmp_path):
    rs = recipes(tmp_path, v3=Path("catalog/recipes/base/v3_staged.yaml").read_text(),
                 v3d="id: v3-delegate\nbase: v3-staged\ndelegation: {recipe: v2-base}\n")
    rec = await run_one(factory, factory.tasks.get("fs-02"), rs["v3-delegate"], "scripted/delegate", "scripted")
    (d,) = delegate_spans(rec)
    assert d["attributes"]["scope"] == "stage:act"                         # inspect and verify list read tools only
    assert rec.status.value == "SUCCESS"


# ------------------------------------------------------------------ a helper's outage is the run's outage (R-02)

OUTAGE = [{"fail": "PROVIDER_ERROR"}] * 3                     # one call, retried twice by the gateway, still failing


@pytest.fixture
def no_backoff(monkeypatch):
    from harness_factory.substrate.gateway import ModelGateway

    async def instant(_s):
        return None
    monkeypatch.setattr(ModelGateway, "_sleep", staticmethod(instant))


async def test_a_helpers_infrastructure_failure_ends_the_parent_run_incomplete(factory, no_backoff):
    """The parent must not carry on and be graded on work an outage cut short: the same outage in plain V2 is
    INCOMPLETE, so through a helper it is too."""
    script = [DELEGATE, *OUTAGE, WRITE, {"content": "done"}]          # without the fix the parent writes and passes
    rec = await run_one(factory, factory.tasks.get("fs-01"), factory.recipes["v2-delegate"], "scripted/replay",
                        "scripted", backend=ScriptedBackend(script))
    assert rec.status.value == "INCOMPLETE"
    assert "delegate 1" in transcript(rec)["error"] and "PROVIDER_ERROR" in transcript(rec)["error"]
    (d,) = delegate_spans(rec)
    assert d["status"] == "error" and d["attributes"]["child_status"] == "INCOMPLETE"
    assert transcript(rec)["snapshot"]["delegations"][0]["status"] == "INCOMPLETE"      # the child's record is kept


async def test_a_helpers_setup_failure_ends_the_parent_run_as_an_error(factory):
    script = [DELEGATE, {"fail": "CONFIG"}, WRITE, {"content": "done"}]                   # CONFIG is never retried
    rec = await run_one(factory, factory.tasks.get("fs-01"), factory.recipes["v2-delegate"], "scripted/replay",
                        "scripted", backend=ScriptedBackend(script))
    assert rec.status.value == "ERROR"
    assert "CONFIG" in transcript(rec)["error"]


@pytest.mark.parametrize(("recipe", "file", "lead"), [
    ("v3-staged", "v3_staged", {"content": "seen"}),                         # inspect stage, then act delegates
    ("v4-planner", "v4_planner", {"content": "1. Hand the write to a helper"}),
])
async def test_every_shape_ends_on_a_helpers_outage(factory, tmp_path, no_backoff, recipe, file, lead):
    shutil.copy(f"catalog/recipes/base/{file}.yaml", tmp_path / "shape.yaml")
    rs = recipes(tmp_path, d=f"id: d\nbase: {recipe}\ndelegation: {{recipe: v2-base}}\n")
    rec = await run_one(factory, factory.tasks.get("fs-01"), rs["d"], "scripted/replay", "scripted",
                        backend=ScriptedBackend([lead, DELEGATE, *OUTAGE, WRITE, {"content": "done"}]))
    assert rec.status.value == "INCOMPLETE"
    assert len(delegate_spans(rec)) == 1
