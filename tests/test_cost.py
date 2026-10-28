"""Efficiency C, cost baselines, and the cost-adjusted E."""

import pytest

from harness_factory.results.cost import CostBaseline, efficiency, weight_profile_of
from harness_factory.results.store import ResultsStore, aggregate_rows, record_row
from harness_factory.runner.runner import smoke


def row(task="t@1", status="SUCCESS", tokens=100, usd=0.0, composite=1.0, cls="KNOWN_PATH|REVERSIBLE|R1",
        recipe="r", model="m", provider="p"):
    return {"task_version": task, "status": status, "tokens_in": tokens, "tokens_out": 0, "usd": usd,
            "composite": composite, "task_class": cls, "recipe_id": recipe, "recipe_hash": recipe,
            "canonical_model": model, "provider": provider}


def test_efficiency_curve():
    assert efficiency(0, 100) == 0.0             # free
    assert efficiency(50, 100) == 0.25           # cheaper than the median still ranks better
    assert efficiency(100, 100) == 0.5           # at the median
    assert efficiency(150, 100) == pytest.approx(0.75)
    assert efficiency(200, 100) == 1.0
    assert efficiency(900, 100) == 1.0           # capped at twice the median
    assert efficiency(10, 0) == 0.0              # degenerate median


def test_median_uses_successful_runs_only():
    b = CostBaseline.from_rows([row(tokens=100), row(tokens=300), row(tokens=10, status="FAIL")])
    assert b.medians == {"p|t@1": 200.0}         # the cheap failure does not drag the median down
    assert b.counts == {"p|t@1": 2}


def test_unsolved_task_falls_back_to_all_runs():
    b = CostBaseline.from_rows([row(task="u@1", status="FAIL", tokens=40), row(task="u@1", status="PARTIAL", tokens=60)])
    assert b.medians == {"p|u@1": 50.0}


def test_basis_is_usd_only_when_every_success_is_priced():
    assert CostBaseline.from_rows([row(usd=0.01), row(usd=0.02)]).basis == "usd"
    assert CostBaseline.from_rows([row(usd=0.01), row(usd=0.0)]).basis == "tokens"


def test_providers_never_share_a_median():
    """Scripted replays (tokens, cheap) must not become the yardstick for real runs (USD)."""
    b = CostBaseline.from_rows([row(provider="scripted", tokens=100), row(provider="groq", tokens=5000, usd=0.002),
                                row(provider="groq", tokens=7000, usd=0.004)])
    assert b.bases == {"groq": "usd", "scripted": "tokens"} and b.basis == "mixed"
    assert b.medians == {"groq|t@1": 0.003, "scripted|t@1": 100.0}
    assert b.c(row(provider="groq", tokens=6000, usd=0.003)) == 0.5          # at its own provider's median


def test_baseline_identity_is_content_addressed():
    a = CostBaseline.from_rows([row(tokens=100)], note="a")
    b = CostBaseline.from_rows([row(tokens=100)], note="b")
    assert a.id == b.id                           # notes and timestamps do not change identity
    assert a.id != CostBaseline.from_rows([row(tokens=101)]).id


def test_adjusted_e_uses_class_weight_and_never_goes_negative():
    b = CostBaseline.from_rows([row(tokens=100)])
    assert b.adjusted(row(tokens=100)) == pytest.approx(1.0 - 0.15 * 0.5)           # at the median
    assert b.adjusted(row(tokens=50)) > b.adjusted(row(tokens=100))                  # cheaper ranks higher
    assert b.adjusted(row(tokens=200)) == pytest.approx(1.0 - 0.15)                 # reversible: w_c = 0.15
    assert b.adjusted(row(tokens=200, cls="SINGLE_STEP|READ|R0")) == pytest.approx(0.75)   # read: w_c = 0.25
    assert b.adjusted(row(tokens=999, composite=0.0, status="FAIL")) == 0.0          # failures stay at 0
    assert b.adjusted(row(task="new@1", tokens=999)) == 1.0                          # outside baseline: C = 0
    assert weight_profile_of("KNOWN_PATH|IRREVERSIBLE|R3") == "irreversible"


async def test_cost_separates_shapes_that_tie_on_outcome(factory):
    recs = await smoke(factory, ["v2-base", "v3-staged"], "scripted", ["scripted/replay"])
    rows = [record_row(r) for r in recs]
    assert all(r["composite"] == 1.0 for r in rows)                                   # E0 ties
    cells = aggregate_rows(rows, CostBaseline.from_rows(rows))
    mean_e = {}
    for c in cells:
        mean_e.setdefault(c.recipe_id, []).append(c.e.mean)
    v2, v3 = (sum(mean_e[k]) / len(mean_e[k]) for k in ("v2-base", "v3-staged"))
    assert v2 > v3                                                                    # V3 pays for its extra calls
    assert all(c.mean_c > 0 for c in cells if c.recipe_id == "v3-staged")


async def test_frozen_baseline_keeps_scores_stable_as_runs_arrive(factory, tmp_path):
    store = ResultsStore(f"sqlite:///{tmp_path / 'r.db'}")
    factory.store = store
    await smoke(factory, "v2-base", "scripted", ["scripted/replay"])
    frozen_id = store.save_baseline(store.live_baseline())
    assert store.save_baseline(store.live_baseline()) == frozen_id                    # idempotent
    frozen = store.get_baseline(frozen_id)
    before = {c.task_class + c.recipe_id: c.e.mean for c in store.aggregate(frozen)[1]}

    await smoke(factory, "v3-staged", "scripted", ["scripted/replay"])                # pricier runs arrive
    after_frozen = {c.task_class + c.recipe_id: c.e.mean for c in store.aggregate(frozen)[1]}
    live_baseline, _ = store.aggregate()
    assert {k: after_frozen[k] for k in before} == before                             # old cells unchanged
    assert live_baseline.id != frozen_id                                               # the live medians moved
    assert [b.id for b in store.list_baselines()] == [frozen_id]
