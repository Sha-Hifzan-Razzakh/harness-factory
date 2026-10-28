from harness_factory import settings
from harness_factory.runner.runner import run_one


async def test_runs_do_not_share_state(factory):
    """Canary: the file the first run writes must not exist when the second run starts."""
    task, recipe = factory.tasks.get("fs-01"), factory.recipes["v2-base"]
    first = await run_one(factory, task, recipe, "scripted/replay", "scripted")
    second = await run_one(factory, task, recipe, "scripted/replay", "scripted", seed=1)
    for rec in (first, second):
        state = next(g for g in rec.grades if g["grader_id"] == "state_diff")
        assert state["detail"] == "+files notes/todo.txt"     # created by this run, not inherited


async def test_sandbox_removed_after_run(factory):
    before = set(settings.SANDBOXES_DIR.iterdir()) if settings.SANDBOXES_DIR.exists() else set()
    await run_one(factory, factory.tasks.get("fs-01"), factory.recipes["v2-base"], "scripted/replay", "scripted")
    assert set(settings.SANDBOXES_DIR.iterdir()) == before
