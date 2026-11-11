"""`hfx bench <file>`: run a benchmark described in one YAML file (docs/design/benchmark.example.yaml).

Registered on the main app by the import at the end of cli/main.py. The file becomes the same campaign `hfx smoke`
runs (plan, plan_status, projection preflight, run_campaign with resume and faults), one provider at a time.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import typer

from harness_factory.cli import main as _main
from harness_factory.cli.main import _projection_table, _records_table, _summary_table, console


@_main.app.command()
def bench(path: str = typer.Argument(..., help="benchmark file, shaped like docs/design/benchmark.example.yaml"),
          resume: bool = typer.Option(False, "--resume",
                                      help="skip runs that already have a scored result; rerun INCOMPLETE/ERROR"),
          dry_run: bool = typer.Option(False, "--dry-run", help="validate, show the plan and projection, then stop"),
          concurrency: int | None = typer.Option(None, "--concurrency", "-j",
                                                 help="override limits.concurrency for this invocation")) -> None:
    """Run the benchmark a file describes: tasks x recipes x models x seeds x faults, under its limits, then
    freeze the cost baseline and write the report as it asks. The file is stored with the campaign under
    var/bench/. Interrupted (Ctrl-C, quota)? Run it again with --resume."""
    from harness_factory import settings
    from harness_factory.results.report import build_report
    from harness_factory.runner import bench as b
    from harness_factory.runner.projection import project
    from harness_factory.runner.runner import plan_status, run_campaign

    file = Path(path)
    try:
        spec, raw = b.load(file)
    except b.BenchFileError as e:
        console.print(f"[red]{e}", highlight=False)
        raise typer.Exit(code=2) from None
    f = _main._factory()                              # looked up at call time: tests swap it
    problems = b.check(f, spec)
    if problems:
        console.print(f"[red]{file}: {len(problems)} problem(s)\n" + "\n".join(f"  {p}" for p in problems),
                      highlight=False)
        raise typer.Exit(code=2)
    _main._print_sinks(f)
    specs = b.build_plan(f, spec)
    tasks = b.select_tasks(f, spec.tasks)
    console.print(f"bench {spec.name} (sha256 {b.file_hash(raw)[:12]}): {len(tasks)} tasks x {len(spec.recipes)} "
                  f"recipes x {len(spec.models)} models x {spec.seeds} seeds x {len(spec.faults)} fault profiles "
                  f"= {len(specs)} runs" + (f"\n[dim]{spec.note}[/]" if spec.note else ""))
    from harness_factory.runner.runner import describe_pairs
    left_out, reduced = b.reach(f, spec)
    if left_out:
        console.print(f"[yellow]left out {len(left_out)} task x recipe pair(s) whose recipe can't call every tool the "
                      f"task allows (list the recipe in plan_unreachable to run them anyway):\n"
                      + "\n".join(f"  {line}" for line in describe_pairs(left_out)), highlight=False)
    if reduced:
        console.print(f"planned {len(reduced)} pair(s) on a reduced tool set (plan_unreachable):\n"
                      + "\n".join(f"  {line}" for line in describe_pairs(reduced)), highlight=False)
    from harness_factory.runner.runner import describe_models_left_out
    no_model = b.model_reach(f, spec)
    if no_model:
        console.print(f"[yellow]left out {len(no_model)} recipe x model pair(s) that can't run:\n"
                      + "\n".join(f"  {line}" for line in describe_models_left_out(no_model)), highlight=False)
    status = plan_status(f, specs)
    todo = status.retry + status.new if resume else specs
    console.print(f"plan: {len(specs)} runs   done {len(status.done)}   retry (INCOMPLETE/ERROR) "
                  f"{len(status.retry)}   new {len(status.new)}"
                  + ("" if resume else "   (without --resume every run executes again)"))
    projection = project(f, todo)
    console.print(_projection_table(projection))
    problems = b.preflight_problems(f, projection, spec.limits.deadline_days, spec.limits.budget_usd)
    if dry_run:
        for msg in problems:
            console.print(f"[yellow]preflight: {msg}")
        return
    if problems:
        for msg in problems:
            console.print(f"[yellow]preflight: {msg}")
        raise typer.Exit(code=2)
    longer = [p for p in projection if p.days > 1]
    if longer:
        console.print("[yellow]" + "; ".join(f"{p.bucket}: today covers about {p.runs_today} of {p.runs} runs"
                                             for p in longer)
                      + ". The rest end INCOMPLETE; run the same command with --resume after the reset (00:00 UTC).")

    out_dir = b.store_file(f, spec, raw, file, specs, resume=resume)
    console.print(f"[dim]bench file stored: {out_dir / 'benchmark.yaml'}[/]")
    j = concurrency or spec.limits.concurrency
    recs, errors, skipped, reruns, wall = [], [], 0, 0, 0.0
    for provider in spec.providers():                # one provider at a time, as `hfx smoke` does
        block = [s for s in specs if s.provider == provider]
        result = asyncio.run(run_campaign(f, block, j, resume=resume))
        recs += result.records
        errors += result.errors
        skipped += len(result.skipped)
        reruns += result.reruns
        wall += result.wall_s
        waits = ", ".join(f"{p} {w:.1f}s" for p, w in result.quota_wait_s.items()) or "none"
        console.print(f"[dim]{provider}: {len(result.records)} runs in {result.wall_s:.1f}s, quota waits: {waits}[/]")
    if recs:
        console.print(_records_table(recs))
        if len(spec.recipes) > 1 or len(spec.models) > 1:
            console.print(_summary_table(recs))
    ok = sum(r.status.value == "SUCCESS" for r in recs)
    console.print(f"{ok}/{len(recs)} SUCCESS   {len(recs)} runs in {wall:.1f}s at concurrency {j}"
                  + (f"   skipped {skipped} done, reran {reruns}" if resume else ""))
    for e in errors:
        console.print(f"[red]error[/] {e.spec.task_id} {e.spec.recipe_id} {e.spec.canonical_model} "
                      f"seed {e.spec.seed}: {e.error}")

    # Judge: on the whole plan (including runs a resume skipped), from the store.
    from harness_factory.results.cost import CostBaseline
    after = plan_status(f, specs)
    unfinished = len(after.retry) + len(after.new)
    if unfinished:
        console.print(f"[yellow]{unfinished} run(s) not scored yet (INCOMPLETE/ERROR); run the same command with "
                      f"--resume to finish them")
    rows = f.store.rows()
    baseline, baseline_id = CostBaseline.from_rows(rows, note="live"), None
    if spec.judge.freeze_cost_baseline:
        if unfinished:
            console.print("[yellow]cost baseline not frozen until every run is scored")
        else:
            baseline = f.store.live_baseline()
            baseline.note = f"bench {spec.name} {b.file_hash(raw)[:12]}"
            baseline_id = f.store.save_baseline(baseline)
            console.print(f"froze cost baseline {baseline_id} ({baseline.basis})")
    report_path = None
    if spec.judge.report:
        keys = set(_manifest_keys(out_dir))
        mine = [r for r in rows if r["run_key"] in keys]
        text = build_report(f, mine, baseline, title=f"Benchmark {spec.name}",
                            compare=spec.judge.compare.pairs() if spec.judge.compare else None)
        if section := b.reach_section(left_out, reduced, no_model):
            text = f"{text}\n\n{section}"
        report_path = out_dir / "report.md"
        report_path.write_text(text + "\n")
        console.print(f"wrote {report_path}")
    b.record_outcome(out_dir, runs=len(recs), success=ok, errors=len(errors), unfinished=unfinished,
                     baseline=baseline.id, baseline_frozen=baseline_id is not None,
                     report=str(report_path.relative_to(settings.VAR)) if report_path else None)
    if errors:
        raise typer.Exit(code=1)


def _manifest_keys(out_dir: Path) -> list[str]:
    import json
    return json.loads((out_dir / "manifest.json").read_text())["run_keys"]
