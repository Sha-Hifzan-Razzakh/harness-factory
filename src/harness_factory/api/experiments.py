"""Experiments and the dashboard over the API.

An experiment is a campaign with a name, the question it answers and, later, its conclusion. It is saved under
settings.EXPERIMENTS_DIR (one JSON file each) with the exact runs it planned (RunKeys), so it survives API restarts:
campaigns themselves live in memory, but their runs are in the results store, and an experiment finds them by key.
"""

from __future__ import annotations

import json
import random
import statistics
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from harness_factory import settings
from harness_factory.api.campaigns import CampaignInvalid, CampaignRequest, preflight, specs_for


class ExperimentIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    hypothesis: str = ""                 # the question it answers / what you expect
    request: CampaignRequest
    author: str | None = None


class ExperimentPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=120)
    hypothesis: str | None = None
    conclusion: str | None = None        # what you learned
    tags: list[str] | None = None


class RerunIn(BaseModel):
    name: str | None = None
    resume: bool = True                  # skip runs whose exact identity already has a scored result


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class ExperimentStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def path(self, eid: str) -> Path:
        p = (self.root / f"{eid}.json").resolve()
        if p.parent != self.root.resolve():
            raise HTTPException(status_code=404, detail=f"no experiment {eid!r}")
        return p

    def save(self, rec: dict) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self.path(rec["id"]).with_suffix(".tmp")
        tmp.write_text(json.dumps(rec, indent=1), encoding="utf-8")
        tmp.replace(self.path(rec["id"]))

    def get(self, eid: str) -> dict:
        p = self.path(eid)
        if not p.is_file():
            raise HTTPException(status_code=404, detail=f"no experiment {eid!r}")
        return json.loads(p.read_text(encoding="utf-8"))

    def all(self) -> list[dict]:
        if not self.root.is_dir():
            return []
        out = [json.loads(p.read_text(encoding="utf-8")) for p in self.root.glob("*.json")]
        return sorted(out, key=lambda r: r["created_at"], reverse=True)


def _latest_by_key(rows: list[dict]) -> dict[str, dict]:
    """Newest scored row per RunKey, else the newest row (an INCOMPLETE/ERROR never hides a scored one)."""
    from harness_factory.results.store import UNSCORED
    out: dict[str, dict] = {}
    for r in sorted(rows, key=lambda r: r["id"]):
        cur = out.get(r["run_key"])
        if cur is None or r["status"] not in UNSCORED or cur["status"] in UNSCORED:
            out[r["run_key"]] = r
    return out


def _stats(rs: list[dict]) -> dict[str, Any]:
    from harness_factory.results.store import UNSCORED
    scored = [r for r in rs if r["status"] not in UNSCORED]
    st = Counter(r["status"] for r in rs)
    return {"n": len(rs), "scored": len(scored), "statuses": dict(st),
            "mean_e0": round(statistics.fmean(r["composite"] for r in scored), 4) if scored else None,
            "success": round(st.get("SUCCESS", 0) / len(scored), 3) if scored else None,
            "tokens": round(statistics.fmean(r["tokens_in"] + r["tokens_out"] for r in scored)) if scored else None,
            "usd": round(sum(r["usd"] for r in rs), 6), "wall_s": round(sum(r["wall_s"] for r in rs), 1)}


def _paired(deltas: list[float]) -> dict[str, Any]:
    """Mean difference with a percentile-bootstrap 95% interval (fixed seed: same answer every time)."""
    if not deltas:
        return {"n": 0}
    rng = random.Random(7)
    means = sorted(statistics.fmean(rng.choices(deltas, k=len(deltas))) for _ in range(1000))
    lo, hi = means[25], means[974]
    wins, losses = sum(d > 1e-9 for d in deltas), sum(d < -1e-9 for d in deltas)
    verdict = "better" if lo > 0 else "worse" if hi < 0 else "no clear difference"
    return {"n": len(deltas), "mean_delta": round(statistics.fmean(deltas), 4), "ci95": [round(lo, 4), round(hi, 4)],
            "wins": wins, "losses": losses, "ties": len(deltas) - wins - losses, "verdict": verdict}


def add_routes(app: FastAPI, state, guarded: list) -> None:
    manager = app.state.campaigns
    own = state.var_dir == settings.VAR.resolve()
    store = ExperimentStore(settings.EXPERIMENTS_DIR if own else state.var_dir / "experiments")

    def rows() -> list[dict]:
        f = state.factory()
        return f.store.rows(latest=False) if f.store else []

    def refresh(rec: dict) -> dict:
        """Bring the saved state up to date with the live campaign (or mark it interrupted after a restart)."""
        c = manager.campaigns.get(rec.get("campaign_id"))
        changed = False
        if c is not None:
            if c.state != rec["state"] or (c.result and rec.get("result") != c.result):
                rec.update(state=c.state, finished_at=c.finished_at, result=c.result, error=c.error)
                changed = True
        elif rec["state"] == "running":
            rec.update(state="interrupted", error="the API stopped while it ran; finished runs are kept "
                                                  "(Run again with resume finishes the rest)")
            changed = True
        if changed:
            store.save(rec)
        return rec

    def summary(rec: dict, by_key: dict[str, dict]) -> dict:
        mine = [by_key[k] for k in rec["run_keys"] if k in by_key]
        s = _stats(mine)
        recipes: dict[str, list[dict]] = defaultdict(list)
        for r in mine:
            recipes[r["recipe_id"]].append(r)
        ranked = sorted(((rid, _stats(rs)["mean_e0"]) for rid, rs in recipes.items()), key=lambda x: -(x[1] or 0))
        return {**{k: rec.get(k) for k in ("id", "name", "hypothesis", "conclusion", "tags", "author", "created_at",
                                           "finished_at", "state", "error")},
                "request": rec["request"], "planned": len(rec["run_keys"]), "done": len(mine), "stats": s,
                "best": {"recipe": ranked[0][0], "mean_e0": ranked[0][1]} if ranked and ranked[0][1] is not None else None,
                "progress": manager.progress(manager.campaigns[rec["campaign_id"]])
                if rec.get("campaign_id") in manager.campaigns else None}

    def start(body: ExperimentIn) -> dict:
        from harness_factory.provision.provisioner import run_key_for
        f = state.factory()
        try:
            specs = specs_for(f, body.request)
        except CampaignInvalid as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        plan_ = preflight(f, body.request, specs)
        if plan_.problems:
            raise HTTPException(status_code=409, detail={"problems": plan_.problems, "plan": plan_.model_dump()})
        if not plan_.will_run:
            raise HTTPException(status_code=409, detail={"problems": [("nothing to run: every run already has a scored "
                                                                       "result (turn resume off to run them again)")],
                                                          "plan": plan_.model_dump()})
        keys = [run_key_for(f.tasks.get(s.task_id), f.recipes[s.recipe_id], s.canonical_model, s.provider, s.seed,
                            declared=f.declared, providers=f.providers, fault=f.fault(s.fault)).as_str() for s in specs]
        c = manager.start(f, body.request, specs, plan_)
        versions = {}
        for rid in body.request.recipes:
            last = state.service.journal.latest("recipe", rid)
            versions[rid] = {"hash": f.recipes[rid].content_hash(), "version": last.version if last else None}
        rec = {"id": f"{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}-{c.id[:4]}", "name": body.name,
               "hypothesis": body.hypothesis, "conclusion": "", "tags": [], "author": body.author or state.service.author,
               "created_at": _now(), "finished_at": None, "state": "running", "error": None, "result": None,
               "campaign_id": c.id, "request": body.request.model_dump(), "plan": plan_.model_dump(),
               "specs": [s.model_dump() for s in specs], "run_keys": keys, "recipes": versions}
        store.save(rec)
        return rec

    @app.post("/api/experiments", dependencies=guarded, status_code=202)
    async def create(body: ExperimentIn) -> dict[str, Any]:
        """Start an experiment: a campaign with a name and the question it answers, saved with its planned runs.
        Async because the campaign is started on the API's event loop."""
        return start(body)

    @app.get("/api/experiments", dependencies=guarded)
    def list_experiments() -> list[dict[str, Any]]:
        by_key = _latest_by_key(rows())
        return [summary(refresh(r), by_key) for r in store.all()]

    @app.get("/api/experiments/{eid}", dependencies=guarded)
    def detail(eid: str) -> dict[str, Any]:
        """Everything about one experiment: settings, progress, per harness x model results, paired differences
        against the first harness, the task x harness grid, and harnesses changed since it ran."""
        rec = refresh(store.get(eid))
        all_rows = rows()
        by_key = _latest_by_key(all_rows)
        f = state.factory()
        runs = []
        for spec, key in zip(rec["specs"], rec["run_keys"]):
            r = by_key.get(key)
            runs.append({**spec, "run_key": key, "row": None if r is None else {
                k: r[k] for k in ("id", "status", "composite", "outcome", "trajectory", "model_calls", "tool_calls",
                                  "tokens_in", "tokens_out", "usd", "wall_s")}})
        done = [x for x in runs if x["row"]]
        groups: dict[tuple, list[dict]] = defaultdict(list)
        for x in done:
            groups[(x["recipe_id"], x["canonical_model"], x["fault"])].append(by_key[x["run_key"]])
        per = [{"recipe": k[0], "model": k[1], "fault": k[2], **_stats(v)} for k, v in sorted(groups.items(),
                                                                                         key=lambda kv: str(kv[0]))]
        grid: dict[tuple, list[dict]] = defaultdict(list)
        for x in done:
            grid[(x["task_id"], x["recipe_id"], x["canonical_model"], x["fault"])].append(by_key[x["run_key"]])
        cells = [{"task": k[0], "recipe": k[1], "model": k[2], "fault": k[3], **_stats(v),
                  "run_ids": [r["id"] for r in v]} for k, v in grid.items()]
        base = rec["request"]["recipes"][0]
        scored = {(x["task_id"], x["recipe_id"], x["canonical_model"], x["seed"], x["fault"]): x["row"]["composite"]
                  for x in done if x["row"]["status"] not in ("INCOMPLETE", "ERROR")}
        compare = []
        for rid in rec["request"]["recipes"][1:]:
            for m in sorted({x["canonical_model"] for x in runs}):
                deltas = [v - scored[(t, base, mm, sd, fl)] for (t, r2, mm, sd, fl), v in scored.items()
                          if r2 == rid and mm == m and (t, base, mm, sd, fl) in scored]
                compare.append({"base": base, "recipe": rid, "model": m, **_paired(deltas)})
        changed = []
        for rid, v in (rec.get("recipes") or {}).items():
            cur = f.recipes.get(rid)
            now_hash = cur.content_hash() if cur else None
            if now_hash != v["hash"]:
                last = state.service.journal.latest("recipe", rid)
                changed.append({"recipe": rid, "then": v, "now": {"hash": now_hash,
                                                                  "version": last.version if last else None}})
        out = summary(rec, by_key)
        return {**out, "plan": rec["plan"], "runs": runs, "per": per, "cells": cells, "compare": compare,
                "changed_since": changed, "recipes": rec.get("recipes"), "result": rec.get("result")}

    @app.patch("/api/experiments/{eid}", dependencies=guarded)
    def update(eid: str, body: ExperimentPatch) -> dict[str, Any]:
        rec = store.get(eid)
        rec.update({k: v for k, v in body.model_dump().items() if v is not None})
        store.save(rec)
        return {k: rec[k] for k in ("id", "name", "hypothesis", "conclusion", "tags")}

    @app.post("/api/experiments/{eid}/rerun", dependencies=guarded, status_code=202)
    async def rerun(eid: str, body: RerunIn) -> dict[str, Any]:
        """The same settings again, with the catalog as it is now (changed harnesses get new runs)."""
        rec = store.get(eid)
        req = CampaignRequest.model_validate({**rec["request"], "resume": body.resume})
        return start(ExperimentIn(name=body.name or f"{rec['name']} (again)", hypothesis=rec["hypothesis"], request=req))

    @app.delete("/api/experiments/{eid}/run", dependencies=guarded, status_code=202)
    def cancel(eid: str) -> dict[str, Any]:
        rec = store.get(eid)
        if not manager.cancel(rec.get("campaign_id") or ""):
            raise HTTPException(status_code=409, detail="not running")
        return {"id": eid, "state": "cancelling"}

    @app.get("/api/dashboard", dependencies=guarded)
    def dashboard(days: int = 30) -> dict[str, Any]:
        """The whole picture: totals, runs per day, harness and model leaderboards, the best harness per task class,
        what fails most, recent experiments and recent catalog changes."""
        from harness_factory.results.store import UNSCORED
        all_rows = rows()
        latest = list(_latest_by_key(all_rows).values())
        scored = [r for r in latest if r["status"] not in UNSCORED and not r.get("fault")]
        today = datetime.now(UTC).date()
        start_day = today - timedelta(days=days - 1)
        per_day: dict[str, list[dict]] = {(start_day + timedelta(d)).isoformat(): [] for d in range(days)}
        for r in all_rows:
            day = r["created_at"].date().isoformat() if hasattr(r["created_at"], "date") else str(r["created_at"])[:10]
            if day in per_day:
                per_day[day].append(r)
        timeline = [{"day": d, "statuses": dict(Counter(r["status"] for r in rs)),
                     "mean_e0": round(statistics.fmean(r["composite"] for r in rs if r["status"] not in UNSCORED), 4)
                     if any(r["status"] not in UNSCORED for r in rs) else None} for d, rs in per_day.items()]

        def board(key: str) -> list[dict]:
            g: dict[str, list[dict]] = defaultdict(list)
            for r in scored:
                g[r[key]].append(r)
            return sorted(({"id": k, **_stats(v)} for k, v in g.items()), key=lambda x: -(x["mean_e0"] or 0))

        classes: dict[str, dict[tuple, list[dict]]] = defaultdict(lambda: defaultdict(list))
        for r in scored:
            classes[r["task_class"]][(r["recipe_id"], r["canonical_model"])].append(r)
        best = []
        for cls, g in sorted(classes.items()):
            ranked = sorted(((k, _stats(v)) for k, v in g.items()), key=lambda kv: -(kv[1]["mean_e0"] or 0))
            (rid, m), s = ranked[0]
            best.append({"task_class": cls, "recipe": rid, "model": m, "mean_e0": s["mean_e0"], "n": s["n"],
                         "runner_up": {"recipe": ranked[1][0][0], "model": ranked[1][0][1],
                                       "mean_e0": ranked[1][1]["mean_e0"]} if len(ranked) > 1 else None,
                         "candidates": len(ranked)})
        failing = [r for r in latest if r["status"] not in ("SUCCESS",) and r["status"] not in UNSCORED]
        checks = Counter(f"{r['task_version'].split('@')[0]}: {c['id']}" for r in failing for g in (r.get("grades") or [])
                         for c in g.get("checks", []) if not c.get("passed"))
        violations = Counter(v.get("kind") for r in failing for v in (r.get("violations") or []))
        by_key = _latest_by_key(all_rows)
        exps = [summary(refresh(r), by_key) for r in store.all()[:6]]
        changes = [e for e in state.service.activity(200) if e["action"] not in ("baseline", "tag")][:8]
        st = Counter(r["status"] for r in all_rows)
        return {
            "totals": {"runs": len(all_rows), "distinct": len(latest), "scored": len(scored),
                       "success": round(sum(r["status"] == "SUCCESS" for r in scored) / len(scored), 3) if scored else None,
                       "mean_e0": round(statistics.fmean(r["composite"] for r in scored), 4) if scored else None,
                       "statuses": dict(st), "tokens": sum(r["tokens_in"] + r["tokens_out"] for r in all_rows),
                       "usd": round(sum(r["usd"] for r in all_rows), 4),
                       "recipes": len({r["recipe_id"] for r in all_rows}), "models": len({r["canonical_model"] for r in all_rows}),
                       "tasks": len({r["task_version"] for r in all_rows}), "experiments": len(store.all())},
            "timeline": timeline, "harnesses": board("recipe_id")[:15], "models": board("canonical_model"),
            "best_per_class": best, "failing_checks": checks.most_common(8), "violations": violations.most_common(6),
            "experiments": exps, "changes": changes,
        }
