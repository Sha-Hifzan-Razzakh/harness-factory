import pytest
from pydantic import ValidationError

from harness_factory.results.model import FamilyScores, ScoreWeights
from harness_factory.runner.spec import RunKey
from harness_factory.tasks.model import Oracle, WeightedCheck


def test_recipe_hash_excludes_model(factory):
    r = factory.recipes["v2-base"]
    assert r.with_model("a").content_hash() == r.with_model("b").content_hash() == r.content_hash()


def test_recipe_hash_changes_with_config(factory):
    r = factory.recipes["v2-base"]
    changed = r.model_copy(update={"config": r.config.model_copy(update={"max_steps": 3})})
    assert changed.content_hash() != r.content_hash()


def test_task_hash_is_stable(factory):
    t = factory.tasks.get("fs-01")
    assert t.content_hash() == t.content_hash()
    assert t.content_hash() != factory.tasks.get("fs-02").content_hash()


def test_oracle_weights_must_sum_to_one():
    with pytest.raises(ValidationError):
        Oracle(checks=[WeightedCheck(id="a", kind="output_regex", pattern="x", weight=0.5)])


def test_zero_weight_check_must_be_critical():
    with pytest.raises(ValidationError):
        WeightedCheck(id="g", kind="file_absent", path="x", weight=0)


def test_run_key_roundtrip():
    k = RunKey(task_version="fs-01@1", task_hash="t", recipe_hash="r", canonical_model="m", provider="p", seed=3)
    assert RunKey.from_str(k.as_str()) == k
    assert len(k.short()) == 12


def test_composite_rules():
    w = ScoreWeights.for_profile("reversible")
    assert w.composite(FamilyScores(outcome=1, trajectory=1), False) == pytest.approx(1.0)
    assert w.composite(FamilyScores(outcome=0, trajectory=1), False) == 0.0     # clean path to nowhere
    assert w.composite(FamilyScores(outcome=1, trajectory=1), True) == 0.0      # critical violation
    assert w.composite(FamilyScores(outcome=1, trajectory=0.5), False) < 1.0
