"""Recipe x model validation (#122, A3): a pair that can't run is caught before it runs. Campaign planning leaves it
out with the reason, provisioning refuses it, `hfx validate --models` reports it, and the recipe is never adjusted.
Unknown caps pass, with a note."""

from __future__ import annotations

import asyncio

import pytest

from harness_factory.recipes.validate import pair_issues, pair_notes, validate_recipe
from harness_factory.runner.runner import describe_models_left_out, models_left_out, plan


@pytest.fixture
def capped(factory):
    """scripted/replay returns at most 4,096 output tokens (v2-base asks for exactly that); others are unknown."""
    served = factory.providers.served("scripted", "scripted/replay")
    served.caps = served.caps.model_copy(update={"max_output_tokens": 4096})
    return factory


def recipe_asking(factory, tokens: int, base: str = "v2-base"):
    r = factory.recipes[base]
    slots = {k: v.model_copy(update={"max_output_tokens": tokens}) for k, v in r.model_slots.items()}
    return r.model_copy(update={"model_slots": slots})


def test_a_slot_asking_for_more_output_than_the_model_returns_is_refused(capped):
    r = recipe_asking(capped, 8192)
    (issue,) = pair_issues(r, capped.providers, "scripted", "scripted/replay")
    assert issue.rule == "model_max_output"
    assert issue.detail == "executor asks for up to 8192 output tokens; scripted/replay returns at most 4096"
    assert pair_issues(recipe_asking(capped, 4096), capped.providers, "scripted", "scripted/replay") == []


def test_unknown_caps_pass_with_a_note(capped):
    others = [m.canonical for m in capped.providers.get("scripted").models if m.canonical != "scripted/replay"
              and m.caps.max_output_tokens is None]
    assert others
    r = recipe_asking(capped, 8192)
    assert pair_issues(r, capped.providers, "scripted", others[0]) == []
    (note,) = pair_notes(r, capped.providers, "scripted", others[0])
    assert "maximum output unknown" in note


def test_a_reply_that_fills_the_context_is_refused(capped):
    served = capped.providers.served("scripted", "scripted/replay")
    served.caps = served.caps.model_copy(update={"max_output_tokens": None, "context_len": 8000})
    (issue,) = pair_issues(recipe_asking(capped, 8192), capped.providers, "scripted", "scripted/replay")
    assert issue.rule == "context_fits_reply"


def test_a_model_that_always_reasons_is_noted(capped):
    from harness_factory.providers.catalog import ReasoningCaps
    served = capped.providers.served("scripted", "scripted/replay")
    served.caps = served.caps.model_copy(update={"reasoning": ReasoningCaps(mode="always")})
    assert any("always reasons" in n for n in pair_notes(capped.recipes["v2-base"], capped.providers, "scripted",
                                                         "scripted/replay"))


def test_pinned_slots_are_checked_against_their_own_model(capped):
    r = recipe_asking(capped, 8192, "v4-planner")
    others = [m.canonical for m in capped.providers.get("scripted").models if m.canonical != "scripted/replay"]
    planner = r.model_slots["planner"].model_copy(update={"canonical_model": others[0]})
    r = r.model_copy(update={"model_slots": {**r.model_slots, "planner": planner}})
    # on scripted/replay only the executor (and empty slots) use it; the pinned planner's model is unknown
    issues = pair_issues(r, capped.providers, "scripted", "scripted/replay")
    assert {i.detail.split()[0] for i in issues} == {"executor"}


def test_planning_leaves_the_pair_out_and_says_why(capped, monkeypatch):
    big = recipe_asking(capped, 8192)
    monkeypatch.setitem(capped.recipes, "v2-big", big.model_copy(update={"id": "v2-big"}))
    specs = plan(capped, ["v2-base", "v2-big"], "scripted", ["scripted/replay"], task_ids=["fs-01"])
    assert {s.recipe_id for s in specs} == {"v2-base"}                    # never adjusted to fit: left out
    left = models_left_out(capped, ["v2-base", "v2-big"], "scripted", ["scripted/replay"])
    assert describe_models_left_out(left) == [(
        "v2-big on scripted/replay: model_max_output: executor asks for up to 8192 output tokens; scripted/replay "
        "returns at most 4096")]


def test_provisioning_refuses_the_pair(capped):
    from harness_factory.runner.runner import run_one
    from harness_factory.substrate.errors import ErrorKind
    big = recipe_asking(capped, 8192).model_copy(update={"id": "v2-big"})
    issues = validate_recipe(big.with_model("scripted/replay"), capped.declared, providers=capped.providers,
                             provider_id="scripted")
    assert [i.rule for i in issues] == ["model_max_output"]
    from harness_factory.substrate.errors import HarnessError
    with pytest.raises(HarnessError, match="model_max_output") as e:          # before anything runs
        asyncio.run(run_one(capped, capped.tasks.get("fs-01"), big, "scripted/replay", "scripted"))
    assert e.value.kind == ErrorKind.PROVISION


def test_plain_validate_and_the_authoring_snapshot_still_agree(factory):
    """Recipes carry no model, so the pair check runs only for named models: `hfx validate` alone is unchanged."""
    for rid, r in factory.recipes.items():
        assert not [i for i in validate_recipe(r, factory.declared, providers=factory.providers)
                    if i.rule == "model_max_output"], rid


def test_validate_models_reports_pairs(capped, monkeypatch):
    from typer.testing import CliRunner

    from harness_factory.cli import main as cli
    big = recipe_asking(capped, 8192).model_copy(update={"id": "v2-big"})
    monkeypatch.setitem(capped.recipes, "v2-big", big)
    monkeypatch.setattr(cli, "_factory", lambda store=True: capped)
    monkeypatch.setattr(cli.console, "width", 300)
    out = CliRunner().invoke(cli.app, ["validate", "--models", "scripted/replay"])
    assert out.exit_code == 1
    assert "FAIL v2-big on scripted/replay (scripted): model_max_output" in " ".join(out.output.split())
    out = CliRunner().invoke(cli.app, ["validate", "--models", "no-such-model"])
    assert "model no-such-model: no provider serves it" in out.output


def test_the_bench_lists_models_left_out(capped, monkeypatch, tmp_path):
    import yaml

    from harness_factory.runner import bench as b
    big = recipe_asking(capped, 8192).model_copy(update={"id": "v2-big"})
    monkeypatch.setitem(capped.recipes, "v2-big", big)
    path = tmp_path / "x.bench.yaml"
    path.write_text(yaml.safe_dump({"name": "pairs", "tasks": {"splits": ["DEV", "TEST"], "ids": ["fs-01"]},
                                    "recipes": ["v2-base", "v2-big"],
                                    "models": [{"model": "scripted/replay", "provider": "scripted"}]}))
    spec, _ = b.load(path)
    assert {s.recipe_id for s in b.build_plan(capped, spec)} == {"v2-base"}
    no_model = b.model_reach(capped, spec)
    assert [(r, m) for r, m, _ in no_model] == [("v2-big", "scripted/replay")]
    section = b.reach_section([], [], no_model)
    assert "Left out 1 recipe x model pair(s) that can't run" in section and "v2-big on scripted/replay" in section
