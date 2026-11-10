"""Spec cards (cards/build.py): built from the same objects a run uses, offline, and never running anything."""

import asyncio
import json

import pytest

from harness_factory.cards.build import (
    attach_result,
    fault_card,
    harness_card,
    model_card,
    recipe_changes,
    spec_card,
    task_card,
    tool_card,
)
from harness_factory.cards.render import save, to_markdown
from harness_factory.provision.provisioner import allowed_tools, run_key_for
from harness_factory.runner.runner import run_one
from harness_factory.substrate.tools import model_name


def test_building_block_cards_render(factory, p0_tasks):
    cards = [model_card(factory, "scripted/replay"), harness_card(factory, "v2-base"),
             tool_card(factory, "fs"), fault_card(factory, "transient")]
    cards += [task_card(factory, t) for t in p0_tasks]
    for c in cards:
        md = to_markdown(c)
        assert md.startswith(f"# {c.title}")
        assert json.loads(c.model_dump_json())["kind"] == c.kind


def test_unknown_items_are_refused(factory):
    with pytest.raises(KeyError):
        model_card(factory, "no-such-model")
    with pytest.raises(KeyError):
        harness_card(factory, "no-such-recipe")
    with pytest.raises(KeyError):
        fault_card(factory, "none")


def test_harness_card_shows_only_what_a_matrix_recipe_changes(factory):
    assert recipe_changes(factory, "v2-recover") == [("config.recover_malformed", "-", "2")]
    assert recipe_changes(factory, "v2-base") == []                         # a base recipe has no base
    card = harness_card(factory, "v2-recover")
    assert card.hash == factory.recipes["v2-recover"].content_hash()
    assert card.section("Changes vs v2-base").table.rows == [["config.recover_malformed", "-", "2"]]


def test_spec_card_matches_what_the_run_will_use(factory):
    task, recipe = factory.tasks.get("fs-02"), factory.recipes["v2-base"]
    card = spec_card(factory, "fs-02", "v2-base", "scripted/replay", "scripted", seed=3)
    key = run_key_for(task, recipe, "scripted/replay", "scripted", 3, declared=factory.declared,
                      providers=factory.providers)
    assert card.verdict == "READY" and not card.issues
    assert card.id == key.short()
    offered = [r[0] for r in card.section("Tools the model will see").table.rows]
    assert offered == sorted(model_name(r) for r in allowed_tools(recipe, task, factory.declared))
    system = card.section("Prompt sent: system").text
    assert "fs__write_file" in system
    everything = to_markdown(card)
    assert "host = 0.0.0.0" not in everything.split("## Prompt sent")[1]     # the oracle never reaches the prompts


def test_spec_card_blocks_a_recipe_that_cant_reach_the_task(factory):
    card = spec_card(factory, "code-02", "v2-base", "scripted/replay", "scripted", prompts=False)
    assert card.verdict == "BLOCKED"
    assert any("can't call" in i for i in card.issues)
    assert spec_card(factory, "code-02", "v2-code", "scripted/replay", "scripted", prompts=False).verdict == "READY"


def test_final_spec_card_carries_the_result(factory, tmp_path, monkeypatch):
    from harness_factory import settings
    monkeypatch.setattr(settings, "VAR", tmp_path / "var")
    card = spec_card(factory, "fs-02", "v2-base", "scripted/replay", "scripted", prompts=False)
    rec = asyncio.run(run_one(factory, factory.tasks.get("fs-02"), factory.recipes["v2-base"],
                              "scripted/replay", "scripted", 0))
    assert rec.run_key.short() == card.id                   # the card predicted the run's identity
    final = attach_result(card, rec)
    assert final.result["status"] == "SUCCESS" and final.result["E0"] == 1.0
    assert final.sections[0].title == "Result"
    path = save(final)
    assert path.parent == tmp_path / "var" / "cards" / "spec" and path.with_suffix(".json").is_file()
    again = spec_card(factory, "fs-02", "v2-base", "scripted/replay", "scripted", prompts=False)
    assert again.section("Already run") is not None         # the store now has a row with this key
