"""Turn spans (#117): each model turn is a `turn <n>` span (CHAIN in Phoenix) holding its model calls, and the tool
batch it asked for is `turn <n> · tools` holding the tool calls. Trace only: no count, budget or score changes."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from harness_factory.observability import tracer as tracer_mod
from harness_factory.runner.runner import run_one
from harness_factory.shapes import common
from harness_factory.shapes.checkpoint import CheckpointConfig, pause_every
from harness_factory.substrate.gateway import ScriptedBackend

README = ("fs__read_file", {"path": "notes/README.txt"})
LIST = ("fs__list_dir", {"path": "notes"})
TODO = ("fs__write_file", {"path": "notes/todo.txt", "content": "buy milk"})


def batch(*calls):
    return {"tool_calls": [{"name": n, "arguments": a} for n, a in calls]}


def spans(rec) -> list[dict]:
    return [json.loads(line) for line in Path(rec.trace_path).read_text().splitlines()]


def tree(rec) -> tuple[dict, dict[str, dict]]:
    """(root, spans by id)."""
    ss = spans(rec)
    by_id = {s["span_id"]: s for s in ss}
    (root,) = [s for s in ss if s["kind"] == "run"]
    return root, by_id


def turn_of(span: dict, by_id: dict) -> dict | None:
    """The nearest turn span above `span`."""
    p = by_id.get(span["parent_id"])
    while p is not None and p["kind"] != "turn":
        p = by_id.get(p["parent_id"])
    return p


def run(factory, task="fs-02", recipe="v2-base", model="scripted/replay", **kw):
    r = factory.recipes[recipe] if isinstance(recipe, str) else recipe
    return asyncio.run(run_one(factory, factory.tasks.get(task), r, model, "scripted", **kw))


def test_turns_hold_their_model_and_tool_calls(factory):
    rec = run(factory)
    root, by_id = tree(rec)
    turns = [s for s in by_id.values() if s["kind"] == "turn"]
    assert turns and all(t["parent_id"] == root["span_id"] for t in turns)        # children of the root
    llm = [s for s in by_id.values() if s["kind"] == "model_call"]
    tools = [s for s in by_id.values() if s["kind"] == "tool_call"]
    assert llm and tools
    for s in llm:
        t = turn_of(s, by_id)
        assert t["parent_id"] == root["span_id"] and t["name"] == f"turn {t['attributes']['turn']}"
    for s in tools:
        assert turn_of(s, by_id)["name"].endswith(" · tools")
    # turn n's tools follow turn n: the batch carries the number of the turn that asked for it
    names = [t["name"] for t in sorted(turns, key=lambda t: t["start_ns"])]
    assert names[:4] == ["turn 1", "turn 1 · tools", "turn 2", "turn 2 · tools"]
    assert len([n for n in names if " · " not in n]) == rec.cost.model_calls


def test_concurrent_calls_of_one_batch_share_their_turn(factory):
    script = [batch(README, LIST), batch(TODO), {"content": "done"}]
    rec = run(factory, "fs-01", "v2-parallel-tools", backend=ScriptedBackend(script))
    _, by_id = tree(rec)
    reads = [s for s in by_id.values() if s["kind"] == "tool_call" and s["name"] in ("fs.read_file", "fs.list_dir")]
    assert len(reads) == 2
    parents = {turn_of(s, by_id)["span_id"] for s in reads}
    assert len(parents) == 1 and by_id[parents.pop()]["name"] == "turn 1 · tools"


@pytest.fixture
def otel(monkeypatch):
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(tracer_mod.WithoutMcpSpans(SimpleSpanProcessor(exporter)))
    monkeypatch.setattr(tracer_mod, "_otel_tracer", provider.get_tracer("harness_factory"))
    yield exporter
    provider.shutdown()


def test_a_budget_stopped_run_names_the_stop_and_closes_its_last_turn(factory, otel):
    base = factory.recipes["v2-base"]
    capped = base.model_copy(update={"config": base.config.model_copy(update={"max_steps": 2})})
    rec = run(factory, "sql-02", capped)                                 # sql-02 needs 3 model calls
    assert any(v.kind == "budget_exceeded" for v in rec.violations)
    finished = otel.get_finished_spans()
    (root,) = [s for s in finished if s.attributes.get("hf.kind") == "run"]
    assert "budget:max_steps" in root.attributes["output.value"]
    turns = [s for s in finished if s.attributes.get("hf.kind") == "turn"]
    assert turns and all(s.end_time for s in turns)
    last = max(turns, key=lambda s: s.start_time)
    assert last.attributes["openinference.span.kind"] == "CHAIN"
    assert last.attributes["output.value"].startswith("stopped: budget:max_steps")
    assert json.loads(last.attributes["input.value"])["turn"] == 3


@pytest.mark.parametrize("recipe", ["v4-planner", "v5-orchestrator"])
def test_steps_and_workers_have_their_own_turns(factory, recipe):
    rec = run(factory, "sql-02", recipe)
    root, by_id = tree(rec)
    for s in by_id.values():
        if s["kind"] in ("model_call", "tool_call") and s["name"] != "delegate.run":
            assert turn_of(s, by_id) is not None, (s["kind"], s["name"])
    if recipe == "v5-orchestrator":
        # a worker numbers its own turns from 1, under its delegate span, which sits in the orchestrator's tree
        workers = [s for s in by_id.values() if s["kind"] == "handoff" and s["name"].startswith("delegate:")]
        assert workers
        for w in workers:
            mine = [s for s in by_id.values() if s["kind"] == "turn" and s["parent_id"] == w["span_id"]]
            assert mine and min(t["attributes"]["turn"] for t in mine) == 1
    else:
        top = sorted((s for s in by_id.values() if s["kind"] == "turn" and s["parent_id"] == root["span_id"]),
                     key=lambda s: s["start_ns"])
        assert top[0]["name"] == "turn 1"                                        # the plan is turn 1


async def test_a_resumed_run_has_the_same_turns(factory, tmp_path):
    task, recipe = factory.tasks.get("sql-02"), factory.recipes["v2-base"]
    plain = await run_one(factory, task, recipe, "scripted/replay", "scripted")
    factory.checkpoint = CheckpointConfig("sqlite", str(tmp_path / "cp.db"))
    resumed = await run_one(factory, task, recipe, "scripted/replay", "scripted", pause=pause_every())

    def shape(rec):
        _, by_id = tree(rec)
        return [(s["name"], by_id[s["parent_id"]]["kind"] if s["parent_id"] else None)
                for s in sorted(by_id.values(), key=lambda s: s["start_ns"])
                if s["kind"] in ("turn", "model_call", "tool_call")]
    assert shape(resumed) == shape(plain) and any(n.startswith("turn") for n, _ in shape(plain))


def test_turn_spans_change_no_number(factory, monkeypatch, p0_tasks):
    """Grading equality: the same scores, statuses and costs with turn spans and without them."""
    with_turns = {t: run(factory, t) for t in p0_tasks}
    from harness_factory.shapes import v2
    monkeypatch.setattr(v2, "model_turn", common._model_turn)
    monkeypatch.setattr(v2, "run_tool_calls", common._run_tool_calls)
    without = {t: run(factory, t) for t in p0_tasks}
    for t in p0_tasks:
        a, b = with_turns[t], without[t]
        assert not [s for s in spans(b) if s["kind"] == "turn"]
        assert (a.status, a.scores, a.composite) == (b.status, b.scores, b.composite), t
        for field in ("model_calls", "failed_calls", "tool_calls", "tokens_in", "tokens_out", "usd"):
            assert getattr(a.cost, field) == getattr(b.cost, field), (t, field)
        assert [(v.kind, v.detail) for v in a.violations] == [(v.kind, v.detail) for v in b.violations]
