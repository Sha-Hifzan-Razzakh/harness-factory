"""Background campaigns over the API (#31): the same plan, preflight and run as `hfx smoke`, started from a request
and followed by polling.

- **One ledger.** Every campaign uses the app's one Factory: one QuotaLedger and one ResultsStore, so campaigns
  started side by side are paced together, as workers of one campaign are. Each campaign starts from the catalog
  as it is then (State.factory() refreshes tasks, recipes, tools and faults, never the ledger).
- **Progress from the store.** Runs are stored as they finish; progress counts the planned RunKeys that have a row
  newer than the campaign's start.
- **Cancel cleans up.** Cancelling a campaign cancels its runs; run_campaign waits for their sandboxes and servers
  to be torn down. Finished runs are kept, and `resume` finishes the rest later.
- Campaigns live in memory: restart the API and the list is empty, but every finished run is in the store.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

State = Literal["running", "done", "cancelled", "failed"]


class CampaignRequest(BaseModel):
    recipes: list[str] = Field(default_factory=lambda: ["v2-base"], min_length=1)
    provider: str = "scripted"
    models: list[str] | None = None          # default: every model the provider serves
    tasks: list[str] | None = None           # default: every DEV + TEST task (held-out ones are the selector's)
    seeds: int = Field(1, ge=1, le=20)
    concurrency: int = Field(4, ge=1, le=32)
    faults: list[str] | None = None          # fault profiles next to clean runs; "none" = clean
    resume: bool = False                     # skip runs that already have a scored result
    deadline_days: int | None = Field(None, ge=1)
    budget_usd: float | None = Field(None, ge=0)


class CampaignPlan(BaseModel):
    total: int
    done: int
    retry: int
    new: int
    will_run: int
    projection: list[dict[str, Any]]
    problems: list[str]                      # preflight: why it would be refused
    warnings: list[str]


class Campaign(BaseModel):
    id: str
    request: CampaignRequest
    state: State = "running"
    created_at: str
    finished_at: str | None = None
    plan: CampaignPlan
    error: str | None = None
    result: dict[str, Any] | None = None      # when finished: counts, waits, errors


class CampaignInvalid(ValueError):
    """The request names something that doesn't exist or can't run (unknown recipe, model, task, fault)."""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def specs_for(f, req: CampaignRequest):
    """The RunSpecs, after checking every name in the request and every recipe on every model."""
    from harness_factory.recipes.validate import validate_recipe
    from harness_factory.runner.runner import plan

    problems = []
    try:
        f.providers.get(req.provider)
    except KeyError as e:
        raise CampaignInvalid(str(e)) from e
    models = req.models or [m.canonical for m in f.providers.get(req.provider).models]
    for m in models:
        try:
            f.providers.served(req.provider, m)
        except KeyError as e:
            problems.append(str(e))
    for rid in req.recipes:
        if rid not in f.recipes:
            problems.append(f"unknown recipe {rid!r}")
            continue
        for m in models:
            for i in validate_recipe(f.recipes[rid].with_model(m), f.declared, providers=f.providers,
                                     provider_id=req.provider):
                problems.append(f"{rid} on {m}: {i.rule}: {i.detail}")
    for t in req.tasks or []:
        if t not in {x.id for x in f.tasks.all(include_held_out=True)}:
            problems.append(f"unknown task {t!r}")
    for fid in req.faults or []:
        if fid != "none" and fid not in f.faults:
            problems.append(f"unknown fault profile {fid!r}")
    if problems:
        raise CampaignInvalid("; ".join(dict.fromkeys(problems)))
    faults = [None if x == "none" else x for x in req.faults] if req.faults else None
    return plan(f, req.recipes, req.provider, models, req.seeds, task_ids=req.tasks, faults=faults)


def preflight(f, req: CampaignRequest, specs) -> CampaignPlan:
    """As `hfx smoke`: project requests, tokens, dollars and days per quota bucket, and refuse only what can't
    work (a bucket already spent today, a plan past the deadline or over the budget)."""
    from harness_factory.runner.projection import project
    from harness_factory.runner.runner import plan_status

    status = plan_status(f, specs)
    todo = status.retry + status.new if req.resume else specs
    projection = project(f, todo)
    problems, warnings = [], []
    for p in projection:
        if f.ledger.exhausted(p.bucket):
            problems.append(f"{p.bucket}: today's quota is already spent; resume after the reset (00:00 UTC)")
        elif req.deadline_days is not None and p.days > req.deadline_days:
            problems.append(f"{p.bucket}: needs about {p.days} days > deadline {req.deadline_days}")
        elif p.days > 1:
            warnings.append(f"{p.bucket}: today covers about {p.runs_today} of {p.runs} runs; the rest end "
                            f"INCOMPLETE; resume after the reset (00:00 UTC)")
    total_usd = sum(p.usd for p in projection)
    if req.budget_usd is not None and total_usd > req.budget_usd:
        problems.append(f"projected ${total_usd:.2f} at list prices > budget ${req.budget_usd}")
    if not req.resume and status.done:
        warnings.append(f"{len(status.done)} run(s) already have a scored result and will run again "
                        f"(set resume to skip them)")
    return CampaignPlan(total=len(specs), done=len(status.done), retry=len(status.retry), new=len(status.new),
                        will_run=len(todo), projection=[p.model_dump() for p in projection], problems=problems,
                        warnings=warnings)


class CampaignManager:
    def __init__(self) -> None:
        self.campaigns: dict[str, Campaign] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._keys: dict[str, list[str]] = {}
        self._start_row: dict[str, int] = {}
        self._factories: dict[str, Any] = {}

    def start(self, f, req: CampaignRequest, specs, plan_: CampaignPlan) -> Campaign:
        from harness_factory.provision.provisioner import run_key_for

        cid = uuid.uuid4().hex[:8]
        c = Campaign(id=cid, request=req, created_at=_now(), plan=plan_)
        self._keys[cid] = [run_key_for(f.tasks.get(s.task_id), f.recipes[s.recipe_id], s.canonical_model, s.provider,
                                       s.seed, declared=f.declared, providers=f.providers,
                                       fault=f.fault(s.fault)).as_str() for s in specs]
        rows = f.store.rows(latest=False) if f.store else []
        self._start_row[cid] = max((r["id"] for r in rows), default=0)
        self._factories[cid] = f
        self.campaigns[cid] = c
        self._tasks[cid] = asyncio.get_running_loop().create_task(self._run(c, f, specs))
        return c

    async def _run(self, c: Campaign, f, specs) -> None:
        from harness_factory.runner.runner import run_campaign
        try:
            result = await run_campaign(f, specs, c.request.concurrency, resume=c.request.resume)
            statuses: dict[str, int] = {}
            for r in result.records:
                statuses[r.status.value] = statuses.get(r.status.value, 0) + 1
            c.result = {"runs": len(result.records), "statuses": statuses, "wall_s": result.wall_s,
                        "quota_wait_s": result.quota_wait_s, "skipped": len(result.skipped), "reruns": result.reruns,
                        "errors": [{"spec": e.spec.model_dump(), "error": e.error} for e in result.errors]}
            c.state = "done"
        except asyncio.CancelledError:
            c.state = "cancelled"
        except Exception as e:  # noqa: BLE001  (a campaign that can't run is reported on the campaign)
            c.state, c.error = "failed", f"{type(e).__name__}: {e}"
        finally:
            c.finished_at = _now()

    def progress(self, c: Campaign) -> dict[str, Any]:
        f = self._factories.get(c.id)
        keys = set(self._keys.get(c.id, []))
        start = self._start_row.get(c.id, 0)
        rows = [r for r in (f.store.rows(latest=False) if f and f.store else [])
                if r["id"] > start and r["run_key"] in keys]
        statuses: dict[str, int] = {}
        for r in rows:
            statuses[r["status"]] = statuses.get(r["status"], 0) + 1
        return {"finished": len(rows), "of": c.plan.will_run, "statuses": statuses,
                "recent": [{"id": r["id"], "task": r["task_version"], "recipe_id": r["recipe_id"],
                            "model": r["canonical_model"], "status": r["status"], "composite": r["composite"]}
                           for r in sorted(rows, key=lambda r: r["id"], reverse=True)[:20]]}

    def cancel(self, cid: str) -> bool:
        task = self._tasks.get(cid)
        if task is None or task.done():
            return False
        task.cancel()
        return True

    async def shutdown(self) -> None:
        """Cancel what is running and wait for its cleanup, so no sandbox or server outlives the API."""
        running = [t for t in self._tasks.values() if not t.done()]
        for t in running:
            t.cancel()
        await asyncio.gather(*running, return_exceptions=True)
