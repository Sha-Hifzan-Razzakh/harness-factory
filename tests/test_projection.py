"""Campaign projection and preflight (MOD-05)."""

import pytest
from typer.testing import CliRunner

from harness_factory.runner.projection import SHAPE_DEFAULTS, Estimator, project
from harness_factory.runner.runner import RunSpec, plan, run_campaign


def spec(task, recipe="v2-base", model="gpt-oss-120b", provider="groq"):
    return RunSpec(task_id=task, recipe_id=recipe, canonical_model=model, provider=provider)


async def test_estimates_come_from_the_most_specific_history(factory, p0_tasks):
    await run_campaign(factory, plan(factory, "v2-base", "scripted", ["scripted/replay"], task_ids=["fs-01", "fs-02"]))
    rows = factory.store.rows()
    est = Estimator(rows, {"scripted"})
    v2, v3 = factory.recipes["v2-base"], factory.recipes["v3-staged"]
    fs01 = next(r for r in rows if r["task_version"] == "fs-01@1")
    e = est.estimate("scripted/replay", "scripted", v2, "fs-01@1")
    assert e.source == "task" and e.tokens == fs01["tokens_in"] + fs01["tokens_out"]
    assert est.estimate("scripted/replay", "scripted", v2, "sql-02@1").source == "recipe"
    assert est.estimate("scripted/noisy", "scripted", v2, "sql-02@1").source == "recipe (any model)"
    real = est.estimate("gpt-oss-120b", "groq", v2, "fs-01@1")                  # scripted never stands in
    assert real.source == "default" and (real.calls, real.tokens) == SHAPE_DEFAULTS[v2.variant]
    assert est.estimate("scripted/replay", "scripted", v3, "fs-01@1").tokens == SHAPE_DEFAULTS[v3.variant][1]


def test_days_and_runs_today_follow_the_bucket_limits(factory):
    specs = [spec(t) for t in ("fs-01", "fs-02", "sql-01")]                     # 3 x (6 calls, 6k tokens)
    fresh = {p.bucket: p for p in project(factory, specs)}["groq|gpt-oss-120b"]
    assert (fresh.runs, fresh.requests, fresh.tokens, fresh.runs_today, fresh.days) == (3, 18, 18_000, 3, 1)
    assert fresh.pacing_min == pytest.approx(18_000 / 8_000, abs=0.1)           # tpm binds before rpm
    factory.ledger.charge("groq|gpt-oss-120b", requests=1, tokens=195_000)       # 5k tokens left today
    late = {p.bucket: p for p in project(factory, specs)}["groq|gpt-oss-120b"]
    assert late.runs_today == 0 and late.days == 2
    other = {p.bucket: p for p in project(factory, [spec("fs-01", model="gpt-oss-20b")])}
    assert other["groq|gpt-oss-20b"].runs_today == 1                             # a different model's bucket


def test_usd_is_measured_when_history_has_it_else_priced_from_the_catalog(factory):
    p = project(factory, [spec("fs-01")])[0]
    served = factory.providers.served("groq", "gpt-oss-120b")
    assert p.usd == pytest.approx(served.cost(int(6000 * 0.86), int(6000 * 0.14)), abs=1e-4)


def _smoke(factory, monkeypatch, *args):
    from harness_factory.cli import main as cli
    monkeypatch.setattr(cli, "_factory", lambda store=True: factory)
    monkeypatch.setattr(cli.console, "width", 200)
    return CliRunner().invoke(cli.app, ["smoke", "--provider", "groq", "--models", "gpt-oss-120b", *args])


def test_preflight_refuses_only_what_cannot_work(factory, monkeypatch):
    called = []

    async def no_campaign(*a, **k):
        called.append(1)
        raise AssertionError("must not start")

    monkeypatch.setattr("harness_factory.runner.runner.run_campaign", no_campaign)
    dry = _smoke(factory, monkeypatch, "--dry-run")
    assert dry.exit_code == 0 and "projection" in dry.output and "groq|gpt-oss-120b" in dry.output
    assert _smoke(factory, monkeypatch, "--budget-usd", "0.0001").exit_code == 2
    factory.ledger.charge("groq|gpt-oss-120b", requests=1, tokens=195_000)
    late = _smoke(factory, monkeypatch, "--deadline-days", "1")
    assert late.exit_code == 2 and "--deadline-days 1" in " ".join(late.output.split())
    factory.ledger.mark_exhausted("groq|gpt-oss-120b")
    spent = _smoke(factory, monkeypatch)
    assert spent.exit_code == 2 and "already spent" in spent.output
    assert not called
