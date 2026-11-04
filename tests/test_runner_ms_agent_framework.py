"""`ms-agent-framework` runner (ADR 0016): the same recipe run by Microsoft Agent Framework's own `Agent`.

The offline checks of tests/test_runners.py for this runner: the framework's own agent drives the run; every model call
goes through ModelGateway and every tool call through ToolExecutor; runs end the way native runs end; the framework
version is identity and native hashes don't move; the transfer check against native runs offline.
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
RID = "v2-ms-agent-framework"


def spans(rec, kind):
    return [s for s in map(json.loads, Path(rec.trace_path).read_text().splitlines()) if s["kind"] == kind]


def snapshot(rec) -> dict:
    return next(g for g in rec.grades if g["grader_id"] == "state_diff")["snapshot"]


def run_error(rec) -> str:
    return next(g["detail"] for g in rec.grades if g["grader_id"] == "run_error")


def with_config(recipe, **cfg):
    return recipe.model_copy(update={"config": recipe.config.model_copy(update=cfg)})


# ------------------------------------------------------------------ identity

def test_the_runner_is_identity_and_native_hashes_stay(factory):
    base, runner = factory.recipes["v2-base"], factory.recipes[RID]
    assert base.content_hash() == "79abd03818943f65"                               # unchanged by this runner
    assert runner.runner == "ms-agent-framework" and runner.content_hash() != base.content_hash()
    assert counterpart_issues(base, runner) == []                                  # differs only in runtime
    version = identity.package_version("agent-framework-core")
    assert version is not None and int(version.split(".")[0]) >= 1                # the GA line
    assert identity.runner_identity("ms-agent-framework") == {
        "runtime": "ms-agent-framework", "packages": {"agent-framework-core": version}}


def test_an_agent_framework_upgrade_changes_only_this_runners_runs(factory, monkeypatch):
    task = factory.tasks.get("fs-01")
    recipes = [factory.recipes[r] for r in ("v2-base", "v2-langgraph-prebuilt", RID)]

    def key(recipe):
        return run_key_for(task, recipe, MODEL, "scripted", 0, declared=factory.declared,
                           providers=factory.providers).as_str()

    before = {r.id: (r.content_hash(), key(r)) for r in recipes}
    real = identity.package_version
    monkeypatch.setattr(identity, "package_version",
                        lambda d: "99.0.0" if d == "agent-framework-core" else real(d))
    after = {r.id: (r.content_hash(), key(r)) for r in recipes}
    assert after["v2-base"] == before["v2-base"] and after["v2-langgraph-prebuilt"] == before["v2-langgraph-prebuilt"]
    assert after[RID][0] != before[RID][0] and after[RID][1] != before[RID][1]


def test_it_is_the_tool_loop_only_and_a_missing_package_refuses_the_run(factory, monkeypatch):
    d = factory.declared
    assert validate_recipe(factory.recipes[RID], d) == []

    def rules(r):
        return {i.rule for i in validate_recipe(r, d)}

    assert "runtime_shape" in rules(factory.recipes["v3-staged"].model_copy(update={"runtime": "ms-agent-framework"}))
    assert "runtime_harness_tools" in rules(factory.recipes["v2-recover"].model_copy(
        update={"runtime": "ms-agent-framework"}))
    real = identity.package_version
    monkeypatch.setattr(identity, "package_version", lambda dist: None if dist == "agent-framework-core" else real(dist))
    assert validate_recipe(factory.recipes[RID], d) == []                          # hfx validate: the recipe is fine
    issues = validate_recipe(factory.recipes[RID], d, providers=factory.providers, provider_id="scripted")
    assert [i.rule for i in issues] == ["runner_installed"] and "ms-agent-framework" in issues[0].detail


# ------------------------------------------------------------------ the framework's own agent does the work

async def test_runner_solves_the_p0_tasks_offline(factory, p0_tasks):
    result = await run_campaign(factory, plan(factory, RID, "scripted", [MODEL], task_ids=p0_tasks), concurrency=5)
    assert not result.errors
    assert [r.status.value for r in result.records] == ["SUCCESS"] * len(p0_tasks)


async def test_runs_are_the_frameworks_agent_tool_loop_and_mcp_tool(factory, monkeypatch):
    from agent_framework import Agent, FunctionInvocationLayer, FunctionTool, MCPStdioTool
    from agent_framework._mcp import MCPTool

    from harness_factory.runners import ms_agent_framework as maf
    from harness_factory.shapes.base import ShapeRegistry

    built, loops, invoked, mcp_calls = [], [], [], []
    real_build, real_loop = maf.build_agent, FunctionInvocationLayer.get_response
    real_invoke, real_call = FunctionTool.invoke, MCPTool.call_tool

    def spy_build(*a, **kw):
        built.append(real_build(*a, **kw))
        return built[-1]

    def spy_loop(self, *a, **kw):
        loops.append(1)
        return real_loop(self, *a, **kw)

    async def spy_invoke(self, *a, **kw):
        invoked.append(self.name)
        return await real_invoke(self, *a, **kw)

    async def spy_call(self, tool_name, **kw):
        mcp_calls.append(tool_name)
        return await real_call(self, tool_name, **kw)

    monkeypatch.setattr(maf, "build_agent", spy_build)
    monkeypatch.setattr(FunctionInvocationLayer, "get_response", spy_loop)
    monkeypatch.setattr(FunctionTool, "invoke", spy_invoke)
    monkeypatch.setattr(MCPTool, "call_tool", spy_call)
    monkeypatch.setattr(ShapeRegistry, "get", classmethod(lambda cls, v: pytest.fail("native shape used")))
    rec = await run_one(factory, factory.tasks.get("mix-01"), factory.recipes[RID], MODEL, "scripted")
    assert rec.status.value == "SUCCESS", rec.grades
    (agent,) = built
    assert type(agent) is Agent and isinstance(agent.client, FunctionInvocationLayer)
    (mcp,) = agent.mcp_tools
    assert type(mcp) is MCPStdioTool and all(isinstance(t, FunctionTool) for t in mcp.functions)
    assert loops == [1]                                    # one agent run: one framework tool loop
    names = [s["name"].replace(".", "__", 1) for s in spans(rec, "tool_call")]
    assert invoked == mcp_calls == names and len(names) > 1   # every tool call was the framework's MCP function
    assert agent.client.calls == rec.cost.model_calls > 1     # every model call was one of its round trips
    assert agent.client.forced_end is None
    assert snapshot(rec)["runner"]["agent"] == "Agent"


# ------------------------------------------------------------------ measurement points

async def test_every_model_call_goes_through_the_gateway_and_every_tool_call_through_the_executor(factory):
    bucket = factory.providers.bucket("scripted", MODEL)
    slots_before = factory.ledger._totals[bucket]
    rec = await run_one(factory, factory.tasks.get("mix-01"), factory.recipes[RID], MODEL, "scripted")
    assert rec.status.value == "SUCCESS"
    runner = snapshot(rec)["runner"]
    model_spans, tool_spans = spans(rec, "model_call"), spans(rec, "tool_call")
    assert runner["framework_model_calls"] == len(model_spans) == rec.cost.model_calls \
        == factory.ledger._totals[bucket] - slots_before > 1
    assert runner["framework_tool_calls"] == len(tool_spans) == rec.cost.tool_calls > 0
    assert all("permission" in s["attributes"] for s in tool_spans)            # ToolExecutor's policy step ran


async def test_a_policy_denied_write_is_refused_and_recorded(factory):
    r = factory.recipes[RID]
    ro = r.model_copy(update={"policies": r.policies.model_copy(
        update={"permission_mode": {"READ": "AUTO", "REVERSIBLE": "DENY", "IRREVERSIBLE": "DENY"}})})
    rec = await run_one(factory, factory.tasks.get("fs-01"), ro, MODEL, "scripted")
    assert any(v.kind == "policy_denied" for v in rec.violations)
    assert rec.status.value == "FAIL"
    (write,) = [s for s in spans(rec, "tool_call") if s["name"] == "fs.write_file"]
    assert write["attributes"]["permission"] == "DENY" and write["error"] == "POLICY"
    # the framework's default for an MCP error result: the model sees its generic text, not the server's
    assert [m["content"] for m in rec.messages if m["role"] == "tool"] == ["Error: Function failed."]


async def test_an_injected_fault_fires_on_a_runner_tool_call(factory):
    factory.faults = {**factory.faults, "t": FaultConfig(id="t", rules=[{"tools": ["fs.write_file"], "kind": "error",
                                                                         "nth": [1]}])}
    rec = await run_one(factory, factory.tasks.get("fs-01"), factory.recipes[RID], MODEL, "scripted", fault="t")
    (write,) = [s for s in spans(rec, "tool_call") if s["name"] == "fs.write_file"]
    assert write["attributes"]["fault"] == "error"
    assert rec.status.value == "FAIL"                                  # the one write never happened
    assert "injected" not in json.dumps(rec.messages).lower()          # the model never learns it was injected


# ------------------------------------------------------------------ how runs end

async def test_a_budget_stop_ends_a_runner_run_like_a_native_one(factory):
    task = factory.tasks.get("fs-01")              # one write, then the final answer: max_steps 1 stops before it
    native = await run_one(factory, task, with_config(factory.recipes["v2-base"], max_steps=1), MODEL, "scripted")
    rec = await run_one(factory, task, with_config(factory.recipes[RID], max_steps=1), MODEL, "scripted")
    for r in (native, rec):
        assert r.status.value == "PARTIAL" and r.scores.outcome == 0.5          # solved, overran: scaled
        assert [v.kind for v in r.violations] == ["budget_exceeded"]
        assert "max_steps 1" in run_error(r)
    assert rec.cost.model_calls == native.cost.model_calls == 1 and rec.cost.tool_calls == native.cost.tool_calls


async def test_a_tool_budget_stop_inside_the_frameworks_tool_call_decides_the_run(factory):
    read = {"tool_calls": [{"name": "fs__read_file", "arguments": {"path": "notes/README.txt"}}]}
    tight = with_config(factory.recipes[RID], max_tool_calls=2)
    rec = await run_one(factory, factory.tasks.get("fs-01"), tight, MODEL, "scripted",
                        backend=ScriptedBackend([read] * 5))
    assert rec.status.value == "FAIL" and rec.cost.tool_calls == 2     # the third call never reached a tool
    assert [v.kind for v in rec.violations] == ["budget_exceeded"]
    assert "max_tool_calls" in run_error(rec)                          # our stop, not the framework's error text


class QuotaGone:
    async def complete(self, messages, tools, slot, provider, served) -> Completion:
        raise HarnessError(ErrorKind.QUOTA_EXHAUSTED, "daily quota spent")


async def test_provider_infrastructure_failure_is_incomplete(factory):
    rec = await run_one(factory, factory.tasks.get("fs-01"), factory.recipes[RID], MODEL, "scripted",
                        backend=QuotaGone())
    assert rec.status.value == "INCOMPLETE" and rec.composite == 0


async def test_the_frameworks_iteration_cap_is_a_harness_stop(factory):
    read = {"tool_calls": [{"name": "fs__read_file", "arguments": {"path": "notes/README.txt"}}]}
    roomy = with_config(factory.recipes[RID], max_steps=60, max_tool_calls=60, max_tokens=10_000_000)
    rec = await run_one(factory, factory.tasks.get("fs-01"), roomy, MODEL, "scripted",
                        backend=ScriptedBackend([read] * 60))
    assert rec.cost.model_calls == 41 and rec.cost.tool_calls == 40    # DEFAULT_MAX_ITERATIONS, then tools off
    assert rec.status.value == "FAIL" and rec.scores.outcome == 0      # nothing written; graded, scaled
    assert "budget_exceeded" in [v.kind for v in rec.violations]
    assert "framework max_iterations" in run_error(rec)


async def test_the_frameworks_consecutive_error_limit_is_a_harness_stop(factory):
    bad = {"tool_calls": [{"name": "fs__read_file", "arguments": {"path": "no/such/file.txt"}}]}
    rec = await run_one(factory, factory.tasks.get("fs-01"), factory.recipes[RID], MODEL, "scripted",
                        backend=ScriptedBackend([bad] * 10))
    assert rec.cost.tool_calls == 3 and rec.cost.model_calls == 4     # 3 failing batches, then tools off
    assert "framework max_consecutive_errors" in run_error(rec)


# ------------------------------------------------------------------ transfer check (RUN-05)

async def test_transfer_check_against_native_on_the_p0_tasks(factory, p0_tasks):
    result = await run_campaign(factory, plan(factory, ["v2-base", RID], "scripted", [MODEL], task_ids=p0_tasks),
                                concurrency=6)
    assert not result.errors
    tr = transfer(factory.store.rows(), factory.providers, factory.recipes["v2-base"], factory.recipes[RID], MODEL,
                  "scripted", min_pairs=len(p0_tasks))
    assert tr.verdict == "equivalent", tr.reason
    assert (tr.n, tr.ties, tr.mean, tr.call_ratio) == (len(p0_tasks), len(p0_tasks), 0.0, 1.0)
    assert tr.framework == {"agent-framework-core": identity.package_version("agent-framework-core")}
    assert factory.store.save_transfer(tr) == factory.store.save_transfer(tr)       # idempotent
