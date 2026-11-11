"""Reasoning effort (#123, A4): a model-slot dimension sent to the provider through LiteLLM; refused before the run
when the model's caps rule it out; existing recipe hashes stay."""

from __future__ import annotations

import pytest
from test_gateway_litellm import _gateway, _resp, _script, no_backoff  # noqa: F401  (fixture)

from harness_factory.providers.catalog import ReasoningCaps
from harness_factory.recipes.model import ModelSlot
from harness_factory.recipes.validate import pair_issues, pair_notes


def test_unset_effort_leaves_every_recipe_hash_alone(factory):
    r = factory.recipes["v2-base"]
    assert "reasoning_effort" not in str(r.model_slots["executor"].model_dump(exclude_none=True))
    low = factory.recipes["v2-reasoning-low"]
    assert low.model_slots["executor"].reasoning_effort == "low" and low.content_hash() != r.content_hash()
    # the same recipe with the field spelled out as None hashes as before
    slots = {k: v.model_copy(update={"reasoning_effort": None}) for k, v in r.model_slots.items()}
    assert r.model_copy(update={"model_slots": slots}).content_hash() == r.content_hash()


def test_an_unknown_level_is_refused_by_the_recipe():
    with pytest.raises(ValueError):
        ModelSlot(reasoning_effort="max")


@pytest.mark.usefixtures("no_backoff")
async def test_the_effort_reaches_the_request(factory, monkeypatch):
    calls = _script(monkeypatch, _resp(content="hi"))
    gw = _gateway(factory.providers, factory.ledger)
    await gw.complete(ModelSlot(canonical_model="gpt-oss-120b", reasoning_effort="low"),
                      [{"role": "user", "content": "x"}], [])
    assert calls[0]["reasoning_effort"] == "low" and calls[0]["drop_params"] is True
    span = [s for s in gw.tracer.spans if s.kind.value == "model_call"][-1]
    assert span.attributes["reasoning_effort"] == "low"


@pytest.mark.usefixtures("no_backoff")
async def test_without_an_effort_nothing_is_sent(factory, monkeypatch):
    calls = _script(monkeypatch, _resp(content="hi"))
    gw = _gateway(factory.providers, factory.ledger)
    await gw.complete(ModelSlot(canonical_model="gpt-oss-120b"), [{"role": "user", "content": "x"}], [])
    assert "reasoning_effort" not in calls[0]


@pytest.fixture
def reasoning(factory):
    served = factory.providers.served("scripted", "scripted/replay")

    def set_caps(caps):
        served.caps = served.caps.model_copy(update={"reasoning": caps})
    yield set_caps
    set_caps(None)


def test_a_model_that_doesnt_reason_is_refused(factory, reasoning):
    reasoning(ReasoningCaps(mode="none"))
    (issue,) = pair_issues(factory.recipes["v2-reasoning-low"], factory.providers, "scripted", "scripted/replay")
    assert issue.rule == "model_reasoning" and "doesn't reason" in issue.detail
    assert pair_issues(factory.recipes["v2-base"], factory.providers, "scripted", "scripted/replay") == []


def test_a_level_the_model_doesnt_take_is_refused(factory, reasoning):
    reasoning(ReasoningCaps(mode="optional", efforts=["medium", "high"]))
    (issue,) = pair_issues(factory.recipes["v2-reasoning-low"], factory.providers, "scripted", "scripted/replay")
    assert issue.detail == "executor sets reasoning_effort low; scripted/replay accepts medium, high"
    reasoning(ReasoningCaps(mode="always", efforts=["low", "high"]))
    assert pair_issues(factory.recipes["v2-reasoning-low"], factory.providers, "scripted", "scripted/replay") == []


def test_unknown_reasoning_passes_with_a_note(factory, reasoning):
    reasoning(None)
    r = factory.recipes["v2-reasoning-low"]
    assert pair_issues(r, factory.providers, "scripted", "scripted/replay") == []
    assert any("reasoning_effort is sent unchecked" in n for n in pair_notes(r, factory.providers, "scripted",
                                                                             "scripted/replay"))


def test_planning_leaves_the_pair_out(factory, reasoning):
    from harness_factory.runner.runner import models_left_out, plan
    reasoning(ReasoningCaps(mode="none"))
    specs = plan(factory, ["v2-base", "v2-reasoning-low"], "scripted", ["scripted/replay"], task_ids=["fs-01"])
    assert {s.recipe_id for s in specs} == {"v2-base"}
    (left,) = models_left_out(factory, ["v2-reasoning-low"], "scripted", ["scripted/replay"])
    assert left[2][0].startswith("model_reasoning:")
