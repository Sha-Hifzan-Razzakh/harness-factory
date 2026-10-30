"""Parallel campaigns and the shared quota ledger."""

import asyncio
import itertools
import json
from pathlib import Path

import pytest

from harness_factory.providers.quota import QuotaLedger
from harness_factory.runner.runner import RunSpec, plan, run_campaign
from harness_factory.substrate.errors import ErrorKind, HarnessError
from harness_factory.tasks.reference import PlanBackend


class FakeTime:
    """A clock that only moves when someone sleeps, so pacing tests take no real time."""

    def __init__(self):
        self.now = 1_000_000.0
        self.slept: list[float] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, s: float) -> None:
        self.slept.append(s)
        self.now += s
        await asyncio.sleep(0)


def limited(factory, **limits):
    """The factory's catalog with the scripted provider's limits replaced, and a fresh ledger on fake time."""
    providers = factory.providers.model_copy(deep=True)
    providers.get("scripted").limits = providers.get("scripted").limits.model_copy(update=limits)
    t = FakeTime()
    factory.providers = providers
    factory.ledger = QuotaLedger(providers, clock=t.clock, sleep=t.sleep, jitter_s=0)
    return t


# ------------------------------------------------------------- ledger

async def test_minute_limit_waits_for_the_window_to_roll(factory):
    t = limited(factory, rpm=2)
    for _ in range(5):
        await factory.ledger.acquire("scripted")
    assert t.slept == [60.0, 60.0]                       # 2 + 2 + 1 across three windows
    assert factory.ledger.waits["scripted"] == 120.0


async def test_token_limit_paces_too(factory):
    t = limited(factory, tpm=1000)
    await factory.ledger.acquire("scripted")
    factory.ledger.record_tokens("scripted", 1000)
    await factory.ledger.acquire("scripted")
    assert t.slept == [60.0]


async def test_daily_limit_fails_fast_instead_of_waiting(factory):
    t = limited(factory, rpd=2)
    await factory.ledger.acquire("scripted")
    await factory.ledger.acquire("scripted")
    with pytest.raises(HarnessError) as e:
        await factory.ledger.acquire("scripted")
    assert e.value.kind == ErrorKind.QUOTA_EXHAUSTED and t.slept == []


async def test_concurrent_workers_never_overbook_a_window(factory):
    t = limited(factory, rpm=3)
    stamps: list[float] = []

    async def worker():
        await factory.ledger.acquire("scripted")
        stamps.append(t.now)

    await asyncio.gather(*(worker() for _ in range(10)))
    per_window: dict[int, int] = {}
    for s in stamps:
        per_window[int((s - 1_000_000.0) // 60)] = per_window.get(int((s - 1_000_000.0) // 60), 0) + 1
    assert len(stamps) == 10 and max(per_window.values()) <= 3


# ------------------------------------------------------------- campaigns

def test_plan_order_is_fixed(factory):
    specs = plan(factory, ["v2-base", "v3-staged"], "scripted", ["scripted/replay"], seeds=2, task_ids=["fs-01"])
    assert [(s.recipe_id, s.seed) for s in specs] == [("v2-base", 0), ("v2-base", 1), ("v3-staged", 0),
                                                       ("v3-staged", 1)]


async def test_parallel_and_serial_campaigns_agree(factory):
    specs = plan(factory, ["v2-base", "v3-staged"], "scripted", ["scripted/replay", "scripted/noisy"],
                 task_ids=["fs-02", "sql-01"])
    serial = await run_campaign(factory, specs, concurrency=1)
    parallel = await run_campaign(factory, specs, concurrency=6)
    key = lambda r: (r.run_key.as_str(), r.status, r.composite, r.cost.tool_calls)
    assert [key(r) for r in serial.records] == [key(r) for r in parallel.records]   # same order, same results
    assert not serial.errors and not parallel.errors


async def test_runs_actually_overlap(factory, monkeypatch):
    """With model latency, runs must be in flight together (the point of concurrency for real providers)."""
    original = PlanBackend.complete

    async def slow(self, *a, **k):
        await asyncio.sleep(0.4)
        return await original(self, *a, **k)

    monkeypatch.setattr(PlanBackend, "complete", slow)
    specs = plan(factory, "v2-base", "scripted", ["scripted/replay"], seeds=4, task_ids=["fs-01"])
    result = await run_campaign(factory, specs, concurrency=4)
    spans = [next(json.loads(line) for line in Path(r.trace_path).read_text().splitlines()
                  if json.loads(line)["kind"] == "run") for r in result.records]
    intervals = sorted((s["start_ns"], s["end_ns"]) for s in spans)
    overlaps = sum(1 for a, b in itertools.pairwise(intervals) if b[0] < a[1])
    assert overlaps >= 2


async def test_daily_quota_ends_runs_incomplete_not_failed(factory):
    limited(factory, rpd=3)                                   # fs-01 replay needs 2 model calls per run
    specs = plan(factory, "v2-base", "scripted", ["scripted/replay"], seeds=3, task_ids=["fs-01"])
    result = await run_campaign(factory, specs, concurrency=1)
    assert [r.status.value for r in result.records] == ["SUCCESS", "INCOMPLETE", "INCOMPLETE"]
    assert sum(r.cost.model_calls for r in result.records) == 3      # never more than the provider allows


async def test_minute_pacing_inside_a_parallel_campaign(factory):
    limited(factory, rpm=2)
    specs = plan(factory, "v2-base", "scripted", ["scripted/replay"], seeds=3, task_ids=["fs-01"])
    result = await run_campaign(factory, specs, concurrency=3)        # 6 calls at 2 per minute
    assert all(r.status.value == "SUCCESS" for r in result.records)
    assert result.quota_wait_s["scripted"] >= 120.0
    waits = [s for r in result.records for s in map(json.loads, Path(r.trace_path).read_text().splitlines())
             if s["kind"] == "model_call" and "quota_wait_s" in s["attributes"]]
    assert waits                                                       # pacing is visible in the trace


async def test_a_failing_run_does_not_stop_the_campaign(factory):
    specs = [RunSpec(task_id="fs-01", recipe_id="v2-base", canonical_model="scripted/replay", provider="scripted"),
             RunSpec(task_id="fs-01", recipe_id="v2-base", canonical_model="no-such-model", provider="scripted"),
             RunSpec(task_id="sql-01", recipe_id="v2-base", canonical_model="scripted/replay", provider="scripted")]
    result = await run_campaign(factory, specs, concurrency=3)
    assert [r.run_key.task_version for r in result.records] == ["fs-01@1", "sql-01@1"]
    assert len(result.errors) == 1 and "no-such-model" in result.errors[0].error


async def test_token_reservations_stop_a_burst_overshooting_tpm(factory):
    t = limited(factory, tpm=1000)
    stamps: list[float] = []

    async def worker():
        await factory.ledger.acquire("scripted", tokens=400)           # each call expects ~400 tokens
        stamps.append(t.now)

    await asyncio.gather(*(worker() for _ in range(5)))
    first_window = [s for s in stamps if s < 1_000_000.0 + 60]
    assert len(first_window) == 2                                       # 2 x 400 fits in 1000; a third would not


async def test_reservation_is_corrected_by_actual_usage(factory):
    limited(factory, tpm=1000)
    await factory.ledger.acquire("scripted", tokens=900)
    factory.ledger.record_tokens("scripted", 100, reserved=900)         # the call actually used 100
    assert factory.ledger.remaining("scripted")["tpm"] == 900
