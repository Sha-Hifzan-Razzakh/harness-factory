"""Cross-provider equivalence calibration (P2): pair runs that differ only in the provider, judge E0's paired
difference, and pool cells of providers calibrated as equivalent.

Offline: a second scripted provider (`scripted-b`) serves the same canonical models. Run with the reference plan it
behaves exactly like `scripted`; run with the lazy script it is measurably worse."""

import dataclasses

import pytest

from harness_factory.providers.catalog import ProviderCatalog, ProviderEntry, ServedModel
from harness_factory.providers.quota import QuotaLedger
from harness_factory.results.calibration import Equivalence, calibrate, pairs, pools
from harness_factory.runner.runner import plan, run_campaign, run_one
from harness_factory.tasks.reference import PlanBackend, load_reference

MODEL = "scripted/replay"


def with_providers(factory, tmp_path, *extra: ProviderEntry):
    providers = factory.providers.model_copy(update={"providers": [*factory.providers.providers, *extra]})
    return dataclasses.replace(factory, providers=providers,
                               ledger=QuotaLedger(providers, state_path=tmp_path / "quota2.json"))


def scripted(pid: str, served: str = "replay") -> ProviderEntry:
    return ProviderEntry(id=pid, kind="scripted", models=[ServedModel(canonical=MODEL, provider_model_id=served)])


async def run_both(f, p0_tasks, lazy_b: bool = False):
    await run_campaign(f, plan(f, "v2-base", "scripted", [MODEL], task_ids=p0_tasks))
    if not lazy_b:
        await run_campaign(f, plan(f, "v2-base", "scripted-b", [MODEL], task_ids=p0_tasks))
        return
    for t in p0_tasks:                                       # provider b serves a model that stops one step early
        task = f.tasks.get(t)
        await run_one(f, task, f.recipes["v2-base"], MODEL, "scripted-b",
                      backend=PlanBackend(load_reference(task), "lazy"))


# ------------------------------------------------------------------ what a run records

async def test_a_run_records_its_tool_environment_apart_from_the_served_model(factory):
    rec = await run_one(factory, factory.tasks.get("fs-01"), factory.recipes["v2-base"], MODEL, "scripted")
    (row,) = factory.store.rows()
    assert row["served_id"] == "replay"
    assert row["tools_hash"] == factory.declared.env_hash(["fs", "sql"], "*") != rec.run_key.env_hash


# ------------------------------------------------------------------ verdicts

async def test_providers_that_behave_alike_are_equivalent_and_their_cells_pool(factory, tmp_path, p0_tasks):
    f = with_providers(factory, tmp_path, scripted("scripted-b"))
    await run_both(f, p0_tasks)
    eq = calibrate(f.store.rows(), f.providers, MODEL, ["scripted-b", "scripted"], min_pairs=len(p0_tasks))
    assert eq.verdict == "equivalent" and eq.providers == ("scripted", "scripted-b")
    assert (eq.n, eq.ties, eq.mean) == (len(p0_tasks), len(p0_tasks), 0.0)
    f.store.save_equivalence(eq)
    assert f.store.save_equivalence(eq) == eq.id and len(f.store.equivalences()) == 1     # idempotent
    pooled = pools(f.store.equivalences(), f.providers)
    assert set(pooled.values()) == {"scripted+scripted-b"}
    _, apart = f.store.aggregate()
    _, together = f.store.aggregate(pools=pooled)
    assert {c.provider for c in apart} == {"scripted", "scripted-b"}
    assert {c.provider for c in together} == {"scripted+scripted-b"}
    assert sum(c.e.n for c in together) == sum(c.e.n for c in apart) == 2 * len(p0_tasks)
    assert all(c.env_hash.startswith("tools:") for c in together)


async def test_a_worse_provider_is_different_and_never_pooled(factory, tmp_path, p0_tasks):
    f = with_providers(factory, tmp_path, scripted("scripted-b"))
    await run_both(f, p0_tasks, lazy_b=True)
    eq = calibrate(f.store.rows(), f.providers, MODEL, ["scripted", "scripted-b"], min_pairs=len(p0_tasks))
    assert eq.verdict == "different" and eq.mean < 0 and eq.losses > 0, eq.reason
    f.store.save_equivalence(eq)
    assert pools(f.store.equivalences(), f.providers) == {}


async def test_too_few_pairs_is_inconclusive(factory, tmp_path, p0_tasks):
    f = with_providers(factory, tmp_path, scripted("scripted-b"))
    await run_both(f, p0_tasks)
    eq = calibrate(f.store.rows(), f.providers, MODEL, ["scripted", "scripted-b"])           # default: 20 pairs
    assert eq.verdict == "inconclusive" and "at least 20" in eq.reason
    with pytest.raises(ValueError, match="exactly two"):
        calibrate(f.store.rows(), f.providers, MODEL, ["scripted", "scripted"])


async def test_a_provider_that_changes_its_served_model_loses_the_verdict(factory, tmp_path, p0_tasks):
    f = with_providers(factory, tmp_path, scripted("scripted-b"))
    await run_both(f, p0_tasks)
    f.store.save_equivalence(calibrate(f.store.rows(), f.providers, MODEL, ["scripted", "scripted-b"],
                                       min_pairs=len(p0_tasks)))
    moved = f.providers.model_copy(update={"providers": [*factory.providers.providers, scripted("scripted-b", "v2")]})
    assert pools(f.store.equivalences(), moved) == {}                       # calibrated against "replay"
    again = calibrate(f.store.rows(), moved, MODEL, ["scripted", "scripted-b"], min_pairs=1)
    assert again.n == 0                                                     # old runs no longer pair


# ------------------------------------------------------------------ pairing and pools, on synthetic rows

def row(provider, served="m1", seed=0, composite=1.0, status="SUCCESS", fault="none", task="t-1@1", tools="tt"):
    return {"run_key": f"{task}|h1|r1|{MODEL}|{provider}|{seed}|{fault}|env-{provider}", "task_version": task,
            "recipe_hash": "r1", "canonical_model": MODEL, "provider": provider, "seed": seed, "status": status,
            "composite": composite, "tokens_in": 100, "tokens_out": 10, "tools_hash": tools, "served_id": served}


def test_only_clean_scored_runs_of_the_same_experiment_pair():
    rows = [row("a"), row("b"),                                          # a pair
            row("a", seed=1), row("b", seed=1, fault="f0"),              # faulted: never paired
            row("a", seed=2), row("b", seed=2, status="INCOMPLETE"),     # unscored: never paired
            row("a", seed=3), row("b", seed=3, tools="other"),           # different tool environment
            row("a", seed=4), row("b", seed=4, served="m0")]             # served by another model id
    assert len(pairs(rows, MODEL, ("a", "b"), {"a": "m1", "b": "m1"})) == 1


def test_pools_need_an_equivalent_record_for_every_pair_inside():
    cat = ProviderCatalog(providers=[ProviderEntry(id=p, models=[ServedModel(canonical=MODEL, provider_model_id="m1")])
                                     for p in ("a", "b", "c")])

    def eq(x, y, verdict="equivalent"):
        return Equivalence(canonical_model=MODEL, providers=(x, y), served={x: "m1", y: "m1"}, recipe_hashes=["r1"],
                           margin=0.1, min_pairs=1, n=1, verdict=verdict, reason="test").model_dump(mode="json")

    assert pools([eq("a", "b"), eq("b", "c")], cat) == {}                   # a~c never measured: no guess
    assert set(pools([eq("a", "b"), eq("b", "c"), eq("a", "c")], cat).values()) == {"a+b+c"}
    assert pools([eq("a", "b"), eq("a", "b", "different")], cat) == {}      # the newest record is in force
