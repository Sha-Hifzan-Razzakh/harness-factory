import pytest

from harness_factory.runner.runner import Factory, run_reference

TASK_IDS = [t.id for t in Factory.load(store=False).tasks.all(include_held_out=True)]


@pytest.mark.parametrize("task_id", TASK_IDS)
async def test_reference_passes_own_oracle(factory, task_id):
    grade = await run_reference(factory, factory.tasks.get(task_id))
    assert grade.passed, [c for c in grade.checks if not c.passed]
