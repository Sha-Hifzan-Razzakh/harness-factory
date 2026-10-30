"""Run identity: what must change a RunKey, and what must not."""

import pytest

from harness_factory.recipes.model import default_prompt_sources
from harness_factory.results.store import ResultsStore
from harness_factory.runner.runner import run_one, smoke
from harness_factory.runner.spec import RunKey
from harness_factory.substrate.errors import HarnessError


async def test_seed_is_not_part_of_the_recipe_hash(factory):
    task, recipe = factory.tasks.get("fs-01"), factory.recipes["v2-base"]
    a = await run_one(factory, task, recipe, "scripted/replay", "scripted", seed=0)
    b = await run_one(factory, task, recipe, "scripted/replay", "scripted", seed=1)
    assert a.run_key.recipe_hash == b.run_key.recipe_hash == recipe.content_hash()   # == hfx validate
    assert a.run_key.seed != b.run_key.seed and a.run_key.as_str() != b.run_key.as_str()


async def test_seeds_pool_into_one_cell(factory, tmp_path, p0_tasks):
    factory.store = ResultsStore(f"sqlite:///{tmp_path / 'r.db'}")
    await smoke(factory, "v2-base", "scripted", ["scripted/replay"], seeds=3, concurrency=4, task_ids=p0_tasks)
    _, cells = factory.store.aggregate()
    assert {c.e.n for c in cells if c.task_class.startswith("SINGLE_STEP|READ")} == {3}


def test_editing_a_prompt_template_changes_the_recipe_hash(factory):
    r = factory.recipes["v3-staged"]
    sources = default_prompt_sources()
    for ref in ("system/default", "task/default", "stage/verify"):
        edited = {**sources, ref: sources[ref] + "\nBe brief."}
        assert r.content_hash(edited) != r.content_hash(sources), ref
    unrelated = {**sources, "system/single_call": "something else"}
    assert r.content_hash(unrelated) == r.content_hash(sources)        # templates it doesn't use don't count


def test_env_hash_tracks_tools_and_provider_model(factory):
    d = factory.declared
    base = d.env_hash(["fs", "sql"], "llama-3.3-70b-versatile")
    assert base == d.env_hash(["fs", "sql"], "llama-3.3-70b-versatile")
    assert base != d.env_hash(["fs", "sql"], "llama-3.3-70b-specdec")         # provider swapped the model
    slower = d.model_copy(deep=True)
    slower.server("sql").tools[2].timeout_s = 99
    assert base != slower.env_hash(["fs", "sql"], "llama-3.3-70b-versatile")   # tool spec changed
    assert base != d.env_hash(["fs"], "llama-3.3-70b-versatile")               # different servers opened


def test_run_key_roundtrip_includes_env_hash():
    k = RunKey(task_version="fs-01@1", task_hash="t", recipe_hash="r", canonical_model="m", provider="p",
               seed=2, env_hash="e")
    assert RunKey.from_str(k.as_str()) == k


async def test_tool_schema_drift_now_fails_provisioning(factory):
    drifted = factory.declared.model_copy(deep=True)
    drifted.server("fs").tools[1].schema_hash = "0000000000000000"
    factory.declared = drifted
    with pytest.raises(HarnessError, match="schema_changed"):
        await run_one(factory, factory.tasks.get("fs-01"), factory.recipes["v2-base"], "scripted/replay",
                      "scripted")


def test_task_labels_are_not_identity(factory):
    t = factory.tasks.get("fs-01")
    relabelled = t.model_copy(update={"split": "TEST", "tags": ["x"]})
    relabelled.source_dir = t.source_dir
    assert relabelled.content_hash() == t.content_hash()
    reworded = t.model_copy(update={"instruction": t.instruction + " Now."})
    reworded.source_dir = t.source_dir
    assert reworded.content_hash() != t.content_hash()
