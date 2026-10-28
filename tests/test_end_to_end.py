import json
from pathlib import Path

import pytest

from harness_factory.runner.runner import Factory, run_one

TASK_IDS = [t.id for t in Factory.load(store=False).tasks.all()]


@pytest.mark.parametrize("task_id", TASK_IDS)
async def test_replay_succeeds_and_is_recorded(factory, task_id):
    rec = await run_one(factory, factory.tasks.get(task_id), factory.recipes["v2-base"], "scripted/replay", "scripted")
    assert rec.status.value == "SUCCESS", rec.grades
    assert rec.composite == pytest.approx(1.0)
    kinds = {json.loads(line)["kind"] for line in Path(rec.trace_path).read_text().splitlines()}
    assert {"run", "model_call", "tool_call", "grade"} <= kinds
    rows = factory.store.recent()
    assert rows[0]["run_key"] == rec.run_key.as_str()


async def test_lazy_model_fails_and_noisy_scores_lower(factory):
    task, recipe = factory.tasks.get("fs-02"), factory.recipes["v2-base"]
    lazy = await run_one(factory, task, recipe, "scripted/lazy", "scripted")
    noisy = await run_one(factory, task, recipe, "scripted/noisy", "scripted")
    assert lazy.status.value == "FAIL" and lazy.composite == 0
    assert noisy.status.value == "SUCCESS" and 0 < noisy.composite < 1


async def test_ungrounded_answer_is_not_success(factory):
    rec = await run_one(factory, factory.tasks.get("sql-01"), factory.recipes["v2-base"], "scripted/lazy", "scripted")
    assert rec.status.value == "PARTIAL"
