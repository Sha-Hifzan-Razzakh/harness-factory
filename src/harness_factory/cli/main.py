"""hfx: command line for the factory. (Not "hf": huggingface_hub, a LiteLLM dependency, owns that name.)"""

from __future__ import annotations

import asyncio

import typer
from rich.console import Console
from rich.table import Table

from harness_factory import settings

app = typer.Typer(no_args_is_help=True, add_completion=False, help="Harness Factory")
console = Console()


def _factory(store: bool = True):
    from harness_factory.runner.runner import Factory
    return Factory.load(store=store)


def _reachable(url: str) -> bool:
    import socket
    from urllib.parse import urlparse
    u = urlparse(url)
    try:
        with socket.create_connection((u.hostname or "localhost", u.port or (443 if u.scheme == "https" else 80)), 1.0):
            return True
    except OSError:
        return False


def _print_sinks(f) -> None:
    """Where this command's results go. Printed up front, so a stale .env (wrong port, someone else's MLflow, a
    missing extra) is visible before a campaign spends quota."""
    console.print(f"[dim]results  {f.store.engine.url.render_as_string() if f.store else '(not stored)'}[/]")
    otlp = settings.OTLP_ENDPOINT
    if not otlp:
        console.print(f"[dim]traces   {settings.TRACES_DIR} (JSONL); OTLP off[/]")
    elif _reachable(otlp):
        console.print(f"[dim]traces   {settings.TRACES_DIR} (JSONL) + OTLP {otlp}[/]")
    else:
        console.print(f"[yellow]traces   OTLP {otlp} is not reachable: spans will be dropped (JSONL still written)")
    t = f.tracker
    if t.enabled:
        console.print(f"[dim]mlflow   {settings.MLFLOW_TRACKING_URI} (experiment {t.experiment})[/]")
    elif t.disabled_reason:
        console.print(f"[yellow]mlflow   off: {t.disabled_reason}")


def _projection_table(projection) -> Table:
    t = Table("bucket", "runs", "requests", "tokens", "list $", "today", "days", "pacing", "estimated from",
              title="projection (MOD-05): from history where it exists")
    for p in projection:
        src = ", ".join(f"{k} {v}" for k, v in sorted(p.sources.items(), key=lambda kv: -kv[1]))
        t.add_row(p.bucket, str(p.runs), f"{p.requests:,}", f"{p.tokens:,}", f"{p.usd:.2f}",
                  f"{p.runs_today}/{p.runs}", str(p.days), f"{p.pacing_min:.0f} min", src)
    return t


def _records_table(recs) -> Table:
    t = Table("task", "class", "recipe", "model", "faults", "status", "O", "Q", "E", "calls", "tools", "tokens")
    for r in recs:
        colour = {"SUCCESS": "green", "PARTIAL": "yellow"}.get(r.status.value, "red")
        t.add_row(r.run_key.task_version, r.task_class, r.recipe_id, r.run_key.canonical_model, r.fault or "",
                  f"[{colour}]{r.status.value}", f"{r.scores.outcome:.2f}", f"{r.scores.trajectory:.2f}",
                  f"{r.composite:.3f}", str(r.cost.model_calls), str(r.cost.tool_calls),
                  str(r.cost.tokens_in + r.cost.tokens_out))
    return t


def _cells_table(baseline, cells, by_class: bool = True) -> Table:
    cols = (["task class"] if by_class else []) + ["recipe", "model", "provider", "env", "n", "excl.", "success",
                                                     "E0", "C", "R", "E", "ci95 (E)", "tokens"]
    t = Table(*cols, title=f"cost baseline {baseline.id} ({baseline.basis}, {baseline.note or 'saved'})")
    for c in cells:
        row = ([c.task_class] if by_class else []) + [
            c.recipe_id, c.canonical_model, c.provider, c.env_hash[:6], str(c.e.n),
            str(c.excluded) if c.excluded else "",
            f"{c.e.pass_rate:.0%}" if c.e.n else "-", f"{c.mean_e0:.3f}" if c.e.n else "-",
            f"{c.mean_c:.2f}" if c.e.n else "-",
            f"{c.robustness:.2f} (n={c.n_faulted})" if c.robustness is not None else "-",
            f"{c.e.mean:.3f}" if c.e.n else "-",
            f"[{c.e.ci95[0]:.3f}, {c.e.ci95[1]:.3f}]" if c.e.n > 1 else "-", str(c.tokens)]
        t.add_row(*row)
    return t


def _mean(values: list[float]) -> str:
    return f"{sum(values) / len(values):.3f}" if values else "-"


def _wmean(cells, value) -> str:
    """Mean of a per-cell value, weighted by each cell's scored runs."""
    n = sum(c.e.n for c in cells)
    return f"{sum(value(c) * c.e.n for c in cells) / n:.3f}" if n else "-"


def _summary_table(recs) -> Table:
    """One row per recipe x model, built from the same cells as `results --cells` (so E includes R when faulted
    runs exist), pooled across task classes weighted by scored runs. excl. = INCOMPLETE/ERROR, never averaged."""
    from harness_factory.results.cost import CostBaseline
    from harness_factory.results.store import aggregate_rows, record_row

    rows = [record_row(r) for r in recs]
    baseline = CostBaseline.from_rows(rows, note="these runs")
    groups: dict[tuple[str, str], list] = {}
    for c in aggregate_rows(rows, baseline):
        groups.setdefault((c.recipe_id, c.canonical_model), []).append(c)
    faulted = sum(1 for r in recs if r.fault)
    t = Table("recipe", "model", "success", "excl.", "E0", "C", "R", "E", "tokens",
              title=f"cost-adjusted against these runs ({baseline.basis})"
                    + (f"; R from {faulted} faulted runs" if faulted else ""))
    for (rid, m), cells in groups.items():
        n = sum(c.e.n for c in cells)
        rs = [c.robustness for c in cells if c.robustness is not None]
        succ = round(sum(c.e.pass_rate * c.e.n for c in cells))
        excl = sum(c.excluded for c in cells)
        t.add_row(rid, m, f"{succ}/{n}", str(excl) if excl else "",
                  _wmean(cells, lambda c: c.mean_e0), _wmean(cells, lambda c: c.mean_c),
                  f"{sum(rs) / len(rs):.2f}" if rs else "-", _wmean(cells, lambda c: c.e.mean),
                  str(sum(c.tokens for c in cells)))
    return t


@app.command()
def tasks() -> None:
    """List tasks with their class keys."""
    from harness_factory.provision.provisioner import task_class
    f = _factory(store=False)
    t = Table("task", "domain", "class", "split", "status", "allowed tools")
    for task in f.tasks.all(include_held_out=True):
        t.add_row(task.key(), task.domain, task_class(task, f.declared), task.split.value, task.status,
                  ", ".join(task.allowed_tools))
    console.print(t)


@app.command()
def validate() -> None:
    """Validate every recipe against the tool catalog, prompts and providers."""
    from harness_factory.recipes.validate import validate_recipe
    f = _factory(store=False)
    bad = 0
    for rid, recipe in f.recipes.items():
        issues = validate_recipe(recipe, f.declared)
        console.print(f"{'[green]ok' if not issues else '[red]FAIL'}[/] {rid} ({recipe.content_hash()})")
        for i in issues:
            console.print(f"    {i.rule}: {i.detail}")
        bad += bool(issues)
    raise typer.Exit(code=1 if bad else 0)


@app.command()
def reference(task_id: str | None = typer.Argument(None)) -> None:
    """Run reference solutions against their own oracles (TSK-04)."""
    from harness_factory.runner.runner import run_reference
    f = _factory(store=False)
    todo = [f.tasks.get(task_id)] if task_id else f.tasks.all(include_held_out=True)
    failed = 0
    for task in todo:
        g = asyncio.run(run_reference(f, task))
        console.print(f"{'[green]pass' if g.passed else '[red]FAIL'}[/] {task.key()} score={g.score:.2f}  {g.detail}")
        for c in g.checks:
            if not c.passed:
                console.print(f"    {c.id}: {c.detail}")
        failed += not g.passed
    raise typer.Exit(code=1 if failed else 0)


@app.command()
def run(task: str = typer.Option(..., "--task"), recipe: str = typer.Option("v2-base", "--recipe"),
        model: str = typer.Option(..., "--model"), provider: str = typer.Option(..., "--provider"),
        seed: int = typer.Option(0, "--seed")) -> None:
    """Run one task with one recipe on one model."""
    from harness_factory.runner.runner import run_one
    f = _factory()
    _print_sinks(f)
    rec = asyncio.run(run_one(f, f.tasks.get(task), f.recipes[recipe], model, provider, seed))
    console.print(_records_table([rec]))


@app.command()
def smoke(recipe: str = typer.Option("v2-base", "--recipe", help="recipe id, or comma-separated ids to compare"),
          provider: str = typer.Option("scripted", "--provider"),
          models: str | None = typer.Option(None, "--models", help="comma-separated canonical models"),
          seeds: int = typer.Option(1, "--seeds"),
          concurrency: int = typer.Option(4, "--concurrency", "-j", help="runs in flight at once"),
          resume: bool = typer.Option(False, "--resume",
                                      help="skip runs that already have a scored result; rerun INCOMPLETE/ERROR"),
          dry_run: bool = typer.Option(False, "--dry-run", help="show what would be skipped and run, then stop"),
          faults: str | None = typer.Option(None, "--faults",
                                            help="fault profiles to add, comma-separated; 'none' = clean "
                                                 "(R needs both, e.g. none,first-call-fails)"),
          deadline_days: int | None = typer.Option(None, "--deadline-days",
                                                   help="refuse if the projection needs more days than this"),
          budget_usd: float | None = typer.Option(None, "--budget-usd",
                                                  help="refuse if the projected list-price cost exceeds this"),
          strict: bool = typer.Option(False, "--strict", help="exit 1 unless every run is SUCCESS (CI gate)")) -> None:
    """Every task x recipe x model x seed on one provider, run in parallel and paced to the provider's limits.
    One recipe is the P0 gate; several compare shapes. Interrupted (Ctrl-C, quota)? Run it again with --resume."""
    from harness_factory.provision.provisioner import run_key_for
    from harness_factory.runner.runner import plan, plan_status, run_campaign
    f = _factory()
    _print_sinks(f)
    specs = plan(f, recipe.split(","), provider, models.split(",") if models else None, seeds,
                 faults=[None if x == "none" else x for x in faults.split(",")] if faults else None)
    status = plan_status(f, specs)
    todo = status.retry + status.new if resume else specs
    if dry_run or resume:
        console.print(f"plan: {len(specs)} runs   done {len(status.done)}   retry (INCOMPLETE/ERROR) "
                      f"{len(status.retry)}   new {len(status.new)}"
                      + ("" if resume else "   (without --resume every run executes again)"))
    # Preflight (MOD-05): project requests, tokens, dollars and days per quota bucket from history. A plan longer
    # than today's quota is normal on free tiers; refuse only what can't work: a bucket already spent today, or a
    # plan that misses the deadline or the budget.
    from harness_factory.runner.projection import project
    projection = project(f, todo)
    console.print(_projection_table(projection))
    problems = []
    for p in projection:
        if f.ledger.exhausted(p.bucket):
            problems.append(f"{p.bucket}: today's quota is already spent (ledger: {settings.QUOTA_STATE.name}); "
                            f"run again with --resume after the reset (00:00 UTC)")
        elif deadline_days is not None and p.days > deadline_days:
            problems.append(f"{p.bucket}: needs about {p.days} days > --deadline-days {deadline_days}")
    total_usd = sum(p.usd for p in projection)
    if budget_usd is not None and total_usd > budget_usd:
        problems.append(f"projected ${total_usd:.2f} at list prices > --budget-usd {budget_usd}")
    if dry_run:
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
    result = asyncio.run(run_campaign(f, specs, concurrency, resume=resume))
    recs = result.records
    if recs:
        console.print(_records_table(recs))
        if len(set(recipe.split(","))) > 1 or not models or "," in models:
            console.print(_summary_table(recs))
    ok = sum(r.status.value == "SUCCESS" for r in recs)
    waits = ", ".join(f"{p} {w:.1f}s" for p, w in result.quota_wait_s.items()) or "none"
    console.print(f"{ok}/{len(recs)} SUCCESS   {len(recs)} runs in {result.wall_s:.1f}s at concurrency "
                  f"{result.concurrency}   quota waits: {waits}"
                  + (f"   skipped {len(result.skipped)} done, reran {result.reruns}" if resume else ""))
    incomplete = sum(r.status.value == "INCOMPLETE" for r in recs)
    if incomplete:
        console.print(f"[yellow]{incomplete} run(s) INCOMPLETE (provider or quota); run the same command with "
                      f"--resume to finish them")
    for e in result.errors:
        console.print(f"[red]error[/] {e.spec.task_id} {e.spec.recipe_id} {e.spec.canonical_model} "
                      f"seed {e.spec.seed}: {e.error}")
    if strict:
        # The gate is about the whole plan, including runs a resume skipped.
        latest = {r["run_key"]: r["status"] for r in f.store.rows()} if f.store else {}
        keys = [run_key_for(f.tasks.get(s.task_id), f.recipes[s.recipe_id], s.canonical_model, s.provider, s.seed,
                            declared=f.declared, providers=f.providers, fault=f.fault(s.fault)).as_str()
                for s in specs if s.fault is None]           # the gate is about clean runs; faults measure R
        if result.errors or any(latest.get(k) != "SUCCESS" for k in keys):
            raise typer.Exit(code=1)
    elif result.errors:
        raise typer.Exit(code=1)


@app.command()
def results(limit: int = typer.Option(20, "--limit"), cells: bool = typer.Option(False, "--cells"),
            baseline: str | None = typer.Option(None, "--baseline",
                                                help="saved baseline id, or 'latest'; default: live")) -> None:
    """Show recent runs, or per-cell cost-adjusted aggregates with --cells."""
    from harness_factory.results.store import ResultsStore
    store = ResultsStore()
    if cells:
        chosen = None
        if baseline == "latest":
            saved = store.list_baselines()
            if not saved:
                console.print("[red]no saved baseline; run `hfx baseline save` first")
                raise typer.Exit(code=1)
            chosen = saved[-1]
        elif baseline:
            chosen = store.get_baseline(baseline)
            if chosen is None:
                console.print(f"[red]no baseline {baseline!r}")
                raise typer.Exit(code=1)
        from harness_factory.select.selector import current_task_hashes
        used, result = store.aggregate(chosen, task_hashes=current_task_hashes(_factory(store=False)))
        console.print(_cells_table(used, result))
        from harness_factory.results.cost import baseline_key
        missing = sorted({baseline_key(r) for r in store.rows()} - set(used.medians))
        if missing:
            console.print(f"[yellow]not in this baseline (scored with C = 0): {', '.join(missing)}")
        for task, n in store.stale_tasks().items():
            console.print(f"[yellow]{task} has {n} different definitions in the store (edited between runs); "
                          f"only rows of its current definition are counted")
        return
    t = Table("id", "task", "recipe", "model", "status", "E0", "tools", "created")
    for r in store.recent(limit):
        t.add_row(str(r["id"]), r["task_version"], r["recipe_id"], r["canonical_model"], r["status"],
                  f"{r['composite']:.3f}", str(r["tool_calls"]), r["created_at"].strftime("%H:%M:%S"))
    console.print(t)


tools_app = typer.Typer(no_args_is_help=True, help="Declared tool catalog (catalog/tools.yaml).")
app.add_typer(tools_app, name="tools")


@tools_app.command("freeze")
def tools_freeze(check: bool = typer.Option(False, "--check", help="exit 1 if tools.yaml is out of date")) -> None:
    """Discover every server's tools and write their schema hashes into tools.yaml, so a later schema change
    fails provisioning (drift) instead of silently changing what the model sees."""
    import yaml

    from harness_factory.env.sandbox import make_sandbox
    from harness_factory.mcp.pool import MCPSessionPool

    f = _factory(store=False)

    async def discover() -> dict[str, str]:
        with make_sandbox("freeze") as sb:
            sb.reset(f.tasks.all()[0])
            declared = f.declared.model_copy(deep=True)
            for s in declared.servers:                      # discovery only: ignore declared hashes
                for t in s.tools:
                    t.schema_hash = None
            remote_ok = settings.allow_remote() or settings.remote_mode() == "replay"     # replay: recorded schemas
            servers = [s.id for s in declared.servers if not s.remote or remote_ok]
            async with MCPSessionPool(declared, servers, sb.env(), sb.launch) as pool:
                return {ref: pool.catalog.schema_hash(ref) for ref in pool.catalog.tools}

    skipped = [s.id for s in f.declared.servers if s.remote and not (settings.allow_remote()
                                                                      or settings.remote_mode() == "replay")]
    if skipped:
        console.print(f"[yellow]skipped remote server(s) {', '.join(skipped)}: set HF_ALLOW_REMOTE=1 (live) or "
                      f"HF_REMOTE_MODE=replay (recorded schemas) to check them")
    hashes = asyncio.run(discover())
    text = settings.TOOLS_FILE.read_text()
    data = yaml.safe_load(text)
    changed = []
    for server in data["servers"]:
        if server["id"] in skipped:
            continue
        for tool in server["tools"]:
            ref = f"{server['id']}.{tool['name']}"
            if tool.get("schema_hash") != hashes[ref]:
                changed.append(ref)
                tool["schema_hash"] = hashes[ref]
    if check:
        for ref in changed:
            console.print(f"[red]stale[/] {ref}")
        console.print("tools.yaml is up to date" if not changed else f"{len(changed)} tool(s) changed")
        raise typer.Exit(code=1 if changed else 0)
    header = "".join(line for line in text.splitlines(keepends=True) if line.startswith("#"))
    settings.TOOLS_FILE.write_text(header + yaml.safe_dump(data, sort_keys=False, width=120))
    console.print(f"wrote {len(hashes)} schema hashes ({len(changed)} changed) to {settings.TOOLS_FILE.name}")


baseline_app = typer.Typer(no_args_is_help=True, help="Cost baselines: the per-task medians that C is relative to.")
app.add_typer(baseline_app, name="baseline")


@baseline_app.command("save")
def baseline_save(note: str = typer.Option("", "--note")) -> None:
    """Freeze the live baseline (current per-task medians) so later runs are scored against fixed numbers."""
    from harness_factory.results.store import ResultsStore
    store = ResultsStore()
    b = store.live_baseline()
    b.note = note
    if not b.medians:
        console.print("[red]no runs in the store yet")
        raise typer.Exit(code=1)
    console.print(f"saved baseline {store.save_baseline(b)} ({b.basis}, {len(b.medians)} tasks)")


@baseline_app.command("list")
def baseline_list() -> None:
    """Saved baselines, oldest first."""
    from harness_factory.results.store import ResultsStore
    store = ResultsStore()
    t = Table("id", "basis", "provider|task medians", "note", "created")
    for b in store.list_baselines():
        t.add_row(b.id, b.basis, str(len(b.medians)), b.note, b.created_at.strftime("%Y-%m-%d %H:%M"))
    console.print(t)
    legacy = store.legacy_baselines()
    if legacy:
        console.print(f"[yellow]{len(legacy)} baseline(s) saved before per-provider medians are ignored: "
                      f"{', '.join(legacy)}")


if __name__ == "__main__":
    app()


select_app = typer.Typer(no_args_is_help=True, help="Selector: which recipe to use per task class (SEL-01..06).")
app.add_typer(select_app, name="select")


def _evidence(f, baseline_id: str | None):
    from harness_factory.results.cost import CostBaseline
    from harness_factory.results.store import current_task_rows
    from harness_factory.select.selector import current_task_hashes
    rows = current_task_rows(f.store.rows(), current_task_hashes(f))
    if baseline_id:
        b = f.store.get_baseline(baseline_id)
        if b is None:
            raise typer.BadParameter(f"no baseline {baseline_id!r} (hfx baseline list)")
        return rows, b
    return rows, CostBaseline.from_rows(rows, note="live")


def _decision_row(t: Table, d) -> None:
    top = d.ranked[0] if d.ranked else None
    ev = f"{top.mean_e:.3f} [{top.ci95[0]:.2f}-{top.ci95[1]:.2f}] n={top.n}" if top else "-"
    runner = f"{d.ranked[1].recipe_id} {d.ranked[1].mean_e:.3f}" if len(d.ranked) > 1 else "-"
    pruned = ", ".join(f"{p.recipe_id} ({p.rule})" for p in d.pruned) or "-"
    t.add_row(d.task_class, d.rule, " + ".join(d.chosen), ev, runner, str(len(d.insufficient)), pruned)


@select_app.command("show")
def select_show(model: str = typer.Option(..., "--model"), provider: str = typer.Option(..., "--provider"),
                policy: str | None = typer.Option(None, "--policy", help="a frozen policy id instead of live"),
                baseline: str | None = typer.Option(None, "--baseline", help="frozen cost baseline id")) -> None:
    """The recipe the selector picks for every task class, with its evidence."""
    from harness_factory.select.policy import build_policy, load_policy
    f = _factory()
    if policy:
        p = load_policy(policy)
    else:
        rows, b = _evidence(f, baseline)
        p = build_policy(f, rows, b, model, provider)
    t = Table("class", "rule", "chosen", "E [95% CI] n", "runner-up", "below min", "pruned",
              title=f"{p.canonical_model} on {p.provider}   policy {p.id}{' (frozen)' if policy else ' (live)'}   "
                    f"baseline {p.baseline_id}")
    for d in p.decisions.values():
        _decision_row(t, d)
    console.print(t)


@select_app.command("freeze")
def select_freeze(model: str = typer.Option(..., "--model"), provider: str = typer.Option(..., "--provider"),
                  baseline: str | None = typer.Option(None, "--baseline"), note: str = typer.Option("", "--note")) -> None:
    """Freeze the current decisions as a content-addressed policy, so provisioning stays stable as runs arrive."""
    from harness_factory.select.policy import build_policy, save_policy
    f = _factory()
    rows, b = _evidence(f, baseline)
    p = build_policy(f, rows, b, model, provider, note=note)
    console.print(f"saved policy {p.id} ({len(p.decisions)} classes) to {save_policy(p)}")


@select_app.command("evaluate")
def select_evaluate(model: str = typer.Option(..., "--model"), provider: str = typer.Option(..., "--provider"),
                    split: str = typer.Option("HELD_OUT", "--split"),
                    concurrency: int = typer.Option(4, "--concurrency", "-j"),
                    dry_run: bool = typer.Option(False, "--dry-run", help="show the picks and the runs, then stop")) -> None:
    """SEL-06: run the held-out tasks with the selector's pick, the best global recipe, rules-only and the
    hand-picked baseline, and compare mean E. Held-out runs never inform the picks. Done runs are reused."""
    from harness_factory.provision.provisioner import run_key_for
    from harness_factory.results.cost import CostBaseline
    from harness_factory.runner.runner import RunSpec, run_campaign
    from harness_factory.select.policy import evidence_rows, plan_evaluation, score_evaluation
    from harness_factory.tasks.model import Split
    f = _factory()
    _print_sinks(f)
    plan = plan_evaluation(f, f.store.rows(), model, provider, split=Split(split))
    t = Table("task", *plan.picks, title=f"{split} picks for {model} on {provider} (evidence baseline {plan.baseline_id})")
    for task_id in plan.picks["selector"]:
        t.add_row(task_id, *(plan.picks[s][task_id] for s in plan.picks))
    console.print(t)
    for n in plan.notes:
        console.print(f"[yellow]{n}")
    runs = plan.runs()
    console.print(f"{len(runs)} distinct runs (strategies that agree share one)")
    if dry_run:
        return
    specs = [RunSpec(task_id=tk, recipe_id=r, canonical_model=model, provider=provider) for tk, r in runs]
    asyncio.run(run_campaign(f, specs, concurrency, resume=True))
    rows = f.store.rows()
    keys = {(tk, r): run_key_for(f.tasks.get(tk), f.recipes[r], model, provider, 0, declared=f.declared,
                                 providers=f.providers).as_str() for tk, r in runs}
    # Cost medians over the evidence plus the evaluation runs themselves, so a strategy that solves a held-out
    # task more cheaply than another scores higher; all strategies are scored against this one baseline.
    planned = set(keys.values())
    baseline = CostBaseline.from_rows(evidence_rows(rows, set(plan.picks["selector"])) +
                                      [r for r in rows if r["run_key"] in planned], note="evaluation")
    scores = score_evaluation(plan, f, rows, baseline, keys)
    st = Table("strategy", "mean E", "passed", "scored", title=f"{split} evaluation (SEL-06)")
    for s in scores:
        st.add_row(s.strategy, "-" if s.mean_e is None else f"{s.mean_e:.3f}", f"{s.passed}/{s.tasks}",
                   f"{s.scored}/{s.tasks}")
    console.print(st)


@app.command()
def provision(task: str = typer.Option(..., "--task"), model: str = typer.Option(..., "--model"),
              provider: str = typer.Option(..., "--provider"),
              policy: str | None = typer.Option(None, "--policy", help="frozen policy id; default: decide live"),
              baseline: str | None = typer.Option(None, "--baseline"), seed: int = typer.Option(0, "--seed")) -> None:
    """Select and run: the task's class picks the recipe (a close call runs both, SEL-04), the decision is logged
    with its evidence (SEL-05), and the run's result feeds the next decision."""
    from harness_factory.provision.provisioner import task_class
    from harness_factory.runner.runner import RunSpec, run_campaign
    from harness_factory.select.policy import load_policy, log_decision
    from harness_factory.select.selector import decide
    f = _factory()
    _print_sinks(f)
    tk = f.tasks.get(task)
    cls = task_class(tk, f.declared)
    if policy:
        p = load_policy(policy)
        if (p.canonical_model, p.provider) != (model, provider):
            raise typer.BadParameter(f"policy {policy} is for {p.canonical_model} on {p.provider}")
        d = p.decisions[cls]
    else:
        rows, b = _evidence(f, baseline)
        d = decide(cls, model, provider, factory=f, rows=rows, baseline=b, task=tk)
    path = log_decision(d, task=tk.key(), policy=policy, seed=seed)
    console.print(f"{d.summary()}\n[dim]decision logged: {path}[/]")
    specs = [RunSpec(task_id=task, recipe_id=r, canonical_model=model, provider=provider, seed=seed) for r in d.chosen]
    result = asyncio.run(run_campaign(f, specs, concurrency=len(specs)))
    if result.records:
        console.print(_records_table(result.records))
    for e in result.errors:
        console.print(f"[red]error[/] {e.spec.recipe_id}: {e.error}")


@app.command()
def report(out: str | None = typer.Option(None, "--out", help="write markdown here (default: print)"),
           models: str | None = typer.Option(None, "--models", help="comma-separated models (default: all)"),
           baseline: str | None = typer.Option(None, "--baseline", help="frozen cost baseline id"),
           title: str = typer.Option("Campaign report", "--title")) -> None:
    """A markdown campaign report from the results store: status, paired matrix-vs-base deltas, selector picks,
    failures and unscored runs."""
    from pathlib import Path

    from harness_factory.results.report import build_report
    f = _factory()
    rows, b = _evidence(f, baseline)
    text = build_report(f, rows, b, title=title, models=models.split(",") if models else None)
    if out:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(text + "\n")
        console.print(f"wrote {out}")
    else:
        print(text)


from harness_factory.cli.authoring import register as _register_authoring  # hfx new, hfx catalog

_register_authoring(app)
