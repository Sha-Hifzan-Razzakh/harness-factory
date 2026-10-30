from harness_factory.recipes.validate import validate_recipe
from harness_factory.runner.runner import run_one
from harness_factory.substrate.gateway import ScriptedBackend


async def test_tool_call_budget_stops_run(factory):
    base = factory.recipes["v2-base"]
    tight = base.model_copy(update={"config": base.config.model_copy(update={"max_tool_calls": 1})})
    rec = await run_one(factory, factory.tasks.get("fs-02"), tight, "scripted/replay", "scripted")
    assert rec.cost.tool_calls == 1                             # the read ran; the write never did
    assert rec.status.value == "FAIL"                           # graded on the environment: port unchanged
    assert any(v.kind == "budget_exceeded" for v in rec.violations)


async def test_tool_outside_task_allowance_is_refused(factory):
    backend = ScriptedBackend([{"tool_calls": [{"name": "fs__delete_file", "arguments": {"path": "notes/README.txt"}}]},
                               {"content": "done"}])
    rec = await run_one(factory, factory.tasks.get("fs-01"), factory.recipes["v2-base"], "scripted/replay",
                        "scripted", backend=backend)
    assert any(v.kind == "tool_not_allowed" for v in rec.violations)
    assert rec.status.value == "FAIL"


async def test_policy_denies_writes(factory):
    base = factory.recipes["v2-base"]
    ro = base.model_copy(update={"policies": base.policies.model_copy(
        update={"permission_mode": {"READ": "AUTO", "REVERSIBLE": "DENY", "IRREVERSIBLE": "DENY"}})})
    rec = await run_one(factory, factory.tasks.get("fs-01"), ro, "scripted/replay", "scripted")
    assert any(v.kind == "policy_denied" for v in rec.violations)
    assert rec.status.value == "FAIL"


def test_tier_must_cover_reachable_risk(factory):
    base = factory.recipes["v2-base"]
    assert validate_recipe(base, factory.declared) == []
    low = base.model_copy(update={"tier": "T1"})
    assert any(i.rule == "tier_covers_risk" for i in validate_recipe(low, factory.declared))
    deletes = base.model_copy(update={"policies": base.policies.model_copy(
        update={"permission_mode": {"READ": "AUTO", "REVERSIBLE": "AUTO", "IRREVERSIBLE": "AUTO"}})})
    assert any(i.rule == "tier_covers_risk" for i in validate_recipe(deletes, factory.declared))


def test_a_guard_excludes_only_the_waits_after_it_started():
    """A delegate's guard shares its parent's cumulative excluded time (quota waits, backoff). Waits from before the
    helper started are not the helper's to keep (R-11)."""
    import time

    from harness_factory.recipes.model import RuntimeConfig
    from harness_factory.substrate.budget import BudgetGuard

    waits = {"s": 0.0}
    parent = BudgetGuard(RuntimeConfig(wall_time_s=100), excluded_s=lambda: waits["s"])
    parent.start()
    waits["s"] = 90.0                                         # 90 s of quota waits before the helper starts
    child = BudgetGuard(RuntimeConfig(wall_time_s=10), excluded_s=parent.excluded_s)
    child.start()
    child.started_at -= 12                                    # 12 s of the helper's own time, no new waits
    assert child.elapsed_s() >= 12
    assert child.exceeded_by() == "wall_time_s 10"
    waits["s"] = 95.0                                         # a 5 s wait while the helper runs is excluded
    assert 6.9 < child.elapsed_s() < 7.5
    assert time.monotonic() >= child.started_at
