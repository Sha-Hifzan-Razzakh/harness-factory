"""LangGraph checkpointer (SUB-09, FWK-01): a V2-V5 run paused between two nodes resumes from its last node and ends
exactly like the same run left alone: same status, outcome, composite, calls, tokens, conversation and violations."""

import json
from pathlib import Path
from types import SimpleNamespace
from typing import TypedDict

import pytest
from langgraph.errors import GraphRecursionError
from langgraph.graph import END, START, StateGraph

from harness_factory import settings
from harness_factory.provision.provisioner import provision
from harness_factory.runner.runner import Factory, plan, run_campaign, run_one
from harness_factory.shapes.base import ShapeRegistry
from harness_factory.shapes.checkpoint import (
    Checkpoint,
    CheckpointConfig,
    RunPaused,
    open_saver,
    pause_after,
    pause_every,
    run_graph,
)


def _spans(rec, kind=None):
    spans = [json.loads(line) for line in Path(rec.trace_path).read_text().splitlines()]
    return [s for s in spans if kind is None or s["kind"] == kind]


def _snapshot(rec) -> dict:
    return next(g for g in rec.grades if g.get("grader_id") == "state_diff")["snapshot"]


def _same_run(plain, resumed) -> None:
    """Everything a score or a reader sees, apart from the resume itself."""
    assert resumed.run_key == plain.run_key
    assert resumed.status == plain.status
    assert resumed.scores == plain.scores
    assert resumed.composite == plain.composite
    for field in ("model_calls", "failed_calls", "tool_calls", "tokens_in", "tokens_out", "usd"):
        assert getattr(resumed.cost, field) == getattr(plain.cost, field), field
    assert resumed.messages == plain.messages
    assert resumed.output == plain.output
    assert [(v.kind, v.detail) for v in resumed.violations] == [(v.kind, v.detail) for v in plain.violations]
    names = lambda rec: [(s["kind"], s["name"]) for s in _spans(rec) if s["name"] != "resume"]
    assert names(resumed) == names(plain)


@pytest.fixture
def cp_config(tmp_path) -> CheckpointConfig:
    return CheckpointConfig("sqlite", str(tmp_path / "checkpoints.db"))


# ------------------------------------------------------------- the done-when test

@pytest.mark.parametrize("recipe_id,model,fault", [
    ("v2-base", "scripted/noisy", None),                   # an invalid call first: Q counts it in both
    ("v2-base", "scripted/retry", "first-call-fails"),     # fault injector state carries over the pauses
    ("v3-staged", "scripted/replay", None),
    ("v4-planner", "scripted/lazy", None),
    ("v5-orchestrator", "scripted/replay", None),
])
async def test_paused_run_ends_like_an_uninterrupted_one(factory, cp_config, recipe_id, model, fault):
    task, recipe = factory.tasks.get("sql-02"), factory.recipes[recipe_id]
    plain = await run_one(factory, task, recipe, model, "scripted", fault=fault)
    factory.checkpoint = cp_config
    resumed = await run_one(factory, task, recipe, model, "scripted", fault=fault, pause=pause_every())

    _same_run(plain, resumed)
    cp = _snapshot(resumed)["checkpoint"]
    assert cp["backend"] == "sqlite" and cp["thread_id"].startswith(plain.run_key.short())
    resumes = _spans(resumed, "handoff")
    assert cp["resumes"] == len([s for s in resumes if s["name"] == "resume"]) >= 2
    assert "checkpoint" not in _snapshot(plain)


async def test_pause_once_mid_graph(factory, cp_config):
    task, recipe = factory.tasks.get("mix-01"), factory.recipes["v3-staged"]
    plain = await run_one(factory, task, recipe, "scripted/replay", "scripted")
    factory.checkpoint = cp_config
    resumed = await run_one(factory, task, recipe, "scripted/replay", "scripted", pause=pause_after(3))
    _same_run(plain, resumed)
    [span] = [s for s in _spans(resumed, "handoff") if s["name"] == "resume"]
    assert span["attributes"]["after_nodes"] == 3
    assert _snapshot(resumed)["checkpoint"]["resumes"] == 1


async def test_budget_stop_is_not_extended_by_a_resume(factory, cp_config):
    """No free steps: the budget is the same object across the pause, so the run stops where it would have."""
    base = factory.recipes["v2-base"]
    capped = base.model_copy(update={"config": base.config.model_copy(update={"max_steps": 2})})
    task = factory.tasks.get("sql-02")                       # needs 3 model calls
    plain = await run_one(factory, task, capped, "scripted/replay", "scripted")
    factory.checkpoint = cp_config
    resumed = await run_one(factory, task, capped, "scripted/replay", "scripted", pause=pause_every())
    assert plain.status.value == "PARTIAL" and any(v.kind == "budget_exceeded" for v in plain.violations)
    _same_run(plain, resumed)
    assert resumed.cost.model_calls == 2


# ------------------------------------------------------------- the graph state comes from the saver

async def test_resume_reads_the_graph_state_back_from_the_saver(factory, cp_config):
    """Pause, close the saver, resume through a new connection to the same file: LangGraph continues the thread
    from the node that was next. Per-run objects (budget, tracer, sandbox) are the instance's."""
    task, recipe = factory.tasks.get("sql-02"), factory.recipes["v3-staged"].with_model("scripted/replay")
    shape = ShapeRegistry.get(recipe.variant)
    kw = dict(declared=factory.declared, providers=factory.providers, ledger=factory.ledger)

    async with provision(task, recipe, "scripted/replay", "scripted", **kw) as p:
        plain = await shape.run(p.instance)
        plain_steps = p.instance.budget.used_steps

    async with provision(task, recipe, "scripted/replay", "scripted", **kw) as p:
        inst = p.instance
        thread = Checkpoint.thread_for(inst.run_key.short())
        async with open_saver(cp_config) as saver:
            inst.checkpoint = Checkpoint(saver=saver, thread_id=thread, backend="sqlite", pause=pause_after(4))
            with pytest.raises(RunPaused) as paused:
                await shape.run(inst)
        assert paused.value.nodes == 4 and paused.value.next_nodes
        steps_at_pause = inst.budget.used_steps
        async with open_saver(cp_config) as saver:
            saved = [c async for c in saver.alist({"configurable": {"thread_id": thread}})]
            assert saved and max(c.metadata["step"] for c in saved) == 4
            inst.checkpoint = Checkpoint(saver=saver, thread_id=thread, backend="sqlite", resuming=True)
            resumed = await shape.run(inst)
            inst.checkpoint.resuming = True            # the thread is finished: nothing to resume
            with pytest.raises(RuntimeError, match="nothing to resume"):
                await shape.run(inst)

    assert 0 < steps_at_pause < inst.budget.used_steps == plain_steps
    assert resumed.status == plain.status and resumed.messages == plain.messages
    assert resumed.output == plain.output


# ------------------------------------------------------------- recursion limit, counted across invocations

class Count(TypedDict):
    n: int
    stop: bool


def _toy(ran: list[str], finish_after: int | None):
    async def a(s):
        ran.append("a")
        return {"n": s["n"] + 1}

    async def b(s):
        ran.append("b")
        return {"n": s["n"] + 1, "stop": finish_after is not None and s["n"] + 1 >= finish_after}

    g = StateGraph(Count)
    g.add_node("a", a)
    g.add_node("b", b)
    g.add_edge(START, "a")
    g.add_edge("a", "b")
    g.add_conditional_edges("b", lambda s: END if s["stop"] else "a", {"a": "a", END: END})
    return g


async def _drive(inst, limit, finish_after):
    """(final state or "recursion", nodes run), resuming after every pause."""
    ran: list[str] = []
    builder = _toy(ran, finish_after)
    while True:
        try:
            return await run_graph(inst, builder, {"n": 0, "stop": False}, limit), len(ran)
        except GraphRecursionError:
            return "recursion", len(ran)
        except RunPaused:
            inst.checkpoint.resuming = True


@pytest.mark.parametrize("finish_after", [None, 4, 6])
async def test_recursion_limit_matches_a_plain_run(cp_config, finish_after):
    """A checkpointed graph hits LangGraph's recursion limit at exactly the node a plain ainvoke does, however
    many times it was paused and resumed: the step counter lives in the checkpoint."""
    async with open_saver(cp_config) as saver:
        for limit in range(1, 9):
            plain = await _drive(SimpleNamespace(checkpoint=None), limit, finish_after)
            cp = Checkpoint(saver=saver, thread_id=f"toy-{finish_after}-{limit}", backend="sqlite", pause=pause_every())
            resumed = await _drive(SimpleNamespace(checkpoint=cp), limit, finish_after)
            assert resumed == plain, (limit, finish_after)
            assert cp.resumes == 0                      # the runner counts resumes; this drives run_graph directly


# ------------------------------------------------------------- opt-in, configuration, campaigns

async def test_off_by_default(factory, monkeypatch):
    monkeypatch.delenv("HF_CHECKPOINT", raising=False)
    assert settings.checkpoint_backend() is None
    assert Factory.load(store=False).checkpoint is None
    task, recipe = factory.tasks.get("fs-01"), factory.recipes["v2-base"]
    with pytest.raises(ValueError, match="needs checkpointing"):
        await run_one(factory, task, recipe, "scripted/replay", "scripted", pause=pause_after(1))
    rec = await run_one(factory, task, recipe, "scripted/replay", "scripted")
    assert "checkpoint" not in _snapshot(rec) and not [s for s in _spans(rec) if s["name"] == "resume"]


def test_settings(monkeypatch, tmp_path):
    monkeypatch.setenv("HF_CHECKPOINT", "sqlite")
    monkeypatch.delenv("HF_CHECKPOINT_URL", raising=False)
    assert settings.checkpoint_backend() == "sqlite"
    assert settings.checkpoint_url("sqlite").endswith("checkpoints.db")
    monkeypatch.setenv("HF_CHECKPOINT_URL", str(tmp_path / "cp.db"))
    assert Factory.load(store=False).checkpoint == CheckpointConfig("sqlite", str(tmp_path / "cp.db"))
    monkeypatch.setenv("HF_CHECKPOINT", "redis")
    with pytest.raises(ValueError, match="HF_CHECKPOINT"):
        settings.checkpoint_backend()
    monkeypatch.delenv("HF_CHECKPOINT_URL")
    with pytest.raises(ValueError, match="needs a url"):
        CheckpointConfig("postgres", settings.checkpoint_url("postgres"))


async def test_concurrent_runs_share_one_sqlite_file(factory, cp_config, p0_tasks):
    factory.checkpoint = cp_config
    specs = plan(factory, ["v2-base", "v3-staged"], "scripted", ["scripted/replay"], task_ids=p0_tasks[:3])
    result = await run_campaign(factory, specs, concurrency=4)
    assert not result.errors and all(r.status.value == "SUCCESS" for r in result.records)
    threads = {_snapshot(r)["checkpoint"]["thread_id"] for r in result.records}
    assert len(threads) == len(specs)
    async with open_saver(cp_config) as saver:
        for thread in threads:
            assert [c async for c in saver.alist({"configurable": {"thread_id": thread}}, limit=1)]
