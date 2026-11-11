"""Budget notice (#125, C1): one short line after the tool results says what tool calls and steps are left, so
the model can stop exploring and deliver. It adds tokens, never steps or tool calls."""

from __future__ import annotations

import asyncio

import pytest
from pydantic import ValidationError

from harness_factory.recipes.model import BudgetNotice
from harness_factory.runner.runner import run_one
from harness_factory.substrate.gateway import ScriptedBackend

READ = {"tool_calls": [{"name": "fs__read_file", "arguments": {"path": "notes/README.txt"}}]}
LIST = {"tool_calls": [{"name": "fs__list_dir", "arguments": {"path": "notes"}}]}
WRITE = {"tool_calls": [{"name": "fs__write_file", "arguments": {"path": "notes/todo.txt", "content": "buy milk"}}]}


def with_config(recipe, **config):
    return recipe.model_copy(update={"config": recipe.config.model_copy(update=config)})


def run(factory, recipe, backend, task="fs-01"):
    return asyncio.run(run_one(factory, factory.tasks.get(task), recipe, "scripted/replay", "scripted",
                               backend=backend))


def tool_texts(rec) -> list[str]:
    return [m["content"] for m in rec.messages if m.get("role") == "tool"]


def test_each_result_carries_the_budget_left(factory):
    recipe = with_config(factory.recipes["v2-budget-notice"], max_tool_calls=10, max_steps=8)
    rec = run(factory, recipe, ScriptedBackend([READ, LIST, WRITE, {"content": "done"}]))
    notes = [t.rsplit("\n", 1)[-1] for t in tool_texts(rec)]
    assert notes == ["[budget] tool calls left: 9 of 10; steps left: 7 of 8",
                     "[budget] tool calls left: 8 of 10; steps left: 6 of 8",
                     "[budget] tool calls left: 7 of 10; steps left: 5 of 8"]
    assert rec.cost.model_calls == 4 and rec.cost.tool_calls == 3          # no extra step or call
    assert not any(w in t.lower() for t in notes for w in ("fault", "grade", "check", "score"))


def test_without_the_dimension_nothing_is_added(factory):
    rec = run(factory, factory.recipes["v2-base"], ScriptedBackend([READ, WRITE, {"content": "done"}]))
    assert not any("[budget]" in t for t in tool_texts(rec))


def test_thresholds_fire_once_each(factory):
    recipe = with_config(factory.recipes["v2-base"], max_tool_calls=4, max_steps=20,
                         budget_notice=BudgetNotice(mode="thresholds", at=[0.5, 0.75]))
    rec = run(factory, recipe, ScriptedBackend([READ, LIST, READ, WRITE, {"content": "done"}]))
    flags = ["[budget]" in t for t in tool_texts(rec)]
    assert flags == [False, True, True, False]            # 2/4 crosses 0.5, 3/4 crosses 0.75, 4/4 adds nothing new


class ReadsTheNotice(ScriptedBackend):
    """A policy that explores until the notice says two tool calls are left, then writes the file. Without the
    notice it never knows, explores, and runs out."""

    def __init__(self):
        super().__init__([])

    async def complete(self, messages, tools, slot, provider, served):
        last = next((m["content"] for m in reversed(messages) if m.get("role") == "tool"), "")
        wrote = any(c["function"]["name"] == "fs__write_file" for m in messages if m.get("role") == "assistant"
                    for c in m.get("tool_calls") or [])
        if wrote:
            self.turns = [{"content": "done"}]
        elif "tool calls left: 2 of" in last or "tool calls left: 1 of" in last:
            self.turns = [WRITE]
        else:
            self.turns = [READ if len(messages) % 2 else LIST]
        self.i = 0
        return await super().complete(messages, tools, slot, provider, served)


def test_a_policy_that_reads_the_notice_delivers_before_the_limit(factory):
    base = with_config(factory.recipes["v2-base"], max_tool_calls=5, max_steps=12)
    noticed = with_config(base, budget_notice=BudgetNotice())
    blind = run(factory, base, ReadsTheNotice())
    told = run(factory, noticed, ReadsTheNotice())
    assert any(v.kind == "budget_exceeded" for v in blind.violations) and blind.scores.outcome < 1
    assert told.status.value == "SUCCESS" and told.cost.tool_calls <= 5


@pytest.mark.parametrize("at", [[], [0], [1.0], [0.5, 0.5]])
def test_thresholds_are_fractions(at):
    with pytest.raises(ValidationError, match="fractions"):
        BudgetNotice(mode="thresholds", at=at)


def test_unset_leaves_every_recipe_hash_alone(factory):
    r = factory.recipes["v2-base"]
    assert with_config(r, budget_notice=None).content_hash() == r.content_hash()
    assert factory.recipes["v2-budget-notice"].content_hash() != r.content_hash()


def test_framework_runners_refuse_it(factory):
    from harness_factory.recipes.validate import validate_recipe
    r = with_config(factory.recipes["v2-langgraph-prebuilt"], budget_notice=BudgetNotice())
    assert any("budget_notice" in i.detail for i in validate_recipe(r, factory.declared))
