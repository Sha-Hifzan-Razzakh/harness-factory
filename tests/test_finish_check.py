"""Finish check (#126, C2): a run about to end on a reply without tool calls gets one generic reminder and one more
model turn. At most once per run, a step like any other, never the task's files or checks; a do-nothing run still
scores 0."""

from __future__ import annotations

import asyncio

from harness_factory.recipes.model import FINISH_CHECK_NOTE, FinishCheck
from harness_factory.runner.runner import run_one
from harness_factory.substrate.gateway import ScriptedBackend

WRITE = {"tool_calls": [{"name": "fs__write_file", "arguments": {"path": "notes/todo.txt", "content": "buy milk"}}]}
CHAT = {"content": "Here is your todo list: buy milk."}           # the deliverable in the reply, not the file


def with_config(recipe, **config):
    return recipe.model_copy(update={"config": recipe.config.model_copy(update=config)})


def run(factory, recipe, script, task="fs-01"):
    return asyncio.run(run_one(factory, factory.tasks.get(task), recipe, "scripted/replay", "scripted",
                               backend=ScriptedBackend(script)))


def notes(rec) -> list[str]:
    return [m["content"] for m in rec.messages if m.get("role") == "user" and m["content"] == FINISH_CHECK_NOTE]


def test_an_early_reply_gets_the_note_once_and_the_run_continues(factory):
    rec = run(factory, factory.recipes["v2-finish-check"], [CHAT, WRITE, {"content": "done"}])
    assert notes(rec) == [FINISH_CHECK_NOTE]
    assert rec.status.value == "SUCCESS" and rec.cost.model_calls == 3           # the extra turn is a step
    without = run(factory, factory.recipes["v2-base"], [CHAT, WRITE, {"content": "done"}])
    assert without.status.value != "SUCCESS" and without.cost.model_calls == 1 and notes(without) == []


def test_it_fires_at_most_once(factory):
    rec = run(factory, factory.recipes["v2-finish-check"], [CHAT, CHAT, CHAT])
    assert notes(rec) == [FINISH_CHECK_NOTE] and rec.cost.model_calls == 2


def test_a_do_nothing_run_still_scores_zero(factory):
    rec = run(factory, factory.recipes["v2-finish-check"], [{"content": "done"}, {"content": "done"}])
    assert rec.composite == 0 and rec.scores.outcome == 0


def test_the_note_is_generic(factory):
    task = factory.tasks.get("fs-01")
    for c in task.oracle.checks:
        assert not c.path or c.path not in FINISH_CHECK_NOTE
    assert "grade" not in FINISH_CHECK_NOTE.lower() and "check:" not in FINISH_CHECK_NOTE.lower()


def test_a_step_capped_run_ends_as_it_would_have(factory):
    recipe = with_config(factory.recipes["v2-finish-check"], max_steps=1)
    rec = run(factory, recipe, [CHAT, WRITE])
    assert notes(rec) == [] and rec.cost.model_calls == 1
    assert not any(v.kind == "budget_exceeded" for v in rec.violations)


def test_a_custom_note(factory):
    recipe = with_config(factory.recipes["v2-base"], finish_check=FinishCheck(note="Save your work, then reply."))
    rec = run(factory, recipe, [CHAT, WRITE, {"content": "done"}])
    assert [m["content"] for m in rec.messages if m.get("role") == "user"][-1] == "Save your work, then reply."


def test_v3_fires_only_in_its_last_stage(factory):
    """A V3 stage ends on a reply without tool calls; only the last stage's reply ends the run, so the note comes
    after the last stage's prompt and nowhere else."""
    recipe = with_config(factory.recipes["v3-staged"], finish_check=FinishCheck())
    stages = len(recipe.workflow.stages)
    rec = run(factory, recipe, [CHAT] * (stages + 3))
    users = [m["content"] for m in rec.messages if m.get("role") == "user"]
    assert users.count(FINISH_CHECK_NOTE) == 1
    assert users[-1] == FINISH_CHECK_NOTE                             # right after the last stage's prompt
    assert rec.cost.model_calls == stages + 1


def test_unset_leaves_every_recipe_hash_alone(factory):
    r = factory.recipes["v2-base"]
    assert with_config(r, finish_check=None).content_hash() == r.content_hash()
    assert factory.recipes["v2-finish-check"].content_hash() != r.content_hash()


def test_framework_runners_refuse_it(factory):
    from harness_factory.recipes.validate import validate_recipe
    r = with_config(factory.recipes["v2-langgraph-prebuilt"], finish_check=FinishCheck())
    assert any("finish_check" in i.detail for i in validate_recipe(r, factory.declared))
