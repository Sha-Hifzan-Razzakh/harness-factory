"""Selector on calibrated pooled cells (ADR 0010): opt-in, current cells only, held-out runs never inform a pick.

Offline: a second scripted provider (`scripted-b`) serves scripted/replay exactly like `scripted`, so the two come
out `equivalent` and a decision for either one can use both providers' runs."""

import asyncio
import dataclasses
import json
from datetime import UTC, datetime

import pytest
from typer.testing import CliRunner

from harness_factory import settings
from harness_factory.hashing import content_hash
from harness_factory.providers.catalog import ProviderEntry, ServedModel
from harness_factory.providers.quota import QuotaLedger
from harness_factory.provision.provisioner import reaches,task_class
from harness_factory.results.calibration import calibrate
from harness_factory.results.cost import CostBaseline
from harness_factory.runner.runner import plan, run_campaign
from harness_factory.select.policy import (
    build_policy,
    evidence_rows,
    load_policy,
    plan_evaluation,
    save_policy,
)
from harness_factory.select.selector import Decision, decide, load_selector_config
from harness_factory.tasks.model import Split

MODEL = "scripted/replay"
BOTH = ("scripted", "scripted-b")


def with_b(factory, tmp_path, served: str = "replay"):
    b = ProviderEntry(id="scripted-b", kind="scripted", models=[ServedModel(canonical=MODEL, provider_model_id=served)])
    providers = factory.providers.model_copy(update={"providers": [*factory.providers.providers, b]})
    return dataclasses.replace(factory, providers=providers,
                               ledger=QuotaLedger(providers, state_path=tmp_path / f"quota-{served}.json"))


async def on_both(f, task_ids, seeds=1):
    for p in BOTH:
        await run_campaign(f, plan(f, "v2-base", p, [MODEL], seeds, task_ids=task_ids), concurrency=4)
    rows = f.store.rows()
    return rows, CostBaseline.from_rows(rows)


def equivalent(f, rows, min_pairs, **update):
    """Calibrate (the two providers behave alike), optionally overwrite fields (e.g. the verdict), and save."""
    eq = calibrate(rows, f.providers, MODEL, list(BOTH), min_pairs=min_pairs)
    assert eq.verdict == "equivalent", eq.reason
    eq = eq.model_copy(update=update)
    f.store.save_equivalence(eq)
    return eq


def cls(f, task_id):
    return task_class(f.tasks.get(task_id), f.declared)


@pytest.fixture
def own_var(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "VAR", tmp_path / "var")
    return tmp_path / "var"


def no_hash_fields(d: Decision) -> dict:
    return d.model_dump(mode="json", exclude={"made_at", "config_hash"})


def test_an_equivalent_verdict_doubles_the_evidence_and_the_pick_names_its_pool(factory, tmp_path, own_var,
                                                                                monkeypatch):
    # Not async: `hfx provision` below runs its own event loop.
    f = with_b(factory, tmp_path)
    rows, b = asyncio.run(on_both(f, ["fs-01"], seeds=2))   # 2 runs per provider; min_runs is 3
    off = load_selector_config()
    on = off.model_copy(update={"pool_providers": True})
    c = cls(f, "fs-01")
    before = decide(c, MODEL, "scripted", factory=f, rows=rows, baseline=b, config=off)
    assert before.rule == "cold_start" and before.evidence_runs == 2 and before.pool is None
    policy_before = build_policy(f, rows, b, MODEL, "scripted", config=off)

    eq = equivalent(f, rows, min_pairs=2)
    # Off (the default): a saved verdict changes nothing, down to the decision and policy ids.
    assert decide(c, MODEL, "scripted", factory=f, rows=rows, baseline=b, config=off).id == before.id
    assert build_policy(f, rows, b, MODEL, "scripted", config=off).id == policy_before.id

    pooled = decide(c, MODEL, "scripted", factory=f, rows=rows, baseline=b, config=on)
    assert pooled.evidence_runs == 2 * before.evidence_runs == 4
    assert pooled.rule == "ranked" and pooled.chosen == ["v2-base"] and pooled.ranked[0].n == 4
    assert pooled.pool.label == "scripted+scripted-b" and pooled.pool.equivalences == [eq.id]
    assert pooled.pool.served == {"scripted": "replay", "scripted-b": "replay"} and not pooled.pool.rejudged
    assert pooled.pool.runs == {"scripted": 2, "scripted-b": 2}
    assert pooled.pool.by_recipe == {"v2-base": {"scripted": 2, "scripted-b": 2}}
    assert "(pooled scripted+scripted-b: scripted 2, scripted-b 2)" in pooled.summary()
    from_b = decide(c, MODEL, "scripted-b", factory=f, rows=rows, baseline=b, config=on)
    assert from_b.evidence_runs == 4 and from_b.pool.label == pooled.pool.label     # the same cell, either side

    # A pooled policy is a different policy (its config hash says pooling was on) and keeps the pool when frozen.
    pooled_policy = build_policy(f, rows, b, MODEL, "scripted", config=on)
    assert pooled_policy.config_hash != policy_before.config_hash and pooled_policy.id != policy_before.id
    save_policy(pooled_policy)
    assert load_policy(pooled_policy.id).decisions[c].pool.runs == {"scripted": 2, "scripted-b": 2}
    assert '"pool"' not in save_policy(policy_before).read_text()

    # Provisioning logs the pool and the runs from each provider; without --pool the log is as before.
    from harness_factory.cli import main as cli
    monkeypatch.setattr(cli, "_factory", lambda store=True: f)
    args = ["provision", "--task", "fs-01", "--model", MODEL, "--provider", "scripted"]
    out = CliRunner().invoke(cli.app, [*args, "--pool"])
    assert out.exit_code == 0, out.output
    assert "pooled scripted+scripted-b: scripted 2, scripted-b 2" in " ".join(out.output.split())
    (logged,) = [json.loads(p.read_text()) for p in (own_var / "decisions").glob("*.json")]
    assert logged["decision"]["rule"] == "ranked" and logged["decision"]["evidence_runs"] == 4
    assert logged["decision"]["pool"]["label"] == "scripted+scripted-b"
    assert logged["decision"]["pool"]["runs"] == {"scripted": 2, "scripted-b": 2}
    assert logged["decision"]["pool"]["equivalences"] == [eq.id]
    out = CliRunner().invoke(cli.app, args)
    assert out.exit_code == 0, out.output
    plain = [json.loads(p.read_text())["decision"] for p in (own_var / "decisions").glob("*.json")]
    assert [d["rule"] for d in plain if "pool" not in d] == ["cold_start"]
    out = CliRunner().invoke(cli.app, ["select", "show", "--model", MODEL, "--provider", "scripted", "--pool"])
    assert out.exit_code == 0, out.output
    shown = " ".join(out.output.split())
    assert f"pooled evidence: scripted+scripted-b (records {eq.id}) {c}: scripted 2, scripted-b 2" in shown
    out = CliRunner().invoke(cli.app, ["select", "show", "--model", MODEL, "--provider", "scripted"])
    assert out.exit_code == 0 and "pooled" not in out.output
    out = CliRunner().invoke(cli.app, [*args, "--pool", "--policy", pooled_policy.id])
    assert out.exit_code == 2                     # --pool decides live; a frozen policy already did


async def test_nothing_is_pooled_without_a_current_equivalent_verdict_or_with_stale_runs(factory, tmp_path):
    f = with_b(factory, tmp_path)
    rows, b = await on_both(f, ["fs-01"], seeds=2)
    on = load_selector_config().model_copy(update={"pool_providers": True})
    c = cls(f, "fs-01")

    def ev(fac=f, rs=rows):
        d = decide(c, MODEL, "scripted", factory=fac, rows=rs, baseline=b, config=on)
        return d.evidence_runs, d.pool

    assert ev() == (2, None)                                                     # no verdict saved
    equivalent(f, rows, 2, verdict="different", reason="test", created_at=datetime.now(UTC))
    assert ev() == (2, None)                                                     # `different` is in force
    equivalent(f, rows, 2, created_at=datetime.now(UTC))
    assert ev()[0] == 4                                                          # a newer `equivalent` pools
    equivalent(f, rows, 2, verdict="inconclusive", reason="test", created_at=datetime.now(UTC))
    assert ev() == (2, None)                                                     # ... until a newer record
    equivalent(f, rows, 2, margin=0.2, created_at=datetime.now(UTC))             # (another id: not a no-op save)
    assert ev()[0] == 4

    # The catalog now names another served id for scripted-b: the verdict (calibrated on "replay") lapses.
    moved = with_b(factory, tmp_path, served="v2")
    assert ev(fac=moved) == (2, None)

    # Runs that are not current never enter a pooled cell: a stale served id, tools environment or recipe hash.
    (b_row, *_) = [r for r in rows if r["provider"] == "scripted-b"]
    stale = [{**b_row, "run_key": f"{b_row['run_key']}|{i}", **change} for i, change in
             enumerate([{"served_id": "old"}, {"tools_hash": "other"}, {"recipe_hash": "old"}, {"tools_hash": None}])]
    assert ev(rs=rows + stale)[0] == 4
    assert ev(rs=rows + [{**b_row, "run_key": f"{b_row['run_key']}|fresh"}])[0] == 5     # a current one does


async def test_held_out_runs_never_inform_a_pooled_held_out_pick(factory, tmp_path, p0_tasks):
    f = with_b(factory, tmp_path)
    held = [t.id for t in f.tasks.by_split(Split.HELD_OUT)]
    rows, _ = await on_both(f, p0_tasks + held)
    on = load_selector_config().model_copy(update={"min_runs": 1, "pool_providers": True})
    evidence = evidence_rows(rows, set(held))
    in_evidence = {cls(f, t): sum(1 for r in evidence if r["task_class"] == cls(f, t)) for t in held}
    assert any(in_evidence.values())

    # A verdict that needs the held-out pairs: judged again without them it is short of min_pairs, so no pool.
    # Only held-out tasks v2-base can reach are run (plan leaves out the others), so only those make pairs.
    ran = [t for t in held if reaches(f.recipes["v2-base"], f.tasks.get(t), f.declared)]
    equivalent(f, rows, min_pairs=len(p0_tasks) + len(ran))
    alone = plan_evaluation(f, rows, MODEL, "scripted", config=on)
    assert all(d.pool is None for d in alone.decisions.values())
    one_side = {t: d.evidence_runs for t, d in alone.decisions.items()}

    # A verdict the visible pairs alone support: pooled, and still counted from evidence only.
    eq = equivalent(f, rows, min_pairs=len(p0_tasks), created_at=datetime.now(UTC))
    ep = plan_evaluation(f, rows, MODEL, "scripted", config=on)
    assert set(ep.picks["selector"]) == set(held)
    for task_id, d in ep.decisions.items():
        assert d.pool is not None and d.pool.rejudged and d.pool.equivalences == [eq.id]
        assert d.evidence_runs == 2 * one_side[task_id] <= in_evidence[cls(f, task_id)]
        assert sum(d.pool.runs.values()) == d.evidence_runs
    assert any(d.evidence_runs for d in ep.decisions.values())


def test_pooling_off_serializes_and_hashes_as_before(factory):
    config = load_selector_config()
    assert config.pool_providers is False
    old_fields = {"min_runs", "candidates", "hand_picked", "defaults", "depth_shapes"}
    dump = config.model_dump(mode="json", by_alias=True)
    assert config.config_hash() == content_hash({k: v for k, v in dump.items() if k in old_fields}, 12)
    assert config.model_copy(update={"pool_providers": True}).config_hash() != config.config_hash()

    d = decide("KNOWN_PATH|REVERSIBLE|R1", MODEL, "scripted", factory=factory, rows=[],
               baseline=CostBaseline.from_rows([]), config=config)
    raw = json.loads(d.model_dump_json())
    assert "pool" not in raw and "pool" not in d.model_dump()
    assert d.id == content_hash({k: v for k, v in d.model_dump(mode="json").items() if k != "made_at"}, 12)
    assert Decision.model_validate(raw).id == d.id                             # a decision saved before pooling
    assert no_hash_fields(decide("KNOWN_PATH|REVERSIBLE|R1", MODEL, "scripted", factory=factory, rows=[],
                                 baseline=CostBaseline.from_rows([]),
                                 config=config.model_copy(update={"pool_providers": True}))) == no_hash_fields(d)
