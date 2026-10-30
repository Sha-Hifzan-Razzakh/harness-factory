"""Fault injection and robustness R."""

import pytest
from pydantic import ValidationError

from harness_factory.env.faults import FaultConfig, FaultInjector
from harness_factory.provision.provisioner import run_key_for
from harness_factory.results.cost import CostBaseline
from harness_factory.results.store import aggregate_rows, record_row, robustness
from harness_factory.runner.runner import plan, plan_status, run_campaign, run_one
from harness_factory.substrate.gateway import ScriptedBackend


def cfg(**rule) -> FaultConfig:
    return FaultConfig(id="t", rules=[rule])


# ------------------------------------------------------------- the injector

def test_decisions_are_deterministic_per_seed():
    c = cfg(tools=["*"], kind="error", rate=0.5)
    a = [FaultInjector(c, 7) for _ in range(2)]
    seq = [[inj.decide("fs.read_file") for _ in range(30)] for inj in a]
    assert seq[0] == seq[1]                                          # same seed: same faults
    other = [FaultInjector(c, 8).decide("fs.read_file") for _ in range(30)]
    assert other != seq[0]                                           # different seed: different faults
    assert 5 < sum(x is not None for x in seq[0]) < 25               # roughly the configured rate


def test_nth_and_max_per_run():
    inj = FaultInjector(cfg(tools=["fs.*"], kind="timeout", nth=[2, 3]), 0)
    assert [inj.decide("fs.read_file") for _ in range(4)] == [None, "timeout", "timeout", None]
    assert inj.decide("sql.query") is None                           # pattern does not match
    capped = FaultInjector(cfg(tools=["*"], kind="error", max_per_run=1), 0)
    assert [capped.decide("x.y") for _ in range(3)] == ["error", None, None]


def test_none_is_reserved_and_rates_are_bounded():
    with pytest.raises(ValidationError):
        FaultConfig(id="none", rules=[{"kind": "error"}])
    with pytest.raises(ValidationError):
        cfg(kind="error", rate=1.5)


# ------------------------------------------------------------- what each fault does to a run

def _with_profile(factory, **rule):
    factory.faults = {**factory.faults, "test": FaultConfig(id="test", rules=[rule])}
    return "test"


@pytest.mark.parametrize("kind", ["error", "timeout", "rate_limit", "empty"])
async def test_failing_faults_do_not_run_the_tool(factory, kind):
    fid = _with_profile(factory, tools=["fs.write_file"], kind=kind, nth=[1])
    rec = await run_one(factory, factory.tasks.get("fs-01"), factory.recipes["v2-base"], "scripted/replay",
                        "scripted", fault=fid)
    assert rec.status.value == "FAIL"                                # the one write never happened
    tool_msgs = [m for m in rec.messages if m["role"] == "tool"]
    assert "injected" not in tool_msgs[0]["content"].lower()         # the model sees a realistic failure


async def test_garbled_fault_runs_the_tool_but_cuts_the_answer(factory):
    fid = _with_profile(factory, tools=["sql.query"], kind="garbled", nth=[1])
    rec = await run_one(factory, factory.tasks.get("mix-01"), factory.recipes["v2-base"], "scripted/replay",
                        "scripted", fault=fid)
    query_reply = next(m for m in rec.messages if m["role"] == "tool")["content"]
    assert query_reply.startswith('{\n  "columns"') and "Creek Analytics" not in query_reply   # cut off
    assert rec.status.value == "SUCCESS"                             # replay writes the CSV from its plan anyway


async def test_retrying_model_recovers_and_injected_errors_are_not_its_fault(factory):
    fid = _with_profile(factory, tools=["*"], kind="error", nth=[1])
    brittle = await run_one(factory, factory.tasks.get("fs-02"), factory.recipes["v2-base"], "scripted/replay",
                            "scripted", fault=fid)
    robust = await run_one(factory, factory.tasks.get("fs-02"), factory.recipes["v2-base"], "scripted/retry",
                           "scripted", fault=fid)
    assert brittle.status.value == "FAIL" and robust.status.value == "SUCCESS"
    assert robust.scores.trajectory == 1.0                           # failed attempts were the environment's
    assert robust.cost.tool_calls == 4                                # read fails, read, write fails, write


# ------------------------------------------------------------- identity

async def test_faulted_runs_are_their_own_experiments(factory):
    task, recipe = factory.tasks.get("fs-01"), factory.recipes["v2-base"]
    clean = run_key_for(task, recipe, "scripted/replay", "scripted", 0, declared=factory.declared,
                        providers=factory.providers)
    faulted = run_key_for(task, recipe, "scripted/replay", "scripted", 0, declared=factory.declared,
                          providers=factory.providers, fault=factory.faults["first-call-fails"])
    assert clean.fault_config_hash == "none" and faulted.fault_config_hash != "none"
    assert clean.recipe_hash == faulted.recipe_hash and clean.as_str() != faulted.as_str()
    rec = await run_one(factory, task, recipe, "scripted/replay", "scripted", fault="first-call-fails")
    assert rec.run_key == faulted and rec.fault == "first-call-fails"


def test_unknown_fault_profile_fails_before_anything_runs(factory):
    with pytest.raises(KeyError, match="no-such"):
        plan(factory, "v2-base", "scripted", ["scripted/replay"], faults=[None, "no-such"])


async def test_resume_tells_clean_and_faulted_runs_apart(factory):
    specs = plan(factory, "v2-base", "scripted", ["scripted/replay"], task_ids=["fs-01"],
                 faults=[None, "first-call-fails"])
    await run_campaign(factory, specs[:1])                           # clean run only
    status = plan_status(factory, specs)
    assert [s.fault for s in status.done] == [None] and [s.fault for s in status.new] == ["first-call-fails"]


# ------------------------------------------------------------- R at aggregation

async def test_r_separates_brittle_from_robust_and_stays_out_of_e_and_cost(factory):
    specs = plan(factory, "v2-base", "scripted", ["scripted/replay", "scripted/retry"],
                 task_ids=["fs-02", "sql-02"], faults=[None, "first-call-fails"])
    result = await run_campaign(factory, specs, concurrency=4)
    rows = [record_row(r) for r in result.records]
    baseline = CostBaseline.from_rows(rows)
    assert sum(baseline.counts.values()) == 4                        # medians from the 4 clean runs only
    cells = {c.canonical_model: c for c in aggregate_rows(rows, baseline) if c.task_class.startswith("KNOWN_PATH|REVERSIBLE|R1")}
    assert cells["scripted/retry"].robustness == 1.0
    assert cells["scripted/replay"].robustness == 0.0
    assert all(c.e.n == 1 and c.n_faulted == 1 for c in cells.values())   # faulted runs only feed R
    assert cells["scripted/retry"].e.mean > cells["scripted/replay"].e.mean


def test_r_is_undefined_without_a_baseline_to_degrade_from():
    clean = [{"outcome": 0.0}]
    assert robustness(clean, [{"outcome": 1.0}]) is None
    assert robustness([{"outcome": 1.0}], []) is None
    assert robustness([{"outcome": 1.0}], [{"outcome": 0.5}, {"outcome": 1.0}]) == 0.75
    assert robustness([{"outcome": 0.5}], [{"outcome": 1.0}]) == 1.0     # capped


async def test_v1_cannot_retry_so_it_has_no_robustness(factory):
    fid = _with_profile(factory, tools=["*"], kind="error", nth=[1])
    rec = await run_one(factory, factory.tasks.get("fs-01"), factory.recipes["v1-base"], "scripted/retry",
                        "scripted", fault=fid)
    assert rec.status.value == "FAIL"


async def test_hand_written_model_sees_the_realistic_message(factory):
    fid = _with_profile(factory, tools=["fs.write_file"], kind="rate_limit", nth=[1])
    backend = ScriptedBackend([{"tool_calls": [{"name": "fs__write_file",
                                                "arguments": {"path": "notes/todo.txt", "content": "buy milk"}}]},
                               {"content": "done"}])
    rec = await run_one(factory, factory.tasks.get("fs-01"), factory.recipes["v2-base"], "scripted/replay",
                        "scripted", backend=backend, fault=fid)
    assert next(m for m in rec.messages if m["role"] == "tool")["content"].startswith("Error 429")
