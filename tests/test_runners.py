"""Framework runners (ADR 0016): the same recipe run by LangGraph's ready-made agent and by LlamaIndex's FunctionAgent.

What must hold for every runner, offline with the scripted models:
- the framework's own agent drives the run (not the native loop, not a loop of ours);
- every model call goes through ModelGateway (a quota slot, a model_call span) and every tool call through
  ToolExecutor (a tool_call span, permissions, faults);
- runs end the way native runs end (DONE, budget, provider infrastructure), graded on the environment;
- the runner and its framework version are identity; native recipes keep their hashes.
"""

import json
from pathlib import Path

import pytest

from harness_factory.env.faults import FaultConfig
from harness_factory.provision.provisioner import run_key_for
from harness_factory.recipes.validate import validate_recipe
from harness_factory.results.transfer import counterpart_issues, transfer
from harness_factory.runner.runner import plan, run_campaign, run_one
from harness_factory.runners import identity
from harness_factory.substrate.errors import ErrorKind, HarnessError
from harness_factory.substrate.gateway import Completion, ScriptedBackend

MODEL = "scripted/replay"
RUNNERS = ["v2-langgraph-prebuilt", "v2-llamaindex-agent"]


def spans(rec, kind):
    return [s for s in map(json.loads, Path(rec.trace_path).read_text().splitlines()) if s["kind"] == kind]


def snapshot(rec) -> dict:
    return next(g for g in rec.grades if g["grader_id"] == "state_diff")["snapshot"]


def with_config(recipe, **cfg):
    return recipe.model_copy(update={"config": recipe.config.model_copy(update=cfg)})


# ------------------------------------------------------------------ identity

def test_runtime_unset_or_native_leaves_every_hash_alone(factory):
    base = factory.recipes["v2-base"]
    assert base.runtime is None and base.content_hash() == "79abd03818943f65"      # unchanged by this field
    assert base.model_copy(update={"runtime": "native"}).content_hash() == base.content_hash()
    for rid, r in factory.recipes.items():
        if r.runner is None:
            assert r.model_copy(update={"runtime": "native"}).content_hash() == r.content_hash(), rid
    runner = factory.recipes["v2-langgraph-prebuilt"]
    assert runner.content_hash() != base.content_hash()
    assert counterpart_issues(base, runner) == []


def test_a_framework_upgrade_changes_a_runner_runs_identity_only(factory, monkeypatch):
    task = factory.tasks.get("fs-01")
    native, lg, li = (factory.recipes[r] for r in ("v2-base", *RUNNERS))

    def key(recipe):
        return run_key_for(task, recipe, MODEL, "scripted", 0, declared=factory.declared,
                           providers=factory.providers).as_str()

    before = {r.id: (r.content_hash(), key(r)) for r in (native, lg, li)}
    real = identity.package_version
    monkeypatch.setattr(identity, "package_version", lambda d: "99.0.0" if d == "langgraph" else real(d))
    after = {r.id: (r.content_hash(), key(r)) for r in (native, lg, li)}
    assert after[native.id] == before[native.id]                     # native never depends on framework versions
    assert after[li.id] == before[li.id]                             # nor does a runner on another framework
    assert after[lg.id][0] != before[lg.id][0] and after[lg.id][1] != before[lg.id][1]


def test_a_framework_runtime_is_the_tool_loop_and_nothing_the_native_runtime_adds(factory):
    d = factory.declared
    for rid in RUNNERS:
        assert validate_recipe(factory.recipes[rid], d) == []

    def rules(r):
        return {i.rule for i in validate_recipe(r, d)}

    assert "runtime_shape" in rules(factory.recipes["v3-staged"].model_copy(update={"runtime": "llamaindex-agent"}))
    assert "runtime_shape" in rules(factory.recipes["v1-base"].model_copy(update={"runtime": "langgraph-prebuilt"}))
    assert "runtime_harness_tools" in rules(factory.recipes["v2-delegate"].model_copy(
        update={"runtime": "langgraph-prebuilt"}))
    assert "runtime_harness_tools" in rules(factory.recipes["v2-recover"].model_copy(
        update={"runtime": "llamaindex-agent"}))
    delegating = factory.recipes["v2-delegate"].model_copy(deep=True)
    delegating.delegation._child = factory.recipes["v2-llamaindex-agent"]      # a helper may not be a runner
    assert "runtime_harness_tools" in rules(delegating)


def test_a_missing_framework_refuses_the_run_not_the_recipe(factory, monkeypatch):
    real = identity.package_version
    monkeypatch.setattr(identity, "package_version", lambda d: None if d == "llama-index-core" else real(d))
    r = factory.recipes["v2-llamaindex-agent"]
    assert validate_recipe(r, factory.declared) == []                      # hfx validate: the recipe is fine
    issues = validate_recipe(r, factory.declared, providers=factory.providers, provider_id="scripted")
    assert [i.rule for i in issues] == ["runner_installed"] and "llamaindex" in issues[0].detail


# ------------------------------------------------------------------ the framework's own agent does the work

@pytest.mark.parametrize("rid", RUNNERS)
async def test_runner_solves_the_p0_tasks_offline(factory, p0_tasks, rid):
    result = await run_campaign(factory, plan(factory, rid, "scripted", [MODEL], task_ids=p0_tasks), concurrency=5)
    assert not result.errors
    assert [r.status.value for r in result.records] == ["SUCCESS"] * len(p0_tasks)


async def test_langgraph_runs_are_create_agent_with_tool_node(factory, monkeypatch):
    from langchain_core.tools import StructuredTool
    from langgraph.graph.state import CompiledStateGraph
    from langgraph.prebuilt import ToolNode

    from harness_factory.runners import langgraph_prebuilt as lg
    from harness_factory.shapes.base import ShapeRegistry

    built, tool_runs = [], []
    real_build, real_ainvoke = lg.build_agent, StructuredTool.ainvoke

    def spy_build(*a, **kw):
        built.append(real_build(*a, **kw))
        return built[-1]

    async def spy_ainvoke(self, *a, **kw):
        tool_runs.append(self.name)
        return await real_ainvoke(self, *a, **kw)

    monkeypatch.setattr(lg, "build_agent", spy_build)
    monkeypatch.setattr(StructuredTool, "ainvoke", spy_ainvoke)
    monkeypatch.setattr(ShapeRegistry, "get", classmethod(lambda cls, v: pytest.fail("native shape used")))
    rec = await run_one(factory, factory.tasks.get("fs-02"), factory.recipes["v2-langgraph-prebuilt"], MODEL,
                        "scripted")
    assert rec.status.value == "SUCCESS", rec.grades
    (agent,) = built
    assert isinstance(agent, CompiledStateGraph)
    assert isinstance(agent.nodes["tools"].bound, ToolNode)                        # LangGraph's prebuilt ToolNode
    assert snapshot(rec)["runner"]["nodes"] == ["model", "tools"]                 # create_agent's graph
    assert tool_runs == [s["name"].replace(".", "__", 1) for s in spans(rec, "tool_call")]   # adapter tools ran


async def test_llamaindex_runs_are_a_function_agent_with_mcp_tool_spec_tools(factory, monkeypatch):
    from llama_index.core.agent.workflow import FunctionAgent
    from llama_index.core.tools import FunctionTool

    from harness_factory.runners import llamaindex_agent as li
    from harness_factory.shapes.base import ShapeRegistry

    built, steps = [], []
    real_build, real_step = li.build_agent, FunctionAgent.take_step

    async def spy_build(*a, **kw):
        built.append(await real_build(*a, **kw))
        return built[-1]

    async def spy_step(self, *a, **kw):
        steps.append(1)
        return await real_step(self, *a, **kw)

    monkeypatch.setattr(li, "build_agent", spy_build)
    monkeypatch.setattr(FunctionAgent, "take_step", spy_step)
    monkeypatch.setattr(ShapeRegistry, "get", classmethod(lambda cls, v: pytest.fail("native shape used")))
    rec = await run_one(factory, factory.tasks.get("fs-02"), factory.recipes["v2-llamaindex-agent"], MODEL,
                        "scripted")
    assert rec.status.value == "SUCCESS"
    (agent,) = built
    assert type(agent) is FunctionAgent and all(isinstance(t, FunctionTool) for t in agent.tools)
    assert len(steps) == rec.cost.model_calls > 1                  # every model call was one FunctionAgent step


# ------------------------------------------------------------------ measurement points

@pytest.mark.parametrize("rid", RUNNERS)
async def test_every_model_call_goes_through_the_gateway_and_every_tool_call_through_the_executor(factory, rid):
    bucket = factory.providers.bucket("scripted", MODEL)
    slots_before = factory.ledger._totals[bucket]
    rec = await run_one(factory, factory.tasks.get("mix-01"), factory.recipes[rid], MODEL, "scripted")
    assert rec.status.value == "SUCCESS"
    runner = snapshot(rec)["runner"]
    model_spans, tool_spans = spans(rec, "model_call"), spans(rec, "tool_call")
    assert runner["framework_model_calls"] == len(model_spans) == rec.cost.model_calls \
        == factory.ledger._totals[bucket] - slots_before > 1
    assert runner["framework_tool_calls"] == len(tool_spans) == rec.cost.tool_calls > 0
    assert all("permission" in s["attributes"] for s in tool_spans)            # ToolExecutor's policy step ran


@pytest.mark.parametrize("rid", RUNNERS)
async def test_a_policy_denied_write_is_refused_and_recorded(factory, rid):
    r = factory.recipes[rid]
    ro = r.model_copy(update={"policies": r.policies.model_copy(
        update={"permission_mode": {"READ": "AUTO", "REVERSIBLE": "DENY", "IRREVERSIBLE": "DENY"}})})
    rec = await run_one(factory, factory.tasks.get("fs-01"), ro, MODEL, "scripted")
    assert any(v.kind == "policy_denied" for v in rec.violations)
    assert rec.status.value == "FAIL"
    (write,) = [s for s in spans(rec, "tool_call") if s["name"] == "fs.write_file"]
    assert write["attributes"]["permission"] == "DENY" and write["error"] == "POLICY"


@pytest.mark.parametrize("rid", RUNNERS)
async def test_an_injected_fault_fires_on_a_runner_tool_call(factory, rid):
    factory.faults = {**factory.faults, "t": FaultConfig(id="t", rules=[{"tools": ["fs.write_file"], "kind": "error",
                                                                         "nth": [1]}])}
    rec = await run_one(factory, factory.tasks.get("fs-01"), factory.recipes[rid], MODEL, "scripted", fault="t")
    (write,) = [s for s in spans(rec, "tool_call") if s["name"] == "fs.write_file"]
    assert write["attributes"]["fault"] == "error"
    assert rec.status.value == "FAIL"                                  # the one write never happened
    assert "injected" not in json.dumps(rec.messages).lower()          # the model never learns it was injected


# ------------------------------------------------------------------ how runs end

@pytest.mark.parametrize("rid", RUNNERS)
async def test_a_budget_stop_ends_a_runner_run_like_a_native_one(factory, rid):
    task = factory.tasks.get("fs-01")              # one write, then the final answer: max_steps 1 stops before it
    native = await run_one(factory, task, with_config(factory.recipes["v2-base"], max_steps=1), MODEL, "scripted")
    rec = await run_one(factory, task, with_config(factory.recipes[rid], max_steps=1), MODEL, "scripted")
    for r in (native, rec):
        assert r.status.value == "PARTIAL" and r.scores.outcome == 0.5          # solved, overran: scaled
        assert [v.kind for v in r.violations] == ["budget_exceeded"]
        assert "max_steps 1" in next(g["detail"] for g in r.grades if g["grader_id"] == "run_error")
    assert rec.cost.model_calls == native.cost.model_calls == 1 and rec.cost.tool_calls == native.cost.tool_calls


class QuotaGone:
    async def complete(self, messages, tools, slot, provider, served) -> Completion:
        raise HarnessError(ErrorKind.QUOTA_EXHAUSTED, "daily quota spent")


@pytest.mark.parametrize("rid", RUNNERS)
async def test_provider_infrastructure_failure_is_incomplete(factory, rid):
    rec = await run_one(factory, factory.tasks.get("fs-01"), factory.recipes[rid], MODEL, "scripted",
                        backend=QuotaGone())
    assert rec.status.value == "INCOMPLETE" and rec.composite == 0


async def test_llamaindex_iteration_cap_is_a_harness_stop(factory):
    read = {"tool_calls": [{"name": "fs__read_file", "arguments": {"path": "notes/README.txt"}}]}
    roomy = with_config(factory.recipes["v2-llamaindex-agent"], max_steps=40, max_tool_calls=40)
    rec = await run_one(factory, factory.tasks.get("fs-01"), roomy, MODEL, "scripted",
                        backend=ScriptedBackend([read] * 40))
    assert rec.cost.model_calls == 20                                  # FunctionAgent's DEFAULT_MAX_ITERATIONS
    assert "budget_exceeded" in [v.kind for v in rec.violations]
    assert "framework max_iterations" in next(g["detail"] for g in rec.grades if g["grader_id"] == "run_error")


# ------------------------------------------------------------------ transfer check (RUN-05)

async def test_transfer_check_pairs_native_and_runner_runs(factory, p0_tasks):
    rids = ["v2-base", *RUNNERS]
    result = await run_campaign(factory, plan(factory, rids, "scripted", [MODEL], task_ids=p0_tasks), concurrency=6)
    assert not result.errors
    rows = factory.store.rows()
    for rid in RUNNERS:
        tr = transfer(rows, factory.providers, factory.recipes["v2-base"], factory.recipes[rid], MODEL, "scripted",
                      min_pairs=len(p0_tasks))
        assert tr.verdict == "equivalent", tr.reason
        assert (tr.n, tr.ties, tr.mean, tr.call_ratio) == (len(p0_tasks), len(p0_tasks), 0.0, 1.0)
        assert tr.framework == identity.framework_versions(tr.runtime)
        assert factory.store.save_transfer(tr) == factory.store.save_transfer(tr)       # idempotent
    assert len(factory.store.transfers()) == len(RUNNERS)
    few = transfer(rows, factory.providers, factory.recipes["v2-base"], factory.recipes[RUNNERS[0]], MODEL,
                   "scripted")
    assert few.verdict == "inconclusive"                               # 5 pairs < the default 20
    with pytest.raises(ValueError, match="also differ in tools"):
        transfer(rows, factory.providers, factory.recipes["v2-search"], factory.recipes[RUNNERS[0]], MODEL, "scripted")
