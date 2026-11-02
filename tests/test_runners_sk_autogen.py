"""`autogen-agentchat` runner and the migration check (ADR 0016, RUN-10, RUN-11).

The offline checks of tests/test_runners.py for AutoGen AgentChat's own `AssistantAgent`: the framework's own agent
and team drive the run; every model call goes through ModelGateway and every tool call through ToolExecutor; runs end
the way native runs end; the framework versions are identity and native hashes don't move. Then the migration check:
the same recipe on AutoGen and on Microsoft Agent Framework, paired and judged with the calibration rule.

Semantic Kernel has no runner: no release co-installs with the factory's lock (every semantic-kernel 1.x from 1.30
pins pydantic<2.14, 1.45+ also mcp<2; the factory runs pydantic 2.14 and MCP 2.3). The last test pins that down, so
the decision is revisited when a release lifts the pins.
"""

import json
from pathlib import Path

import pytest

from harness_factory.env.faults import FaultConfig
from harness_factory.provision.provisioner import run_key_for
from harness_factory.recipes.validate import validate_recipe
from harness_factory.results.transfer import Transfer, counterpart_issues, transfer
from harness_factory.runner.runner import plan, run_campaign, run_one
from harness_factory.runners import identity
from harness_factory.substrate.errors import ErrorKind, HarnessError
from harness_factory.substrate.gateway import Completion, ScriptedBackend

MODEL = "scripted/replay"
RID = "v2-autogen-agentchat"
MAF = "v2-ms-agent-framework"
AUTOGEN = ("autogen-agentchat", "autogen-core", "autogen-ext")


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
    assert runner.runner == "autogen-agentchat" and runner.content_hash() != base.content_hash()
    assert counterpart_issues(base, runner) == []                                  # differs only in runtime
    versions = {d: identity.package_version(d) for d in AUTOGEN}
    assert all(v is not None and v.startswith("0.7.") for v in versions.values())  # AgentChat 0.4+ line, one release
    assert identity.runner_identity("autogen-agentchat") == {"runtime": "autogen-agentchat", "packages": versions}


@pytest.mark.parametrize("dist", AUTOGEN)
def test_an_autogen_upgrade_changes_only_this_runners_runs(factory, monkeypatch, dist):
    task = factory.tasks.get("fs-01")
    recipes = [factory.recipes[r] for r in ("v2-base", MAF, RID)]

    def key(recipe):
        return run_key_for(task, recipe, MODEL, "scripted", 0, declared=factory.declared,
                           providers=factory.providers).as_str()

    before = {r.id: (r.content_hash(), key(r)) for r in recipes}
    real = identity.package_version
    monkeypatch.setattr(identity, "package_version", lambda d: "99.0.0" if d == dist else real(d))
    after = {r.id: (r.content_hash(), key(r)) for r in recipes}
    assert after["v2-base"] == before["v2-base"] and after[MAF] == before[MAF]
    assert after[RID][0] != before[RID][0] and after[RID][1] != before[RID][1]


def test_it_is_the_tool_loop_only_and_a_missing_package_refuses_the_run(factory, monkeypatch):
    d = factory.declared
    assert validate_recipe(factory.recipes[RID], d) == []

    def rules(r):
        return {i.rule for i in validate_recipe(r, d)}

    assert "runtime_shape" in rules(factory.recipes["v3-staged"].model_copy(update={"runtime": "autogen-agentchat"}))
    assert "runtime_harness_tools" in rules(factory.recipes["v2-recover"].model_copy(
        update={"runtime": "autogen-agentchat"}))
    real = identity.package_version
    monkeypatch.setattr(identity, "package_version", lambda dist: None if dist == "autogen-core" else real(dist))
    assert validate_recipe(factory.recipes[RID], d) == []                          # hfx validate: the recipe is fine
    issues = validate_recipe(factory.recipes[RID], d, providers=factory.providers, provider_id="scripted")
    assert [i.rule for i in issues] == ["runner_installed"] and "autogen-agentchat" in issues[0].detail


# ------------------------------------------------------------------ the framework's own agent does the work

async def test_runner_solves_the_p0_tasks_offline(factory, p0_tasks):
    result = await run_campaign(factory, plan(factory, RID, "scripted", [MODEL], task_ids=p0_tasks), concurrency=5)
    assert not result.errors
    assert [r.status.value for r in result.records] == ["SUCCESS"] * len(p0_tasks)


async def test_runs_are_the_frameworks_agent_team_and_workbench(factory, monkeypatch):
    from autogen_agentchat.agents import AssistantAgent
    from autogen_agentchat.teams import RoundRobinGroupChat
    from autogen_core.tools import BaseTool, StaticStreamWorkbench

    from harness_factory.runners import autogen_agentchat as ag
    from harness_factory.shapes.base import ShapeRegistry

    teams, turns, workbench_calls, tool_runs = [], [], [], []
    real_build, real_turn = ag.build_team, AssistantAgent.on_messages_stream
    real_wb, real_run_json = StaticStreamWorkbench.call_tool_stream, BaseTool.run_json

    async def spy_build(*a, **kw):
        teams.append(await real_build(*a, **kw))
        return teams[-1]

    def spy_turn(self, *a, **kw):
        turns.append(self.name)
        return real_turn(self, *a, **kw)

    def spy_wb(self, name, *a, **kw):
        workbench_calls.append(name)
        return real_wb(self, name, *a, **kw)

    async def spy_run_json(self, *a, **kw):
        tool_runs.append(self.name)
        return await real_run_json(self, *a, **kw)

    monkeypatch.setattr(ag, "build_team", spy_build)
    monkeypatch.setattr(AssistantAgent, "on_messages_stream", spy_turn)
    monkeypatch.setattr(StaticStreamWorkbench, "call_tool_stream", spy_wb)
    monkeypatch.setattr(BaseTool, "run_json", spy_run_json)
    monkeypatch.setattr(ShapeRegistry, "get", classmethod(lambda cls, v: pytest.fail("native shape used")))
    rec = await run_one(factory, factory.tasks.get("mix-01"), factory.recipes[RID], MODEL, "scripted")
    assert rec.status.value == "SUCCESS", rec.grades
    (team,) = teams
    assert type(team) is RoundRobinGroupChat
    (agent,) = team._participants
    assert type(agent) is AssistantAgent and agent._max_tool_iterations == 1          # AutoGen's defaults
    assert agent._reflect_on_tool_use is False and agent._model_client_stream is False
    assert [type(w) for w in agent._workbench] == [StaticStreamWorkbench]            # the agent's own workbench
    assert all(isinstance(t, ag.ExecutorTool) for t in agent._tools)
    names = [s["name"].replace(".", "__", 1) for s in spans(rec, "tool_call")]
    assert workbench_calls == tool_runs == names and len(names) > 1   # every tool call went through the workbench
    assert turns == ["assistant"] * rec.cost.model_calls              # one team turn per model call
    assert agent._model_client.calls == rec.cost.model_calls > 1
    assert snapshot(rec)["runner"].items() >= {"agent": "AssistantAgent", "team": "RoundRobinGroupChat"}.items()


async def test_what_the_framework_sends(factory):
    """No prompt text added; the OpenAI conversion's own additions are the `name` of the task message and the tool
    schema of the generated argument model (field titles, `strict: false`)."""
    from autogen_core.models import SystemMessage, UserMessage
    from mcp.types import Tool

    from harness_factory.runners.autogen_agentchat import ExecutorTool, to_wire
    rec = await run_one(factory, factory.tasks.get("fs-01"), factory.recipes[RID], MODEL, "scripted")
    native = await run_one(factory, factory.tasks.get("fs-01"), factory.recipes["v2-base"], MODEL, "scripted")
    system, task = rec.messages[0], rec.messages[1]
    assert system == native.messages[0] and task == {**native.messages[1], "name": "user"}
    tool = ExecutorTool(None, Tool(name="fs__write_file", description="Write a file.", input_schema={
        "type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}))
    wire, tools = to_wire([SystemMessage(content="s"), UserMessage(content="t", source="user")], [tool], "m")
    assert wire == [{"role": "system", "content": "s"}, {"role": "user", "content": "t", "name": "user"}]
    assert tools == [{"type": "function", "function": {
        "name": "fs__write_file", "description": "Write a file.", "strict": False,
        "parameters": {"type": "object", "properties": {"path": {"title": "Path", "type": "string"}},
                       "required": ["path"], "additionalProperties": False}}}]


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
    # AutoGen's default for a failing tool: the workbench reports the exception text, the executor's own message
    (seen,) = [m["content"] for m in rec.messages if m["role"] == "tool"]
    assert "denied" in seen.lower() and seen != "Error: Function failed."


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


@pytest.mark.parametrize("rid", [RID, MAF])
async def test_a_tool_budget_stop_inside_the_frameworks_tool_call_decides_the_run(factory, rid):
    """Both frameworks start a reply's calls at once: calls still running count against max_tool_calls."""
    read = {"name": "fs__read_file", "arguments": {"path": "notes/README.txt"}}
    tight = with_config(factory.recipes[rid], max_tool_calls=2)
    rec = await run_one(factory, factory.tasks.get("fs-01"), tight, MODEL, "scripted",
                        backend=ScriptedBackend([{"tool_calls": [read] * 3}] * 2))   # three calls in one reply
    assert rec.status.value == "FAIL" and rec.cost.tool_calls == 2     # the third call never reached a tool
    assert rec.cost.model_calls == 1           # the workbench reported the stop as a tool error; no 2nd model call
    assert [v.kind for v in rec.violations] == ["budget_exceeded"]
    assert "max_tool_calls" in run_error(rec)                          # our stop, not the team's error text


async def test_the_team_has_no_turn_cap_of_its_own_so_the_recipe_budget_ends_it(factory):
    read = {"tool_calls": [{"name": "fs__read_file", "arguments": {"path": "notes/README.txt"}}]}
    roomy = with_config(factory.recipes[RID], max_steps=45, max_tool_calls=100, max_tokens=10_000_000)
    rec = await run_one(factory, factory.tasks.get("fs-01"), roomy, MODEL, "scripted",
                        backend=ScriptedBackend([read] * 60))
    assert rec.cost.model_calls == 45 and rec.cost.tool_calls == 45    # past MAF's 40 and LlamaIndex's 20
    assert "budget_exceeded" in [v.kind for v in rec.violations] and "max_steps 45" in run_error(rec)


class QuotaGone:
    async def complete(self, messages, tools, slot, provider, served) -> Completion:
        raise HarnessError(ErrorKind.QUOTA_EXHAUSTED, "daily quota spent")


async def test_provider_infrastructure_failure_is_incomplete(factory):
    rec = await run_one(factory, factory.tasks.get("fs-01"), factory.recipes[RID], MODEL, "scripted",
                        backend=QuotaGone())
    assert rec.status.value == "INCOMPLETE" and rec.composite == 0


# ------------------------------------------------------------------ transfer and migration checks (RUN-05, RUN-11)

async def test_transfer_against_native_and_migration_check_against_ms_agent_framework(factory, p0_tasks):
    rids = ["v2-base", MAF, RID]
    result = await run_campaign(factory, plan(factory, rids, "scripted", [MODEL], task_ids=p0_tasks), concurrency=6)
    assert not result.errors
    rows, n = factory.store.rows(), len(p0_tasks)
    native = transfer(rows, factory.providers, factory.recipes["v2-base"], factory.recipes[RID], MODEL, "scripted",
                      min_pairs=n)
    assert native.verdict == "equivalent" and native.baseline_runtime is None
    migration = transfer(rows, factory.providers, factory.recipes[MAF], factory.recipes[RID], MODEL, "scripted",
                         min_pairs=n, between_runners=True)
    assert migration.verdict == "equivalent", migration.reason
    assert (migration.n, migration.ties, migration.mean, migration.call_ratio) == (n, n, 0.0, 1.0)
    assert migration.framework == identity.framework_versions("autogen-agentchat")
    assert (migration.baseline_runtime, migration.baseline_framework) == (
        "ms-agent-framework", identity.framework_versions("ms-agent-framework"))
    assert factory.store.save_transfer(migration) == factory.store.save_transfer(migration)   # idempotent
    saved = Transfer.model_validate(factory.store.transfers()[-1])
    assert saved.id == migration.id and saved.baseline_runtime == "ms-agent-framework"
    # the same record without the migration fields hashes as before: native checks keep their ids
    from harness_factory.hashing import content_hash
    old = {k: v for k, v in native.model_dump(mode="json").items()
           if k not in ("created_at", "note", "baseline_runtime", "baseline_framework")}
    assert native.id == content_hash(old, 12)


def test_the_migration_check_needs_two_different_runners(factory):
    base, maf, ag = factory.recipes["v2-base"], factory.recipes[MAF], factory.recipes[RID]
    assert counterpart_issues(maf, ag, between_runners=True) == []
    assert counterpart_issues(maf, ag) == [f"{MAF} is not native (runtime ms-agent-framework)"]   # unchanged default
    assert "has no framework runtime" in counterpart_issues(base, ag, between_runners=True)[0]
    assert "both run on" in counterpart_issues(ag, ag, between_runners=True)[0]
    with pytest.raises(ValueError, match="also differ in tools"):
        search_on_maf = factory.recipes["v2-search"].model_copy(update={"runtime": "ms-agent-framework"})
        transfer([], factory.providers, search_on_maf, ag, MODEL, "scripted", between_runners=True)


async def test_the_migration_command_reports_a_verdict(tmp_path, monkeypatch, p0_tasks):
    """`hfx transfer run <runner> --baseline <runner recipe>` on runs already in the store."""
    from typer.testing import CliRunner

    from harness_factory import settings
    from harness_factory.cli import main as cli
    from harness_factory.runner.runner import Factory
    monkeypatch.setattr(settings, "RESULTS_URL", f"sqlite:///{tmp_path / 'results.db'}")
    monkeypatch.setattr(settings, "QUOTA_STATE", tmp_path / "quota.json")
    monkeypatch.setattr(cli.console, "width", 300)          # the shared console was sized when it was imported
    f = Factory.load()
    result = await run_campaign(f, plan(f, [MAF, RID], "scripted", [MODEL], task_ids=p0_tasks), concurrency=6)
    assert not result.errors
    out = CliRunner().invoke(cli.app, ["transfer", "run", RID, "--baseline", MAF, "--model", MODEL,
                                       "--provider", "scripted", "--min-pairs", str(len(p0_tasks))])
    assert out.exit_code == 0, out.output
    assert "equivalent: the 95% interval" in out.output and "(ms-agent-framework)" in out.output
    bad = CliRunner().invoke(cli.app, ["transfer", "run", RID, "--baseline", "v2-base", "--model", MODEL,
                                       "--provider", "scripted"])
    assert bad.exit_code == 2 and "has no framework runtime" in bad.output


# ------------------------------------------------------------------ Semantic Kernel: left out, and why

def test_semantic_kernel_is_not_a_runner_while_it_pins_pydantic_below_2_14_and_mcp_below_2():
    from importlib import metadata

    from harness_factory.recipes.model import RuntimeName
    from harness_factory.runners.base import RunnerRegistry
    assert "semantic-kernel" not in RunnerRegistry.runtimes() and "semantic-kernel" not in identity.RUNNER_PACKAGES
    assert "semantic-kernel" not in RuntimeName.__args__
    assert int(metadata.version("pydantic").split(".")[1]) >= 14 and metadata.version("mcp").startswith("2.")
    with pytest.raises(metadata.PackageNotFoundError):
        metadata.version("semantic-kernel")
