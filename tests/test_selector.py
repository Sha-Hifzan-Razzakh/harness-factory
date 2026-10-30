"""Selector (SEL-01..06), on real scripted runs over the P0 tasks."""

import json

import pytest
from typer.testing import CliRunner

from harness_factory import settings
from harness_factory.provision.provisioner import task_class
from harness_factory.results.cost import CostBaseline
from harness_factory.runner.runner import plan, run_campaign
from harness_factory.select.policy import (
    build_policy,
    evidence_rows,
    load_policy,
    log_decision,
    plan_evaluation,
    save_policy,
)
from harness_factory.select.selector import decide, load_selector_config


@pytest.fixture
def config():
    return load_selector_config().model_copy(update={"min_runs": 1})     # P0 classes hold 1-2 tasks each


@pytest.fixture
def own_var(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "VAR", tmp_path / "var")
    return tmp_path / "var"


async def campaign(factory, recipes, model, task_ids, seeds=1):
    await run_campaign(factory, plan(factory, recipes, "scripted", [model], seeds, task_ids=task_ids), concurrency=4)
    rows = factory.store.rows()
    return rows, CostBaseline.from_rows(rows)


def cls(factory, task_id):
    return task_class(factory.tasks.get(task_id), factory.declared)


async def test_measured_effectiveness_ranks_and_the_winner_is_chosen(factory, p0_tasks, config):
    rows, b = await campaign(factory, ["v2-base", "v2-recover"], "scripted/invent", p0_tasks)
    d = decide(cls(factory, "fs-02"), "scripted/invent", "scripted", factory=factory, rows=rows, baseline=b,
               config=config)
    assert d.rule == "ranked" and d.chosen == ["v2-recover"]
    assert [c.recipe_id for c in d.ranked] == ["v2-recover", "v2-base"] and d.ranked[1].mean_e == 0
    assert d.evidence_runs == sum(c.n for c in d.ranked)


async def test_overlapping_intervals_are_a_close_call_and_both_run(factory, p0_tasks, config):
    # replay never triggers recovery: same prompt, same calls, same cost, so the same E
    rows, b = await campaign(factory, ["v2-base", "v2-recover"], "scripted/replay", p0_tasks)
    d = decide(cls(factory, "fs-02"), "scripted/replay", "scripted", factory=factory, rows=rows, baseline=b,
               config=config)
    assert d.rule == "close_call" and sorted(d.chosen) == ["v2-base", "v2-recover"]
    cheaper, _ = await campaign(factory, ["v2-plan-check"], "scripted/replay", p0_tasks)
    d = decide(cls(factory, "fs-02"), "scripted/replay", "scripted", factory=factory, rows=cheaper,
               baseline=CostBaseline.from_rows(cheaper), config=config)
    assert "v2-plan-check" not in d.chosen      # its longer prompt costs more every run: a measured loss, not a tie


async def test_cold_start_and_too_few_runs_fall_back_to_the_rule_default(factory, p0_tasks):
    d = decide(cls(factory, "fs-02"), "scripted/replay", "scripted", factory=factory, rows=[],
               baseline=CostBaseline.from_rows([]))
    assert d.rule == "cold_start" and d.chosen == ["v2-base"] and d.evidence_runs == 0
    rows, b = await campaign(factory, ["v2-recover"], "scripted/invent", ["fs-02"])
    d = decide(cls(factory, "fs-02"), "scripted/invent", "scripted", factory=factory, rows=rows, baseline=b)
    assert d.rule == "cold_start" and [c.recipe_id for c in d.insufficient] == ["v2-recover"]   # n=1 < min_runs 3
    unknown = decide("UNKNOWN_PATH|REVERSIBLE|R1", "scripted/replay", "scripted", factory=factory, rows=[],
                     baseline=CostBaseline.from_rows([]))
    assert unknown.chosen == ["v3-staged"]


async def test_results_of_an_older_recipe_version_never_decide(factory, p0_tasks, config):
    rows, b = await campaign(factory, ["v2-recover"], "scripted/invent", p0_tasks)
    r = factory.recipes["v2-recover"]
    factory.recipes["v2-recover"] = r.model_copy(update={"config": r.config.model_copy(update={"max_steps": 14})})
    d = decide(cls(factory, "fs-02"), "scripted/invent", "scripted", factory=factory, rows=rows, baseline=b,
               config=config)
    assert d.rule == "cold_start" and d.evidence_runs == 0      # the edited recipe has no results of its own yet


def test_hard_rules_prune_before_anything_is_measured(factory, config):
    d = decide("KNOWN_PATH|REVERSIBLE|R1", "scripted/replay", "scripted", factory=factory, rows=[],
               baseline=CostBaseline.from_rows([]), config=config)
    assert ("v1-base", "shape_fits_depth") in [(p.recipe_id, p.rule) for p in d.pruned]
    base = factory.recipes["v2-base"]
    factory.recipes["fs-only"] = base.model_copy(update={"id": "fs-only", "tools": base.tools.model_copy(
        update={"tool_ids": ["fs.*"], "mcp_servers": ["fs"]})})
    sql = factory.tasks.get("sql-01")
    d = decide(cls(factory, "sql-01"), "scripted/replay", "scripted", factory=factory, rows=[],
               baseline=CostBaseline.from_rows([]), config=config, task=sql)
    assert [p.rule for p in d.pruned if p.recipe_id == "fs-only"] == ["reaches_task_tools"]
    no_tools = factory.providers.model_copy(deep=True)            # a model without tool calling: only V1 is left
    no_tools.served("scripted", "scripted/replay").caps.tool_calling = "none"
    factory.providers = no_tools
    d = decide("SINGLE_STEP|REVERSIBLE|R1", "scripted/replay", "scripted", factory=factory, rows=[],
               baseline=CostBaseline.from_rows([]), config=config)
    assert {p.recipe_id for p in d.pruned if p.rule == "model_tool_calling"} >= {"v2-base", "v3-staged"}
    assert d.rule == "cold_start" and d.chosen == ["v1-base"]   # every default pruned: the one shape left


async def test_policies_are_frozen_by_content_and_decisions_are_logged(factory, p0_tasks, config, own_var):
    rows, b = await campaign(factory, ["v2-base", "v2-recover"], "scripted/invent", p0_tasks)
    p = build_policy(factory, rows, b, "scripted/invent", "scripted", config=config)
    path = save_policy(p)
    again = build_policy(factory, rows, b, "scripted/invent", "scripted", config=config)
    assert again.id == p.id and save_policy(again) == path                   # same decisions, same policy
    loaded = load_policy(p.id)
    assert loaded.decisions[cls(factory, "fs-02")].chosen == ["v2-recover"]
    with pytest.raises(KeyError, match="saved: " + p.id):
        load_policy("nope")
    logged = json.loads(log_decision(p.decisions[cls(factory, "fs-02")], task="fs-02@1").read_text())
    assert logged["decision"]["rule"] == "ranked" and logged["context"]["task"] == "fs-02@1"
    assert logged["decision"]["ranked"][0]["recipe_id"] == "v2-recover"


async def test_held_out_runs_never_inform_the_held_out_picks(factory, p0_tasks, config):
    held = [t.id for t in factory.tasks.by_split(__import__("harness_factory.tasks.model", fromlist=["Split"]).Split.HELD_OUT)]
    rows, _ = await campaign(factory, ["v2-base", "v2-recover"], "scripted/invent", p0_tasks + held)
    assert {r["task_version"].split("@")[0] for r in rows} >= set(held)
    ev = evidence_rows(rows, set(held))
    assert not {r["task_version"].split("@")[0] for r in ev} & set(held)
    ep = plan_evaluation(factory, rows, "scripted/invent", "scripted", config=config)
    assert set(ep.picks["selector"]) == set(held)
    for task_id, d in ep.decisions.items():
        held_cls = cls(factory, task_id)
        in_evidence = sum(1 for r in ev if r["task_class"] == held_cls)
        assert d.evidence_runs <= in_evidence                    # counted from evidence only
    assert ep.picks["rules"] == {t: load_selector_config().default_for(cls(factory, t)) for t in held}
    assert len(ep.runs()) <= 4 * len(held)                       # strategies that agree share a run


def test_provision_selects_logs_and_runs(factory, own_var, monkeypatch):
    from harness_factory.cli import main as cli
    monkeypatch.setattr(cli, "_factory", lambda store=True: factory)
    out = CliRunner().invoke(cli.app, ["provision", "--task", "fs-01", "--model", "scripted/replay",
                                       "--provider", "scripted"])
    assert out.exit_code == 0, out.output
    assert "cold start" in " ".join(out.output.split())
    assert len(list((own_var / "decisions").glob("*.json"))) == 1
    last = factory.store.rows()[-1]
    assert last["recipe_id"] == "v2-base" and last["status"] == "SUCCESS"
