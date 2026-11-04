"""More framework runners (ADR 0016, #56): LangGraph's supervisor, Pydantic AI's Agent, the OpenAI Agents SDK.

The offline checks of tests/test_runners.py, for each runner here:
- the framework's own agent drives the run (not the native loop, not a loop of ours);
- every model call goes through ModelGateway (a quota slot, a model_call span) and every tool call through
  ToolExecutor (a tool_call span, permissions, faults);
- runs end the way native runs end (DONE, budget, provider infrastructure, the framework's own limits);
- the runner and its framework version are identity; native recipes keep their hashes;
- the transfer check against native runs works offline.
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
RUNNERS = ["v2-langgraph-supervisor", "v2-pydantic-ai", "v2-openai-agents"]
SUPERVISOR_CALLS = 2        # the supervisor's handoff, then its own final answer after the worker's


def spans(rec, kind):
    return [s for s in map(json.loads, Path(rec.trace_path).read_text().splitlines()) if s["kind"] == kind]


def snapshot(rec) -> dict:
    return next(g for g in rec.grades if g["grader_id"] == "state_diff")["snapshot"]


def with_config(recipe, **cfg):
    return recipe.model_copy(update={"config": recipe.config.model_copy(update=cfg)})


def run_error(rec) -> str:
    return next(g["detail"] for g in rec.grades if g["grader_id"] == "run_error")


@pytest.fixture
def no_native(monkeypatch):
    from harness_factory.shapes.base import ShapeRegistry
    monkeypatch.setattr(ShapeRegistry, "get", classmethod(lambda cls, v: pytest.fail("native shape used")))


# ------------------------------------------------------------------ identity

def test_new_runtimes_leave_native_hashes_alone(factory):
    base = factory.recipes["v2-base"]
    assert base.content_hash() == "79abd03818943f65"
    for rid in RUNNERS:
        r = factory.recipes[rid]
        assert r.runner == r.runtime and r.content_hash() != base.content_hash()
        assert counterpart_issues(base, r) == []                   # they differ from v2-base only in runtime


@pytest.mark.parametrize(("rid", "dist"), [("v2-langgraph-supervisor", "langgraph-supervisor"),
                                           ("v2-pydantic-ai", "pydantic-ai-slim"),
                                           ("v2-openai-agents", "openai-agents")])
def test_a_framework_upgrade_changes_only_that_runners_identity(factory, monkeypatch, rid, dist):
    task = factory.tasks.get("fs-01")
    recipes = [factory.recipes[r] for r in ("v2-base", "v2-langgraph-prebuilt", *RUNNERS)]

    def key(recipe):
        return recipe.content_hash(), run_key_for(task, recipe, MODEL, "scripted", 0, declared=factory.declared,
                                                  providers=factory.providers).as_str()

    assert identity.package_version(dist) is not None             # installed with dev, version in the identity
    assert identity.runner_identity(factory.recipes[rid].runtime)["packages"][dist] == identity.package_version(dist)
    before = {r.id: key(r) for r in recipes}
    real = identity.package_version
    monkeypatch.setattr(identity, "package_version", lambda d: "99.0.0" if d == dist else real(d))
    after = {r.id: key(r) for r in recipes}
    for r in recipes:
        changed = after[r.id][0] != before[r.id][0] and after[r.id][1] != before[r.id][1]
        assert changed == (r.id == rid), r.id


@pytest.mark.parametrize("runtime", ["langgraph-supervisor", "pydantic-ai", "openai-agents"])
def test_new_runtimes_are_the_tool_loop_only(factory, runtime):
    d = factory.declared

    def rules(r):
        return {i.rule for i in validate_recipe(r, d)}

    assert validate_recipe(factory.recipes["v2-base"].model_copy(update={"runtime": runtime}), d) == []
    for rid in ("v1-base", "v3-staged", "v4-planner", "v5-orchestrator"):
        assert "runtime_shape" in rules(factory.recipes[rid].model_copy(update={"runtime": runtime})), rid
    assert "runtime_harness_tools" in rules(factory.recipes["v2-delegate"].model_copy(update={"runtime": runtime}))
    assert "runtime_harness_tools" in rules(factory.recipes["v2-recover"].model_copy(update={"runtime": runtime}))


@pytest.mark.parametrize(("rid", "dist", "extra"), [("v2-langgraph-supervisor", "langgraph-supervisor",
                                                     "langgraph-supervisor"),
                                                    ("v2-pydantic-ai", "pydantic-ai-slim", "pydantic-ai"),
                                                    ("v2-openai-agents", "openai-agents", "openai-agents")])
def test_a_missing_framework_refuses_the_run_not_the_recipe(factory, monkeypatch, rid, dist, extra):
    real = identity.package_version
    monkeypatch.setattr(identity, "package_version", lambda d: None if d == dist else real(d))
    r = factory.recipes[rid]
    assert validate_recipe(r, factory.declared) == []
    issues = validate_recipe(r, factory.declared, providers=factory.providers, provider_id="scripted")
    assert [i.rule for i in issues] == ["runner_installed"] and f"`{extra}`" in issues[0].detail


# ------------------------------------------------------------------ the framework's own agent does the work

@pytest.mark.parametrize("rid", RUNNERS)
async def test_runner_solves_the_p0_tasks_offline(factory, p0_tasks, rid):
    result = await run_campaign(factory, plan(factory, rid, "scripted", [MODEL], task_ids=p0_tasks), concurrency=5)
    assert not result.errors
    assert [r.status.value for r in result.records] == ["SUCCESS"] * len(p0_tasks)


async def test_supervisor_runs_are_create_supervisor_over_a_create_agent_worker(factory, monkeypatch):
    from langchain_core.tools import StructuredTool
    from langgraph.graph.state import CompiledStateGraph
    from langgraph.prebuilt import ToolNode

    from harness_factory.runners import langgraph_supervisor as sup

    built, tool_runs = [], []
    real_build, real_ainvoke = sup.build_team, StructuredTool.ainvoke

    def spy_build(*a, **kw):
        built.append(real_build(*a, **kw))
        return built[-1]

    async def spy_ainvoke(self, *a, **kw):
        tool_runs.append(self.name)
        return await real_ainvoke(self, *a, **kw)

    monkeypatch.setattr(sup, "build_team", spy_build)
    monkeypatch.setattr(StructuredTool, "ainvoke", spy_ainvoke)
    task = factory.tasks.get("fs-02")
    native = await run_one(factory, task, factory.recipes["v2-base"], MODEL, "scripted")
    from harness_factory.shapes.base import ShapeRegistry
    monkeypatch.setattr(ShapeRegistry, "get", classmethod(lambda cls, v: pytest.fail("native shape used")))
    rec = await run_one(factory, task, factory.recipes["v2-langgraph-supervisor"], MODEL, "scripted")
    assert rec.status.value == "SUCCESS", rec.grades
    ((team, worker),) = built
    assert isinstance(team, CompiledStateGraph) and isinstance(worker, CompiledStateGraph)
    assert isinstance(worker.nodes["tools"].bound, ToolNode)                     # the worker: create_agent's graph
    runner = snapshot(rec)["runner"]
    assert runner["nodes"] == ["supervisor", "worker"] and runner["worker_nodes"] == ["model", "tools"]
    worker_tools = [s["name"].replace(".", "__", 1) for s in spans(rec, "tool_call")]
    assert tool_runs == ["transfer_to_worker", *worker_tools]       # the framework's handoff, then the adapter tools
    assert rec.cost.model_calls == native.cost.model_calls + SUPERVISOR_CALLS
    assert rec.cost.tool_calls == native.cost.tool_calls             # the handoff is not an environment tool call


async def test_pydantic_ai_runs_are_an_agent_with_its_mcp_toolset(factory, monkeypatch, no_native):
    from pydantic_ai import Agent
    from pydantic_ai.mcp import MCPToolset

    from harness_factory.runners import pydantic_ai_agent as pa

    built, runs, tool_runs, requests = [], [], [], []
    real_build, real_run, real_call = pa.build_agent, Agent.run, MCPToolset.call_tool
    real_request = pa.GatewayModel.request

    def spy_build(*a, **kw):
        built.append(real_build(*a, **kw))
        return built[-1]

    async def spy_run(self, *a, **kw):
        runs.append(self)
        return await real_run(self, *a, **kw)

    async def spy_call(self, name, *a, **kw):
        tool_runs.append(name)
        return await real_call(self, name, *a, **kw)

    async def spy_request(self, *a, **kw):
        requests.append(1)
        return await real_request(self, *a, **kw)

    monkeypatch.setattr(pa, "build_agent", spy_build)
    monkeypatch.setattr(Agent, "run", spy_run)
    monkeypatch.setattr(MCPToolset, "call_tool", spy_call)
    monkeypatch.setattr(pa.GatewayModel, "request", spy_request)
    rec = await run_one(factory, factory.tasks.get("fs-02"), factory.recipes["v2-pydantic-ai"], MODEL, "scripted")
    assert rec.status.value == "SUCCESS", rec.grades
    (agent,) = built
    assert type(agent) is Agent and runs == [agent]
    assert any(type(t) is MCPToolset for t in agent.toolsets)
    assert tool_runs == [s["name"].replace(".", "__", 1) for s in spans(rec, "tool_call")]   # its toolset ran them
    assert len(requests) == rec.cost.model_calls > 1                 # every model call was a Model.request


async def test_openai_agents_runs_are_an_agent_run_by_runner_with_its_mcp_util(factory, monkeypatch, no_native):
    from agents import Agent, Runner
    from agents.mcp.util import MCPUtil

    from harness_factory.runners import openai_agents as oa

    built, runs, tool_runs = [], [], []
    real_build, real_run, real_invoke = oa.build_agent, Runner.run, MCPUtil.invoke_mcp_tool

    def spy_build(*a, **kw):
        built.append(real_build(*a, **kw))
        return built[-1]

    async def spy_run(cls, agent, *a, **kw):
        runs.append((agent, kw.get("run_config")))
        return await real_run(agent, *a, **kw)

    async def spy_invoke(cls, server, tool, *a, **kw):
        tool_runs.append(tool.name)
        return await real_invoke(server, tool, *a, **kw)

    monkeypatch.setattr(oa, "build_agent", spy_build)
    monkeypatch.setattr(Runner, "run", classmethod(spy_run))
    monkeypatch.setattr(MCPUtil, "invoke_mcp_tool", classmethod(spy_invoke))
    rec = await run_one(factory, factory.tasks.get("fs-02"), factory.recipes["v2-openai-agents"], MODEL, "scripted")
    assert rec.status.value == "SUCCESS", rec.grades
    (agent,) = built
    ((ran, config),) = runs
    assert type(agent) is Agent and ran is agent and config.tracing_disabled      # no trace export (the one change)
    assert tool_runs == [s["name"].replace(".", "__", 1) for s in spans(rec, "tool_call")]   # the SDK invoked them


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

@pytest.mark.parametrize(("rid", "steps"), [("v2-langgraph-supervisor", 1 + 1),   # + the supervisor's handoff
                                            ("v2-pydantic-ai", 1), ("v2-openai-agents", 1)])
async def test_a_budget_stop_ends_a_runner_run_like_a_native_one(factory, rid, steps):
    task = factory.tasks.get("fs-01")              # one write, then the final answer: the step cap stops before it
    native = await run_one(factory, task, with_config(factory.recipes["v2-base"], max_steps=1), MODEL, "scripted")
    rec = await run_one(factory, task, with_config(factory.recipes[rid], max_steps=steps), MODEL, "scripted")
    for r, cap in ((native, 1), (rec, steps)):
        assert r.status.value == "PARTIAL" and r.scores.outcome == 0.5          # solved, overran: scaled
        assert [v.kind for v in r.violations] == ["budget_exceeded"]
        assert f"max_steps {cap}" in run_error(r)
    assert rec.cost.model_calls == steps and rec.cost.tool_calls == native.cost.tool_calls


class QuotaGone:
    async def complete(self, messages, tools, slot, provider, served) -> Completion:
        raise HarnessError(ErrorKind.QUOTA_EXHAUSTED, "daily quota spent")


@pytest.mark.parametrize("rid", RUNNERS)
async def test_provider_infrastructure_failure_is_incomplete(factory, rid):
    rec = await run_one(factory, factory.tasks.get("fs-01"), factory.recipes[rid], MODEL, "scripted",
                        backend=QuotaGone())
    assert rec.status.value == "INCOMPLETE" and rec.composite == 0


async def test_openai_agents_turn_limit_is_a_harness_stop(factory):
    read = {"tool_calls": [{"name": "fs__read_file", "arguments": {"path": "notes/README.txt"}}]}
    roomy = with_config(factory.recipes["v2-openai-agents"], max_steps=40, max_tool_calls=40)
    rec = await run_one(factory, factory.tasks.get("fs-01"), roomy, MODEL, "scripted",
                        backend=ScriptedBackend([read] * 40))
    assert rec.cost.model_calls == 10                                  # the SDK's DEFAULT_MAX_TURNS
    assert "budget_exceeded" in [v.kind for v in rec.violations]
    assert "framework max_turns" in run_error(rec)


async def test_pydantic_ai_tool_retry_cap_is_a_harness_stop(factory):
    missing = {"tool_calls": [{"name": "fs__read_file", "arguments": {"path": "no/such/file.txt"}}]}
    rec = await run_one(factory, factory.tasks.get("fs-01"), factory.recipes["v2-pydantic-ai"], MODEL, "scripted",
                        backend=ScriptedBackend([missing] * 5))
    assert rec.cost.model_calls == rec.cost.tool_calls == 2           # retries=1: the second error ends the run
    assert "budget_exceeded" in [v.kind for v in rec.violations]
    assert "framework max_retries" in run_error(rec)


# ------------------------------------------------------------------ transfer check (RUN-05)

async def test_transfer_check_pairs_native_and_runner_runs(factory, p0_tasks):
    rids = ["v2-base", *RUNNERS]
    result = await run_campaign(factory, plan(factory, rids, "scripted", [MODEL], task_ids=p0_tasks), concurrency=6)
    assert not result.errors
    rows = factory.store.rows()
    for rid in RUNNERS:
        tr = transfer(rows, factory.providers, factory.recipes["v2-base"], factory.recipes[rid], MODEL, "scripted",
                      min_pairs=len(p0_tasks))
        assert tr.verdict == "equivalent", (rid, tr.reason)
        assert (tr.n, tr.ties, tr.mean) == (len(p0_tasks), len(p0_tasks), 0.0)
        assert tr.call_ratio > 1.0 if rid == "v2-langgraph-supervisor" else tr.call_ratio == 1.0
        assert tr.framework == identity.framework_versions(tr.runtime)
        factory.store.save_transfer(tr)
    assert len(factory.store.transfers()) == len(RUNNERS)
