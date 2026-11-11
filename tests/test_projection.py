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
    # Scripted history exists but nothing matches v3: the recipe's caps, an upper bound, not the shape default.
    ceiling = est.estimate("scripted/replay", "scripted", v3, "fs-01@1")
    assert ceiling.source == "ceiling (recipe caps)" and ceiling.upper_bound
    assert (ceiling.calls, ceiling.tokens) == (v3.config.max_steps, v3.config.max_tokens)


def _row(model, recipe, task, calls, tokens, usd=0.0, provider="groq", status="SUCCESS"):
    return {"canonical_model": model, "recipe_id": recipe, "task_version": task, "model_calls": calls,
            "tokens_in": tokens, "tokens_out": 0, "usd": usd, "provider": provider, "status": status,
            "run_key": "t|h|r|m|p|0"}


def test_fallback_levels_without_history_for_the_pair(factory):
    """#113, lab E2: a new recipe on audit tasks projected 252K tokens for 42 runs from the shape default; real
    runs used 56K-200K each. Each level below the pair's own history, most specific first."""
    v2, v3 = factory.recipes["v2-base"], factory.recipes["v3-staged"]
    rows = [_row("big", "v2-search", "da-01@1", 20, 120_000),        # this task is heavy on `big` (another V2)
            _row("big", "v2-search", "da-02@1", 20, 80_000),
            _row("small", "v2-base", "da-01@1", 8, 10_000),         # v2-base ran only on `small`
            _row("small", "v2-search", "da-01@1", 8, 20_000),
            _row("big", "v2-base", "da-09@1", 10, 50_000, status="ERROR")]   # unscored: never history
    est = Estimator(rows, {"scripted"}, factory.recipes)
    same_shape = est.estimate("big", "groq", v2, "da-01@1")
    assert (same_shape.source, same_shape.tokens) == ("task (same shape)", 120_000)
    assert est.estimate("big", "groq", v3, "da-01@1").source != "task (same shape)"   # V3 is another shape
    # v2-base on `big` for a task `big` never ran: v2-base's runs on `small`, scaled by big's typical run over
    # small's (median 100K vs 15K).
    scaled = est.estimate("big", "groq", v2, "da-05@1")
    assert scaled.source == "recipe (scaled)" and scaled.tokens == pytest.approx(10_000 * 100_000 / 15_000)
    assert scaled.usd is None                                       # priced at big's rates, never small's dollars
    unscaled = est.estimate("new-model", "groq", v2, "da-05@1")     # nothing known about new-model to scale by
    assert (unscaled.source, unscaled.tokens) == ("recipe (any model)", 10_000) and unscaled.usd is None
    ceiling = est.estimate("big", "groq", v3, "da-05@1")
    assert ceiling.source == "ceiling (recipe caps)" and ceiling.tokens == v3.config.max_tokens
    # The default only with no history at all of this provider kind.
    assert Estimator([], {"scripted"}, factory.recipes).estimate("big", "groq", v3, "da-05@1").source == "default"
    assert est.estimate("scripted/replay", "scripted", v2, "da-05@1").source == "default"   # real never stands in


def test_without_recipes_the_shape_level_is_skipped(factory):
    rows = [_row("big", "v2-search", "da-01@1", 20, 120_000)]
    est = Estimator(rows, {"scripted"})
    assert est.estimate("big", "groq", factory.recipes["v2-base"], "da-01@1").source == "ceiling (recipe caps)"


def test_a_ceiling_estimate_can_make_preflight_refuse_a_budget(factory):
    """Projections are what preflight reads: an upper bound may now refuse a budget that a default passed."""
    rows = [_row("gpt-oss-120b", "v2-search", "fs-09@1", 5, 5_000)]
    est = Estimator(rows, {"scripted"}, factory.recipes)
    e = est.estimate("gpt-oss-120b", "groq", factory.recipes["v3-staged"], "fs-01@1")
    assert e.upper_bound and e.tokens > SHAPE_DEFAULTS[factory.recipes["v3-staged"].variant][1] * 10


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
