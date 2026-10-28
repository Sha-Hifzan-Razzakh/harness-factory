"""Campaign resume and the persistent quota ledger."""

import asyncio

from harness_factory.providers.quota import DAY, QuotaLedger
from harness_factory.provision.provisioner import run_key_for
from harness_factory.runner.runner import plan, plan_status, run_campaign, run_one


class Clock:
    def __init__(self, now: float):
        self.now = now

    def __call__(self) -> float:
        return self.now


def ledger(factory, path, clock, **limits) -> QuotaLedger:
    providers = factory.providers.model_copy(deep=True)
    providers.get("scripted").limits = providers.get("scripted").limits.model_copy(update=limits)
    factory.providers = providers
    return QuotaLedger(providers, clock=clock, jitter_s=0, state_path=path)


# ------------------------------------------------------------- run identity before running

async def test_predicted_run_key_matches_the_real_one(factory):
    task, recipe = factory.tasks.get("sql-02"), factory.recipes["v3-staged"]
    predicted = run_key_for(task, recipe, "scripted/noisy", "scripted", 4,
                            declared=factory.declared, providers=factory.providers)
    rec = await run_one(factory, task, recipe, "scripted/noisy", "scripted", seed=4)
    assert predicted == rec.run_key


# ------------------------------------------------------------- resume

async def test_resume_skips_what_is_done(factory):
    specs = plan(factory, ["v2-base", "v3-staged"], "scripted", ["scripted/replay"], task_ids=["fs-01", "sql-01"])
    first = await run_campaign(factory, specs[:2], concurrency=2)            # v2 runs only
    assert len(first.records) == 2
    status = plan_status(factory, specs)
    assert len(status.done) == 2 and len(status.new) == 2 and not status.retry
    second = await run_campaign(factory, specs, concurrency=2, resume=True)
    assert len(second.skipped) == 2 and len(second.records) == 2 and second.reruns == 0
    third = await run_campaign(factory, specs, concurrency=2, resume=True)
    assert len(third.skipped) == 4 and third.records == []


async def test_quota_interrupted_runs_are_finished_tomorrow(factory, tmp_path):
    """Day 1 runs out of quota part-way: those runs are INCOMPLETE. Day 2, --resume reruns exactly those."""
    clock = Clock(10 * DAY + 3600)
    factory.ledger = ledger(factory, tmp_path / "q.json", clock, rpd=4)        # fs-01 replay = 2 calls per run
    specs = plan(factory, "v2-base", "scripted", ["scripted/replay"], seeds=4, task_ids=["fs-01"])
    day1 = await run_campaign(factory, specs, concurrency=1)
    assert [r.status.value for r in day1.records] == ["SUCCESS", "SUCCESS", "INCOMPLETE", "INCOMPLETE"]

    clock.now += DAY                                                          # next UTC day: quota resets
    factory.ledger = ledger(factory, tmp_path / "q.json", clock, rpd=4)        # a fresh process reads the file
    day2 = await run_campaign(factory, specs, concurrency=1, resume=True)
    assert len(day2.skipped) == 2 and day2.reruns == 2
    assert [r.status.value for r in day2.records] == ["SUCCESS", "SUCCESS"]

    latest = factory.store.rows()
    assert len(latest) == 4 and {r["status"] for r in latest} == {"SUCCESS"}  # reruns superseded INCOMPLETE rows
    assert len(factory.store.rows(latest=False)) == 6                         # history is kept
    _, cells = factory.store.aggregate()
    assert cells[0].e.n == 4 and cells[0].excluded == 0


async def test_an_interrupted_campaign_resumes_to_the_same_result(factory):
    specs = plan(factory, "v2-base", "scripted", ["scripted/replay"], seeds=2,
                 task_ids=["fs-01", "fs-02", "mix-01", "sql-01", "sql-02"])             # 10 runs
    job = asyncio.ensure_future(run_campaign(factory, specs, concurrency=2))
    while len(factory.store.rows()) < 3:                                       # let a few finish, then Ctrl-C
        await asyncio.sleep(0.05)
    job.cancel()
    try:
        await job
    except asyncio.CancelledError:
        pass
    done_before = len(factory.store.rows())
    assert 3 <= done_before < 10

    resumed = await run_campaign(factory, specs, concurrency=2, resume=True)
    assert len(resumed.skipped) == done_before and len(resumed.records) == 10 - done_before
    latest = factory.store.rows()
    assert len(latest) == 10 and {r["status"] for r in latest} == {"SUCCESS"}


async def test_rerunning_without_resume_supersedes_rather_than_double_counts(factory):
    specs = plan(factory, "v2-base", "scripted", ["scripted/replay"], task_ids=["fs-01"])
    await run_campaign(factory, specs)
    await run_campaign(factory, specs)
    assert len(factory.store.rows(latest=False)) == 2 and len(factory.store.rows()) == 1
    _, cells = factory.store.aggregate()
    assert cells[0].e.n == 1


# ------------------------------------------------------------- persistent ledger

async def test_ledger_usage_survives_a_restart(factory, tmp_path):
    clock = Clock(5 * DAY + 100)
    a = ledger(factory, tmp_path / "q.json", clock, rpd=10)
    for _ in range(3):
        await a.acquire("scripted", tokens=50)
    b = ledger(factory, tmp_path / "q.json", clock, rpd=10)                    # new process
    assert b.remaining("scripted")["rpd"] == 7


async def test_provider_said_spent_survives_a_restart_until_utc_midnight(factory, tmp_path):
    clock = Clock(5 * DAY + 100)
    a = ledger(factory, tmp_path / "q.json", clock)
    a.mark_exhausted("scripted")
    assert ledger(factory, tmp_path / "q.json", clock).exhausted("scripted")
    clock.now = 6 * DAY - 1                                                   # one second before UTC midnight
    assert ledger(factory, tmp_path / "q.json", clock).exhausted("scripted")
    clock.now = 6 * DAY + 1                                                   # new UTC day
    assert not ledger(factory, tmp_path / "q.json", clock).exhausted("scripted")


def test_day_windows_align_to_utc_midnight(factory, tmp_path):
    clock = Clock(7 * DAY - 10)
    led = ledger(factory, None, clock, rpd=5)
    led.charge("scripted", 5)
    assert led.exhausted("scripted")
    clock.now = 7 * DAY + 10                                                  # 20 s later, but a new UTC day
    assert not led.exhausted("scripted")


def test_a_corrupt_ledger_file_starts_clean(factory, tmp_path):
    path = tmp_path / "q.json"
    path.write_text("{not json")
    led = ledger(factory, path, Clock(DAY), rpd=5)
    assert led.remaining("scripted")["rpd"] == 5
    led.charge("scripted", 1)
    assert ledger(factory, path, Clock(DAY), rpd=5).remaining("scripted")["rpd"] == 4   # rewritten cleanly
