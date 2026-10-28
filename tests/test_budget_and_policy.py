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
