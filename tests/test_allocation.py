"""Allocation planner (P2, MOD-04, MOD-06; ADR 0011): which provider serves each cell of a campaign.

Offline: a test-local catalog of two scripted providers, `alpha` and `beta`, serving the same canonical model with
different daily quotas and prices. With no history, every V2 run is estimated at the shape default (6 calls,
6,000 tokens), so a daily request limit of 60 holds 10 runs."""

import dataclasses
import json

import pytest
from typer.testing import CliRunner

from harness_factory import settings
from harness_factory.providers.catalog import ProviderCatalog, ProviderEntry, RateLimits, ServedModel
from harness_factory.providers.quota import QuotaLedger
from harness_factory.results.calibration import Equivalence, pools
from harness_factory.runner.allocation import (
    _interleave,
    _shares,
    allocate,
    load_allocation,
    save_allocation,
)
from harness_factory.runner.runner import run_campaign

MODEL, MODEL2 = "scripted/replay", "scripted/noisy"


def provider(pid: str, rpd: int | None = None, price: float = 0.0, served: str = "replay",
             scope: str = "account", models=(MODEL,)) -> ProviderEntry:
    ids = {MODEL: served, MODEL2: "noisy"}
    return ProviderEntry(id=pid, kind="scripted", limits=RateLimits(rpd=rpd, scope=scope),
                         models=[ServedModel(canonical=m, provider_model_id=ids[m], price_in=price, price_out=price)
                                 for m in models])


def with_catalog(factory, tmp_path, *entries: ProviderEntry):
    providers = ProviderCatalog(providers=list(entries))
    return dataclasses.replace(factory, providers=providers,
                               ledger=QuotaLedger(providers, state_path=tmp_path / "quota-alloc.json"))


def verdict(a: str, b: str, kind: str = "equivalent", served: str = "replay") -> dict:
    return Equivalence(canonical_model=MODEL, providers=tuple(sorted((a, b))), served={a: served, b: served},
                       recipe_hashes=["r1"], margin=0.1, min_pairs=20, n=20, verdict=kind,
                       reason="test").model_dump(mode="json")


@pytest.fixture
def own_var(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "VAR", tmp_path / "var")
    return tmp_path / "var"


def by_provider(alloc) -> dict[str, int]:
    out: dict[str, int] = {}
    for s in alloc.specs:
        out[s.provider] = out.get(s.provider, 0) + 1
    return out


# ------------------------------------------------------------------ without a verdict: one provider per model

def test_without_a_verdict_every_cell_of_a_model_goes_to_the_provider_with_capacity(factory, tmp_path, p0_tasks):
    f = with_catalog(factory, tmp_path, provider("alpha", rpd=60), provider("beta", rpd=600))
    alloc = allocate(f, "v2-base", [MODEL], seeds=3, task_ids=p0_tasks, equivalences=[])
    assert alloc.ok and by_provider(alloc) == {"beta": 15}             # alpha holds 10 of 15 runs today
    (d,) = alloc.decisions
    assert d.chosen == "beta" and set(d.cells.values()) == {"beta"} and "no equivalent calibration" in d.rule
    assert {g.label: g.days for g in d.candidates} == {"alpha": 2, "beta": 1}
    assert d.calibration == ["alpha~beta: not calibrated"]


def test_with_equal_capacity_the_cheaper_provider_serves(factory, tmp_path, p0_tasks):
    f = with_catalog(factory, tmp_path, provider("alpha", price=2.0), provider("beta", price=1.0))
    alloc = allocate(f, "v2-base", [MODEL], task_ids=p0_tasks, equivalences=[])
    assert by_provider(alloc) == {"beta": 5} and alloc.usd == alloc.decisions[0].usd > 0
    cost = allocate(f, "v2-base", [MODEL], task_ids=p0_tasks, equivalences=[], strategy="cost")
    assert by_provider(cost) == {"beta": 5}


def test_a_different_or_stale_verdict_never_splits(factory, tmp_path, p0_tasks):
    f = with_catalog(factory, tmp_path, provider("alpha", rpd=60), provider("beta", rpd=120))
    different = allocate(f, "v2-base", [MODEL], seeds=3, task_ids=p0_tasks,
                         equivalences=[verdict("alpha", "beta", "different")])
    assert by_provider(different) == {"beta": 15}
    assert different.decisions[0].calibration[0].startswith("alpha~beta: different")
    stale = allocate(f, "v2-base", [MODEL], seeds=3, task_ids=p0_tasks,
                     equivalences=[verdict("alpha", "beta", served="old-id")])     # calibrated on another model id
    assert by_provider(stale) == {"beta": 15} and "not in force" in stale.decisions[0].calibration[0]


# ------------------------------------------------------------------ with a verdict: split by remaining capacity

def test_an_equivalent_verdict_splits_runs_by_remaining_capacity(factory, tmp_path, p0_tasks):
    f = with_catalog(factory, tmp_path, provider("alpha", rpd=60), provider("beta", rpd=120))
    alloc = allocate(f, "v2-base", [MODEL], seeds=3, task_ids=p0_tasks, equivalences=[verdict("alpha", "beta")])
    assert alloc.ok and by_provider(alloc) == {"alpha": 6, "beta": 9}   # capacity 10 : min(20, 15)
    (d,) = alloc.decisions
    assert d.chosen == "alpha+beta" and set(d.cells.values()) == {"alpha+beta"} and "equivalent pool" in d.rule
    pool = next(g for g in d.candidates if g.label == "alpha+beta")
    assert pool.capacity == {"alpha": 10, "beta": 20} and pool.days == 1
    # capacity already spent today moves the split
    f.ledger.charge("alpha", requests=24)                                # 36 calls left: 6 runs
    assert by_provider(allocate(f, "v2-base", [MODEL], seeds=3, task_ids=p0_tasks,
                                equivalences=[verdict("alpha", "beta")])) == {"alpha": 4, "beta": 11}
    f.ledger.mark_exhausted("alpha")                                     # a spent member gets nothing
    assert by_provider(allocate(f, "v2-base", [MODEL], seeds=3, task_ids=p0_tasks,
                                equivalences=[verdict("alpha", "beta")])) == {"beta": 15}


def test_two_unlimited_equivalent_providers_split_evenly_and_interleave():
    assert _shares({"a": float("inf"), "b": float("inf")}, 7) == {"a": 4, "b": 3}
    assert _shares({"a": 0, "b": 0}, 4) == {"a": 2, "b": 2}
    assert _interleave({"a": 2, "b": 4}, 6) == ["b", "a", "b", "b", "a", "b"]


def test_earlier_models_use_up_a_shared_bucket(factory, tmp_path, p0_tasks):
    both = (MODEL, MODEL2)
    f = with_catalog(factory, tmp_path, provider("alpha", rpd=90, models=both), provider("beta", rpd=60, models=both))
    alloc = allocate(f, "v2-base", [MODEL, MODEL2], seeds=2, task_ids=p0_tasks, equivalences=[])
    chosen = {d.canonical_model: d.chosen for d in alloc.decisions}
    assert chosen == {MODEL: "alpha", MODEL2: "beta"}                  # alpha keeps 5 runs after the first model
    # per-model buckets (scope: model) are independent: alpha has room for both
    per_model = with_catalog(factory, tmp_path, provider("alpha", rpd=90, models=both, scope="model"),
                             provider("beta", rpd=60, models=both, scope="model"))
    again = allocate(per_model, "v2-base", [MODEL, MODEL2], seeds=2, task_ids=p0_tasks, equivalences=[])
    assert {d.canonical_model: d.chosen for d in again.decisions} == {MODEL: "alpha", MODEL2: "alpha"}


# ------------------------------------------------------------------ refusals, as the projection refuses

def test_a_plan_over_quota_is_refused_with_the_reason(factory, tmp_path, p0_tasks):
    f = with_catalog(factory, tmp_path, provider("alpha", rpd=6, price=1.0), provider("beta", rpd=6, price=1.0))
    late = allocate(f, "v2-base", [MODEL], task_ids=p0_tasks, equivalences=[], deadline_days=1)
    assert not late.ok and late.specs == [] and "--deadline-days 1" in late.refusals[0]
    assert late.decisions[0].chosen is None and late.decisions[0].refused
    with pytest.raises(ValueError, match="refused"):
        save_allocation(late)
    assert allocate(f, "v2-base", [MODEL], task_ids=p0_tasks, equivalences=[], deadline_days=5).ok
    pricey = allocate(f, "v2-base", [MODEL], task_ids=p0_tasks, equivalences=[], budget_usd=0.0001)
    assert not pricey.ok and "--budget-usd" in pricey.refusals[0]
    f.ledger.mark_exhausted("alpha")
    f.ledger.mark_exhausted("beta")
    spent = allocate(f, "v2-base", [MODEL], task_ids=p0_tasks, equivalences=[verdict("alpha", "beta")])
    assert not spent.ok and "already spent" in spent.refusals[0]
    nobody = allocate(f, "v2-base", [MODEL2], task_ids=p0_tasks, equivalences=[])
    assert nobody.refusals == [f"{MODEL2}: no active provider serves it"]


# ------------------------------------------------------------------ logged, saved, run

def _cli(f, monkeypatch, *args):
    from harness_factory.cli import main as cli
    monkeypatch.setattr(cli, "_factory", lambda store=True: f)
    monkeypatch.setattr(cli.console, "width", 250)
    return CliRunner().invoke(cli.app, ["allocate", *args])


def test_every_decision_is_logged_and_a_refusal_saves_nothing(factory, tmp_path, own_var, monkeypatch, p0_tasks):
    f = with_catalog(factory, tmp_path, provider("alpha", rpd=6), provider("beta", rpd=6))
    out = _cli(f, monkeypatch, "plan", "--models", MODEL, "--tasks", ",".join(p0_tasks), "--deadline-days", "1")
    assert out.exit_code == 2 and "--deadline-days 1" in " ".join(out.output.split())
    (logged,) = (own_var / "decisions").glob("*.json")
    entry = json.loads(logged.read_text())
    assert entry["decision"]["kind"] == "allocation" and entry["decision"]["refused"]
    assert entry["context"]["allocation"] is None and not (own_var / "allocations").exists()


async def test_an_equivalent_pool_runs_on_both_providers_and_pools_into_one_cell(factory, tmp_path, own_var,
                                                                                p0_tasks):
    f = with_catalog(factory, tmp_path, provider("alpha", rpd=60), provider("beta", rpd=60))
    f.store.save_equivalence(Equivalence.model_validate(verdict("alpha", "beta")))
    alloc = allocate(f, "v2-base", [MODEL], seeds=2, task_ids=p0_tasks)     # verdicts from the store
    assert by_provider(alloc) == {"alpha": 5, "beta": 5}
    path = save_allocation(alloc)
    assert load_allocation(alloc.id).specs == alloc.specs and path.name == f"{alloc.id}.json"
    assert allocate(f, "v2-base", [MODEL], seeds=2, task_ids=p0_tasks).id == alloc.id           # deterministic
    result = await run_campaign(f, alloc.specs, concurrency=4)
    assert not result.errors and {r.run_key.provider for r in result.records} == {"alpha", "beta"}
    _, cells = f.store.aggregate(pools=pools(f.store.equivalences(), f.providers))
    assert {c.provider for c in cells} == {"alpha+beta"}


def test_cli_plans_saves_shows_and_runs(factory, tmp_path, own_var, monkeypatch):
    f = with_catalog(factory, tmp_path, provider("alpha", price=2.0), provider("beta", price=1.0))
    out = _cli(f, monkeypatch, "plan", "--models", MODEL, "--tasks", "fs-01", "--run", "--strict")
    assert out.exit_code == 0, out.output
    (saved,) = (own_var / "allocations").glob("*.json")
    assert len(list((own_var / "decisions").glob("*.json"))) == 1
    assert [r["provider"] for r in f.store.rows()] == ["beta"]
    shown = _cli(f, monkeypatch, "show", saved.stem)
    assert shown.exit_code == 0 and "beta" in shown.output
    resumed = _cli(f, monkeypatch, "run", saved.stem, "--resume", "--strict")
    assert resumed.exit_code == 0 and "skipped 1 done" in resumed.output
    assert _cli(f, monkeypatch, "show", "nope").exit_code != 0
