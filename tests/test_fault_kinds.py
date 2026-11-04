"""Fault kinds `latency` and `malformed` (ENV-03): a real delay charged to the run's wall time, and results that
parse but are wrong in shape. Offline: LocalTools for the executor, the scripted provider for whole runs."""

import asyncio
import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from harness_factory.env.faults import FaultConfig, FaultInjector, _draw, load_fault_profiles, malform
from harness_factory.env.mcp.servers import SideEffect, ToolSpec
from harness_factory.hashing import content_hash
from harness_factory.mcp.client import ToolResult
from harness_factory.observability.span import SpanKind
from harness_factory.observability.tracer import Tracer
from harness_factory.provision.provisioner import provision
from harness_factory.recipes.model import PolicySet, RuntimeConfig
from harness_factory.results.cost import CostBaseline
from harness_factory.results.store import aggregate_rows, record_row
from harness_factory.runner.runner import plan, run_campaign, run_one
from harness_factory.substrate.budget import BudgetGuard
from harness_factory.substrate.errors import ErrorKind
from harness_factory.substrate.tools import LocalTool, ToolExecutor

FAULTS_DIR = Path(__file__).resolve().parents[1] / "catalog" / "faults"
WORDS = ("inject", "fault", "malformed", "simulat")


def cfg(*rules) -> FaultConfig:
    return FaultConfig(id="t", rules=list(rules))


# ------------------------------------------------------------- rules and identity

def test_latency_rules_need_a_valid_delay_and_other_kinds_refuse_one():
    with pytest.raises(ValidationError, match="delay_s"):
        cfg({"kind": "latency"})
    with pytest.raises(ValidationError, match="delay_s"):
        cfg({"kind": "error", "delay_s": 1})
    with pytest.raises(ValidationError, match="min <= max"):
        cfg({"kind": "latency", "delay_s": [2, 1]})
    with pytest.raises(ValidationError, match=">= 0"):
        cfg({"kind": "latency", "delay_s": -1})
    assert cfg({"kind": "latency", "delay_s": [0.1, 0.5]}).rules[0].delay_s == (0.1, 0.5)


def test_existing_profiles_hash_as_before_the_new_field():
    """delay_s is left out of the hash when unset, so the profiles from before it keep their RunKeys."""
    profiles = load_fault_profiles(FAULTS_DIR)
    for fid in ("first-call-fails", "transient", "garbled-reads"):
        p = profiles[fid]
        old = {"id": p.id, "rules": [r.model_dump(mode="json", exclude={"delay_s"}) for r in p.rules]}
        assert p.content_hash() == content_hash(old, 12), fid
    assert {"slow-tools", "malformed-results"} <= set(profiles)
    slow = profiles["slow-tools"]
    assert all(r.kind == "latency" for r in slow.rules) and slow.content_hash() != cfg(
        {"kind": "latency", "delay_s": 1}).content_hash()


def test_existing_kinds_fire_exactly_as_before():
    """fire() keeps the old decision for every existing profile: same draw, same rules, same order."""
    def old_decide(c: FaultConfig, seed: int, calls: list[str]) -> list[str | None]:
        seen, fired, out = {}, {}, []
        for ref in calls:
            seen[ref] = n = seen.get(ref, 0) + 1
            kind = None
            for i, rule in enumerate(c.rules):
                if not rule.matches(ref) or (rule.nth is not None and n not in rule.nth):
                    continue
                if rule.max_per_run is not None and fired.get(i, 0) >= rule.max_per_run:
                    continue
                if _draw(c.content_hash(), seed, ref, n, i) < rule.rate:
                    fired[i] = fired.get(i, 0) + 1
                    kind = rule.kind
                    break
            out.append(kind)
        return out

    calls = ["fs.read_file", "fs.write_file", "sql.query", "search.grep"] * 15
    for fid in ("first-call-fails", "transient", "garbled-reads"):
        c = load_fault_profiles(FAULTS_DIR)[fid]
        for seed in (0, 1, 7):
            inj = FaultInjector(c, seed)
            assert [inj.decide(r) for r in calls] == old_decide(c, seed, calls), (fid, seed)


def test_delays_are_deterministic_per_seed_and_in_range():
    c = cfg({"kind": "latency", "delay_s": [0.2, 2.0]})

    def delays(seed):
        inj = FaultInjector(c, seed)
        return [inj.fire("fs.read_file").delay_s for _ in range(20)]

    assert delays(3) == delays(3)                                   # same seed: same delays, no wall-clock random
    assert delays(3) != delays(4)
    assert all(0.2 <= d <= 2.0 for d in delays(3)) and len(set(delays(3))) > 10
    fixed = FaultInjector(cfg({"kind": "latency", "delay_s": 0.5}), 0)
    assert {fixed.fire("x.y").delay_s for _ in range(5)} == {0.5}
    assert FaultInjector(cfg({"kind": "error"}), 0).fire("x.y").delay_s is None


def test_malformed_skips_calls_that_write():
    inj = FaultInjector(cfg({"kind": "malformed", "max_per_run": 1}), 0)
    assert inj.fire("fs.write_file", reads=False) is None           # never fires on a write, nor uses up the cap
    f = inj.fire("fs.read_file")
    assert f is not None and f.kind == "malformed" and f.call == 1
    assert inj.fire("fs.read_file") is None                         # max_per_run 1


# ------------------------------------------------------------- malformed results

SAMPLES = {
    "json-object": ('{\n  "columns": [\n    "id"\n  ],\n  "rows": [\n    [\n      1\n    ]\n  ]\n}', "json"),
    "json-array": ('[\n  {\n    "name": "id",\n    "primary_key": true\n  }\n]', "json"),
    "json-stream": ('{\n  "name": "id",\n  "type": "INTEGER"\n}\n{\n  "name": "city",\n  "type": "TEXT"\n}', "jsonl"),
    "json-scalar": ('"hello"', "json"),
    "delimited": ("id,name,city\n1,Al Noor,Dubai\n2,Harbor,Abu Dhabi\n", "csv"),
    "key-value": ("[server]\nhost = 0.0.0.0\nport = 8080\n\n[logging]\nlevel = info\n", "kv"),
    "lines": ("config/\nnotes/", "lines"),
    "one line": ("hello world", "text"),
}


def _parses(text: str, form: str) -> None:
    if form == "json" or (form == "jsonl" and text.lstrip().startswith("{\n  \"items\"")):
        json.loads(text)
    elif form == "jsonl":
        dec, i = json.JSONDecoder(), 0
        while i < len(text.rstrip()):
            _, i = dec.raw_decode(text, i)
            i += len(text[i:]) - len(text[i:].lstrip())
    elif form == "csv":
        assert len({line.count(",") for line in text.splitlines() if line}) == 1
    elif form == "kv":
        assert all("=" in line for line in text.splitlines() if line and not line.startswith("["))


@pytest.mark.parametrize("name", list(SAMPLES))
def test_malformed_results_still_parse_and_are_deterministic(name):
    text, form = SAMPLES[name]
    for draw in (0.05, 0.4, 0.95):
        out = malform(text, draw)
        assert out is not None and out[0] != text, (name, draw)
        assert out == malform(text, draw)                            # same draw, same reshaping
        assert not any(w in out[0].lower() for w in WORDS)
        _parses(out[0], form)


def test_malformed_json_forms():
    query = '{"columns": ["id", "name"], "rows": [[1, "A"]]}'
    forms = {malform(query, d)[1] for d in (i / 30 for i in range(30))}
    assert forms == {"json-object: wrapped in data", "json-object: renamed columns", "json-object: renamed rows",
                     "json-object: dropped columns", "json-object: dropped rows"}
    wrapped = json.loads(malform('["a", "b"]', 0.0)[0])
    assert wrapped == {"items": ["a", "b"], "count": 2}              # a list wrapped in an object
    renamed = json.loads(malform('[{"primary_key": 1}, {"primary_key": 0}]', 0.99)[0])
    assert renamed == [{"primaryKey": 1}, {"primaryKey": 0}]         # snake_case -> camelCase, every element


def test_text_forms_shift_columns_and_values():
    csv_out = malform("id,name\n1,A\n2,B", 0.5)[0]
    assert csv_out == "id,name\nA,1\nB,2"                            # header kept, values under the wrong headers
    kv_out = malform("host = h\nport = 8080", 0.5)[0]
    assert kv_out == "host = 8080\nport = h"
    assert malform("notes/a.txt:1: x\nnotes/b.txt:4: y", 0.5)[0] == "notes/a.txt:4: y\nnotes/b.txt:1: x"


@pytest.mark.parametrize("text", ["", "word", "42", "true", "a\na", "[]"])
def test_nothing_to_reshape(text):
    out = malform(text, 0.5)
    assert out is None or (text == "[]" and json.loads(out[0]) == {"items": [], "count": 0})


# ------------------------------------------------------------- in the ToolExecutor (LocalTool, no MCP)

def _executor(rule: dict, *, reply: ToolResult | None = None, timeout_s: float = 10.0, side_effect=SideEffect.READ,
              wall_time_s: int = 600, excluded=lambda: 0.0):
    calls: list[dict] = []

    async def call(args):
        calls.append(args)
        return reply or ToolResult(text="host = h\nport = 8080")

    spec = ToolSpec(name="probe", side_effect=side_effect, risk="R0", timeout_s=timeout_s)
    tool = LocalTool(spec=spec, description="probe", input_schema={"type": "object"}, call=call)
    budget = BudgetGuard(RuntimeConfig(wall_time_s=wall_time_s), excluded_s=excluded)
    budget.start()
    tracer = Tracer("t")
    ex = ToolExecutor(SimpleNamespace(catalog=None), SimpleNamespace(), {"loc.probe"}, PolicySet(), budget, tracer,
                      "rk", faults=FaultInjector(FaultConfig(id="t", rules=[rule]), 0), local={"loc.probe": tool})
    return ex, calls, tracer


def _span(tracer):
    return next(s for s in tracer.spans if s.kind == SpanKind.tool_call)


async def test_latency_delays_the_call_and_is_charged_to_wall_time():
    infra = {"s": 0.0}
    ex, calls, tracer = _executor({"kind": "latency", "delay_s": 0.15}, excluded=lambda: infra["s"])
    t0 = time.monotonic()
    res = await ex.execute("loc__probe", {})
    assert time.monotonic() - t0 >= 0.15 and calls == [{}]          # a real delay; then the tool really ran
    assert not res.is_error and res.text == "host = h\nport = 8080"
    assert ex.budget.elapsed_s() >= 0.15 and infra["s"] == 0.0      # charged, never excluded as infra wait
    span = _span(tracer)
    assert span.attributes["fault"] == "latency" and span.attributes["fault_delay_s"] == 0.15
    assert span.duration_ms() >= 150


async def test_latency_can_end_the_run_on_wall_time(factory):
    """Through the real provisioner: excluded_s is the gateway's infra wait, and a slow tool isn't part of it."""
    factory.faults = {**factory.faults, "t": cfg({"tools": ["fs.read_file"], "kind": "latency", "delay_s": 0.12})}
    async with provision(factory.tasks.get("fs-02"), factory.recipes["v2-base"], "scripted/replay", "scripted",
                         declared=factory.declared, providers=factory.providers, ledger=factory.ledger,
                         fault=factory.fault("t")) as p:
        inst = p.instance
        inst.budget.start()
        await inst.tools.execute("fs__read_file", {"path": "config/app.ini"})
        assert inst.gateway.infra_wait_s == 0.0 and inst.budget.elapsed_s() >= 0.12
        inst.budget.limits = inst.budget.limits.model_copy(update={"wall_time_s": 0.1})
        assert inst.budget.exceeded_by("tool_call") == "wall_time_s 0.1"


async def test_latency_past_the_tool_timeout_is_a_real_timeout():
    ex, calls, tracer = _executor({"kind": "latency", "delay_s": 30}, timeout_s=0.05)
    t0 = time.monotonic()
    res = await ex.execute("loc__probe", {})
    assert time.monotonic() - t0 < 5 and calls == []                 # waited timeout_s, not 30 s; nothing ran
    assert res.is_error and res.text == "loc.probe timed out after 0.05s"   # the same message as a real timeout
    span = _span(tracer)
    assert span.error == ErrorKind.TOOL_TIMEOUT.value and span.attributes["fault"] == "latency"   # not Q's


async def test_cancel_during_a_latency_fault():
    ex, calls, _ = _executor({"kind": "latency", "delay_s": 30})
    task = asyncio.create_task(ex.execute("loc__probe", {}))
    await asyncio.sleep(0.05)
    t0 = time.monotonic()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert time.monotonic() - t0 < 1 and calls == []                 # stopped at once; the tool never ran
    assert ex.budget.used_tool_calls == 0


async def test_latency_keeps_the_calls_own_error_as_the_models():
    ex, calls, tracer = _executor({"kind": "latency", "delay_s": 0.01},
                                  reply=ToolResult(text="no such file: x", is_error=True))
    res = await ex.execute("loc__probe", {})
    span = _span(tracer)
    assert res.is_error and calls == [{}]
    assert "fault" not in span.attributes and span.attributes["fault_inert"] == "latency"   # Q counts it


async def test_malformed_result_reaches_the_model_as_a_normal_result():
    ex, _, tracer = _executor({"kind": "malformed"})
    res = await ex.execute("loc__probe", {})
    assert not res.is_error and res.text == "host = 8080\nport = h" and res.structured is None
    span = _span(tracer)
    assert span.attributes["fault"] == "malformed" and span.attributes["malformed"] == "key-value: values shifted"
    assert span.status != "error"


async def test_malformed_leaves_errors_and_writes_alone():
    ex, _, tracer = _executor({"kind": "malformed"}, reply=ToolResult(text="bad path: x y", is_error=True))
    res = await ex.execute("loc__probe", {})
    assert res.text == "bad path: x y" and _span(tracer).attributes["fault_inert"] == "malformed"
    ex, _, tracer = _executor({"kind": "malformed"}, side_effect=SideEffect.REVERSIBLE)
    res = await ex.execute("loc__probe", {})
    assert res.text == "host = h\nport = 8080" and "fault" not in _span(tracer).attributes
    assert ex.faults.log == []


# ------------------------------------------------------------- whole runs

async def test_malformed_run_is_deterministic_and_never_announced(factory):
    factory.faults = {**factory.faults, "t": cfg({"tools": ["sql.query"], "kind": "malformed", "nth": [1]})}
    replies = []
    for _ in range(2):
        rec = await run_one(factory, factory.tasks.get("mix-01"), factory.recipes["v2-base"], "scripted/replay",
                            "scripted", fault="t")
        replies.append(next(m for m in rec.messages if m["role"] == "tool")["content"])
        spans = [s for s in map(json.loads, Path(rec.trace_path).read_text().splitlines())
                 if s["kind"] == SpanKind.tool_call.value and s["name"] == "sql.query"]
        assert spans[0]["attributes"]["fault"] == "malformed" and spans[0]["status"] != "error"
        assert rec.scores.trajectory == 1.0
    assert replies[0] == replies[1]                                  # same RunKey, same reshaping
    data = json.loads(replies[0])                                    # still parses
    assert data != {"columns": ["id"], "rows": []} and not any(w in replies[0].lower() for w in WORDS)
    clean = await run_one(factory, factory.tasks.get("mix-01"), factory.recipes["v2-base"], "scripted/replay",
                          "scripted")
    assert next(m for m in clean.messages if m["role"] == "tool")["content"] != replies[0]


async def test_r_treats_latency_and_malformed_runs_as_faulted(factory):
    profiles = load_fault_profiles(FAULTS_DIR)
    fast = profiles["slow-tools"].model_copy(update={"rules": [profiles["slow-tools"].rules[0].model_copy(
        update={"rate": 1.0, "delay_s": 0.01})]})                     # same id, CI-sized delays
    factory.faults = {**factory.faults, "slow-tools": fast, "malformed-results": profiles["malformed-results"]}
    specs = plan(factory, "v2-base", "scripted", ["scripted/retry"], task_ids=["fs-02"],
                 faults=[None, "slow-tools", "malformed-results"])
    result = await run_campaign(factory, specs, concurrency=3)
    assert sorted(r.fault or "" for r in result.records) == ["", "malformed-results", "slow-tools"]
    rows = [record_row(r) for r in result.records]
    baseline = CostBaseline.from_rows(rows)
    assert sum(baseline.counts.values()) == 1                        # cost medians from the clean run only
    (cell,) = aggregate_rows(rows, baseline)                         # one task, recipe, model: one cell
    assert cell.e.n == 1 and cell.n_faulted == 2 and cell.robustness is not None
