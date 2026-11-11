"""Progress signals (#114): per cell, what runs did on the way, read from stored runs and traces. They separate
recipes when every run scores E = 0, and change no score."""

from __future__ import annotations

import asyncio
import json

from harness_factory.results import signals as sig
from harness_factory.results.cost import CostBaseline
from harness_factory.results.report import build_report
from harness_factory.runner.runner import run_one
from harness_factory.substrate.gateway import ScriptedBackend
from harness_factory.tasks.model import Task

README = {"tool_calls": [{"name": "fs__read_file", "arguments": {"path": "notes/README.txt"}}]}
LIST = {"tool_calls": [{"name": "fs__list_dir", "arguments": {"path": "notes"}}]}
WRONG = {"tool_calls": [{"name": "fs__write_file", "arguments": {"path": "notes/todo.txt", "content": "buy eggs"}}]}
DONE = {"content": "done"}


def run(factory, recipe, script, seed=0):
    return asyncio.run(run_one(factory, factory.tasks.get("fs-01"), factory.recipes[recipe], "scripted/replay",
                               "scripted", seed, backend=ScriptedBackend(script)))


def test_signals_separate_two_recipes_when_every_run_scores_zero(factory):
    # v2-base only looks around; v2-parallel-tools writes the output file, with the wrong text. Both score 0.
    reads = [run(factory, "v2-base", [README, LIST, DONE], seed) for seed in (0, 1)]
    writes = [run(factory, "v2-parallel-tools", [README, WRONG, DONE], seed) for seed in (0, 1)]
    assert all(r.composite == 0 for r in reads + writes)

    rows = factory.store.rows()
    table = {r[0]: r for r in sig.cell_rows(factory, [r for r in rows if r["status"] not in ("INCOMPLETE", "ERROR")])}
    base, par = table["`v2-base`"], table["`v2-parallel-tools`"]
    col = {name: i for i, name in enumerate(sig.HEADER)}
    assert base[col["runs"]] == par[col["runs"]] == "2"
    assert base[col["wrote output"]] == "0/2" and par[col["wrote output"]] == "2/2"
    assert base[col["write"]] == "0/2" and par[col["write"]] == "2/2"
    assert base[col["read"]] == par[col["read"]] == "2/2"
    assert base[col["first write (median)"]] == "-" and par[col["first write (median)"]] == "2"
    assert base[col["tool calls (median)"]] == par[col["tool calls (median)"]] == "2"
    assert base[col["engine"]] == "0/2" and base[col["schema valid"]] == "-"       # fs only; no json_schema check

    text = build_report(factory, rows, CostBaseline.from_rows(rows), signals=True, with_selector=False)
    assert "## 2b. Progress signals" in text and "| `v2-parallel-tools` | `scripted/replay` | 2 | 2/2 |" in text
    assert "## 2b." not in build_report(factory, rows, CostBaseline.from_rows(rows), with_selector=False)


def test_signals_change_no_score(factory):
    rec = run(factory, "v2-base", [README, WRONG, DONE])
    before = factory.store.rows()
    build_report(factory, before, CostBaseline.from_rows(before), signals=True, with_selector=False)
    after = factory.store.rows()
    assert [(r["run_key"], r["composite"], r["status"]) for r in after] == \
        [(r["run_key"], r["composite"], r["status"]) for r in before]
    assert rec.composite == after[0]["composite"]


def task_with_schema() -> Task:
    return Task.model_validate({
        "id": "sig-01", "domain": "data", "instruction": "Write out.json.", "allowed_tools": ["fs.write_file"],
        "oracle": {"checks": [
            {"id": "shape", "kind": "json_schema", "path": "out.json", "weight": 1,
             "json_schema": {"type": "object", "required": ["n"]}},
            {"id": "guard", "kind": "file_contains", "path": "notes/README.txt", "text": "x", "weight": 0,
             "critical": True}]}})


def span(name, i, effect=None, args=None, status="ok"):
    attrs = {k: v for k, v in (("side_effect", effect), ("args", args)) if v is not None}
    return {"kind": "tool_call", "name": name, "start_ns": i, "status": status, "attributes": attrs}


def write_trace(tmp_path, spans) -> str:
    p = tmp_path / "t.jsonl"
    p.write_text("\n".join(json.dumps(s) for s in [{"kind": "run", "name": "r", "start_ns": 0}, *spans]))
    return str(p)


def test_run_signals_from_a_trace(tmp_path):
    task = task_with_schema()
    assert sig.output_paths(task) == {"out.json"}                       # the guard's file is not an output
    trace = write_trace(tmp_path, [
        span("audit.evaluate", 1, "READ", '{"student": "1"}'),
        span("fs.write_file", 2, "REVERSIBLE", '{"path": "scratch.txt", "content": "x"}'),
        span("fs.write_file", 3, "REVERSIBLE", '{"path": "./out.json", "content": "{\\"n\\": 1, "'),   # cut args
        span("fs.write_file", 4, "REVERSIBLE", '{"path": "late.txt"}', status="error"),
        span("spill.read", 5, "READ", "{}"),
    ])
    row = {"trace_path": trace, "grades": [{"grader_id": "state_diff", "checks": [{"id": "shape", "passed": True},
                                                                                  {"id": "guard", "passed": True}]}]}
    s = sig.run_signals(row, task)
    assert s == sig.RunSignals(wrote_output=True, read=True, write=True, engine=True, first_write=2, tool_calls=5,
                               schema_valid=True)
    row["grades"][0]["checks"][0]["passed"] = False
    assert sig.run_signals(row, task).schema_valid is False
    assert sig.run_signals({"trace_path": str(tmp_path / "missing.jsonl")}, task) is None


def test_a_failed_or_harness_call_is_no_signal(tmp_path):
    task = task_with_schema()
    trace = write_trace(tmp_path, [span("fs.write_file", 1, "REVERSIBLE", '{"path": "out.json"}', status="error"),
                                   span("delegate.run", 2, "READ", "{}")])
    s = sig.run_signals({"trace_path": trace, "grades": []}, task)
    assert (s.wrote_output, s.write, s.engine, s.read, s.first_write, s.tool_calls) == (False, False, False, False,
                                                                                       None, 2)
    assert s.schema_valid is False                                       # no result for the schema check
