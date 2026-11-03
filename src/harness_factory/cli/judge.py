"""hfx judge: the rubric judge's identity and calibration (grading/judge.py, grading/calibration.py, ADR 0014)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

console = Console()
judge_app = typer.Typer(no_args_is_help=True, add_completion=False,
                        help="The rubric judge: status and calibration (ADR 0014)")


def _judge(f, model: str | None, provider: str | None):
    from harness_factory.grading.judge import JudgeConfig, RubricJudge, load_judge_config
    cfg = load_judge_config()
    if cfg is None and not (model and provider):
        console.print("[red]no judge configured (catalog/judge/judge.yaml); pass --model and --provider")
        raise typer.Exit(code=1)
    cfg = (cfg or JudgeConfig(provider=provider, model=model)).model_copy(
        update={k: v for k, v in (("provider", provider), ("model", model)) if v})
    try:
        return RubricJudge(cfg, f.providers, f.ledger, f.store.calibrations if f.store else None)
    except (KeyError, FileNotFoundError) as e:
        console.print(f"[red]{e}")
        raise typer.Exit(code=1) from e


def _trust(judge) -> str:
    from harness_factory.grading.judge import MIN_ITEMS, MIN_KAPPA
    ok, rec = judge.calibration()
    if rec is None:
        return f"[yellow]uncalibrated[/] (no record on >= {MIN_ITEMS} items): rubric checks carry weight 0"
    kappa = "undefined" if rec.kappa is None else f"{rec.kappa:.3f}"
    verdict = "[green]calibrated[/]" if ok else f"[yellow]uncalibrated[/] (kappa < {MIN_KAPPA}): weight 0"
    return f"{verdict}: kappa {kappa} on {rec.n} items of {rec.set_name}, {rec.created_at:%Y-%m-%d}"


@judge_app.command("status")
def status(model: str | None = typer.Option(None, "--model"),
           provider: str | None = typer.Option(None, "--provider")) -> None:
    """The judge in effect, its hash, and whether its rubric checks carry weight."""
    from harness_factory.runner.runner import Factory
    f = Factory.load()
    judge = _judge(f, model, provider)
    console.print(f"judge  {judge.config.provider} / {judge.config.model}  hash {judge.hash}")
    console.print(f"trust  {_trust(judge)}")
    records = f.store.calibrations(judge.hash) if f.store else []
    if records:
        t = Table("date", "set", "n", "failed", "kappa", "MAE", title="calibration records of this hash")
        for r in records:
            t.add_row(f"{r.created_at:%Y-%m-%d %H:%M}", r.set_name, str(r.n), str(r.failed),
                      "-" if r.kappa is None else f"{r.kappa:.3f}", "-" if r.mae is None else f"{r.mae:.3f}")
        console.print(t)


SET_OPT = typer.Option(..., "--set", exists=True, dir_okay=False,
                       help="calibration set YAML (format: grading/calibration.py)")
OUT_OPT = typer.Option(None, "--out", help="write the full report as JSON")


@judge_app.command("calibrate")
def calibrate(set_path: Path = SET_OPT,
              model: str | None = typer.Option(None, "--model", help="canonical judge model (default: judge.yaml)"),
              provider: str | None = typer.Option(None, "--provider", help="its provider (default: judge.yaml)"),
              record: bool = typer.Option(True, "--record/--no-record",
                                          help="store the calibration record in the results store"),
              concurrency: int = typer.Option(4, "--concurrency", "-j"),
              out: Path | None = OUT_OPT) -> None:
    """Run the judge over a hand-scored set; report Cohen's kappa (pass/fail) and MAE (scores), and store the
    record under the judge's hash. The judge is trusted at n >= 100 and kappa >= 0.7."""
    from harness_factory.grading.calibration import calibrate as run_calibration
    from harness_factory.grading.calibration import load_calibration_set
    from harness_factory.grading.judge import MIN_ITEMS, MIN_KAPPA
    from harness_factory.runner.runner import Factory
    f = Factory.load(store=record)
    judge = _judge(f, model, provider)
    try:
        cset = load_calibration_set(set_path)
    except ValueError as e:
        console.print(f"[red]bad calibration set {set_path}: {e}")
        raise typer.Exit(code=1) from e
    console.print(f"judge {judge.config.provider} / {judge.config.model} ({judge.hash}) over {len(cset.items)} "
                  f"items of {cset.name}")
    report = asyncio.run(run_calibration(judge, cset, concurrency=concurrency))
    rec = report.record
    t = Table("item", "human", "judge", "pass (h / j)", "note", title="disagreements and failures")
    for i in report.items:
        if i.judge_score is None or i.judge_pass != i.human_pass:
            t.add_row(i.id, f"{i.human_score:.2f}", "-" if i.judge_score is None else f"{i.judge_score:.2f}",
                      f"{i.human_pass} / {'-' if i.judge_pass is None else i.judge_pass}", i.error or "")
    if t.row_count:
        console.print(t)
    kappa = "undefined (one label only)" if rec.kappa is None else f"{rec.kappa:.3f}"
    mae = "-" if rec.mae is None else f"{rec.mae:.3f}"
    console.print(f"n {rec.n} (failed {rec.failed})  kappa {kappa}  MAE {mae}  judge usage {report.tokens} tokens, "
                  f"${report.usd:.4f}")
    if rec.qualifies():
        console.print(f"[green]qualifies[/]: n >= {MIN_ITEMS} and kappa >= {MIN_KAPPA}")
    else:
        console.print(f"[yellow]does not qualify[/]: needs n >= {MIN_ITEMS} and kappa >= {MIN_KAPPA}")
    if record and f.store is not None:
        f.store.add_calibration(rec)
        console.print(f"recorded for judge {rec.judge_hash}; {_trust(judge)}")
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report.model_dump(mode="json"), indent=1))
        console.print(f"wrote {out}")


def register(app: typer.Typer) -> None:
    app.add_typer(judge_app, name="judge")
