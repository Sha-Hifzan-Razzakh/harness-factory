"""The API app: catalog authoring (every write goes through AuthoringService) and read access to tasks, models,
results, reports and transcripts.

Status codes for a change: 201 created / 200 updated or deleted / 200 dry run / 409 needs confirm (the body says
which identities change; repeat with ?confirm=true) / 422 rejected (the body lists the issues; nothing written).

Security: pasted tool code is executed during discovery, so the API binds to localhost by default and, when a
token is configured (HF_API_TOKEN), every route but /api/health needs `Authorization: Bearer <token>`.
"""

from __future__ import annotations

import hmac
import json
import os
import threading
from pathlib import Path
from typing import Any

from fastapi import Body, Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from harness_factory import settings
from harness_factory.authoring import toolcode
from harness_factory.authoring.fsops import LockTimeout
from harness_factory.authoring.models import KINDS, ChangeResult, Issue, Item
from harness_factory.authoring.service import BUILTIN_SERVERS, DIMENSIONS, AuthoringError, AuthoringService
from harness_factory.authoring.snapshot import LAYER_VARS

CHANGE_CODES = {"applied": 200, "dry_run": 200, "needs_confirm": 409, "rejected": 422}


class State:
    """What the app holds: the authoring service and a Factory that is reloaded when the catalog changes."""

    def __init__(self, service: AuthoringService, results_url: str | None, var_dir: Path) -> None:
        self.service = service
        self.results_url = results_url
        self.var_dir = var_dir.resolve()
        self._factory = None
        self._factory_fp: str | None = None
        self._lock = threading.Lock()

    def factory(self):
        from harness_factory.runner.runner import Factory
        fp = self.service._fingerprint()
        with self._lock:
            if self._factory is None or fp != self._factory_fp:
                url = self.results_url or settings.RESULTS_URL
                if url.startswith("sqlite:///"):                 # a fresh install has no var/ yet
                    Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
                self._factory = Factory.load(store=True, results_url=self.results_url)
                self._factory_fp = fp
            return self._factory


def _change_response(r: ChangeResult, created: bool = False) -> JSONResponse:
    code = 201 if created and r.status == "applied" else CHANGE_CODES[r.status]
    return JSONResponse(status_code=code, content=r.model_dump(mode="json"))


def _authoring_error(e: Exception) -> HTTPException:
    if isinstance(e, LockTimeout):
        return HTTPException(status_code=423, detail=str(e))
    msg = str(e)
    return HTTPException(status_code=404 if msg.startswith("no ") else 400, detail=msg)


def create_app(service: AuthoringService | None = None, token: str | None = None,
               results_url: str | None = None, var_dir: Path | None = None) -> FastAPI:
    """`token`: required bearer token (None = open, for localhost). `results_url`: the store to read
    (default: settings.RESULTS_URL). `var_dir`: where transcripts may be read from (default: settings.VAR)."""
    state = State(service or AuthoringService(), results_url, var_dir or settings.VAR)
    app = FastAPI(title="Harness Factory", version="1", summary="Catalog authoring and results (ADR 0009)")
    app.state.hf = state

    def auth(request: Request) -> None:
        if token is None:
            return
        given = request.headers.get("authorization", "")
        if not hmac.compare_digest(given.encode(), f"Bearer {token}".encode()):
            raise HTTPException(status_code=401, detail="missing or wrong bearer token",
                                headers={"WWW-Authenticate": "Bearer"})

    guarded = [Depends(auth)]

    # ---- meta ------------------------------------------------------------------------------------------------

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "root": str(state.service.root)}

    @app.get("/api/meta", dependencies=guarded)
    def meta() -> dict[str, Any]:
        snap = state.service.snapshot()
        return {"kinds": list(KINDS), "variants": snap.get("variants", []), "dimensions": list(DIMENSIONS),
                "prompt_layers": {k: sorted(v) for k, v in LAYER_VARS.items()},
                "builtin_servers": sorted(BUILTIN_SERVERS), "side_effects": ["READ", "REVERSIBLE", "IRREVERSIBLE"],
                "risks": ["R0", "R1", "R2", "R3"], "param_types": ["str", "int", "float", "bool"]}

    # ---- catalog ---------------------------------------------------------------------------------------------

    @app.get("/api/catalog", dependencies=guarded)
    def catalog(kind: str | None = None) -> list[Item]:
        try:
            return state.service.list(kind)
        except AuthoringError as e:
            raise _authoring_error(e) from e

    @app.get("/api/check", dependencies=guarded)
    def check() -> list[Issue]:
        return state.service.errors()

    @app.get("/api/catalog/{kind}", dependencies=guarded)
    def catalog_kind(kind: str) -> list[Item]:
        return catalog(kind)

    @app.get("/api/catalog/{kind}/{item_id:path}", dependencies=guarded)
    def catalog_item(kind: str, item_id: str) -> Item:
        try:
            return state.service.get(kind, item_id)
        except AuthoringError as e:
            raise _authoring_error(e) from e

    @app.post("/api/catalog/{kind}", dependencies=guarded, response_model=ChangeResult,
              responses={409: {"model": ChangeResult}, 422: {"model": ChangeResult}})
    def create_item(kind: str, payload: dict[str, Any] = Body(...), dry_run: bool = False,  # noqa: B008
                    confirm: bool = False) -> JSONResponse:
        try:
            return _change_response(state.service.create(kind, payload, dry_run=dry_run, confirm=confirm), True)
        except (AuthoringError, LockTimeout) as e:
            raise _authoring_error(e) from e

    @app.put("/api/catalog/{kind}/{item_id:path}", dependencies=guarded, response_model=ChangeResult,
             responses={409: {"model": ChangeResult}, 422: {"model": ChangeResult}})
    def update_item(kind: str, item_id: str, payload: dict[str, Any] = Body(...),  # noqa: B008
                    dry_run: bool = False, confirm: bool = False) -> JSONResponse:
        key = "ref" if kind.rstrip("s") == "prompt" else "id"
        if payload.get(key, item_id) != item_id:
            raise HTTPException(status_code=400, detail=f"payload {key} {payload.get(key)!r} is not {item_id!r}")
        try:
            return _change_response(state.service.update(kind, {**payload, key: item_id}, dry_run=dry_run,
                                                         confirm=confirm))
        except (AuthoringError, LockTimeout) as e:
            raise _authoring_error(e) from e

    @app.delete("/api/catalog/{kind}/{item_id:path}", dependencies=guarded, response_model=ChangeResult,
                responses={409: {"model": ChangeResult}, 422: {"model": ChangeResult}})
    def delete_item(kind: str, item_id: str, dry_run: bool = False, confirm: bool = False) -> JSONResponse:
        try:
            return _change_response(state.service.delete(kind, item_id, dry_run=dry_run, confirm=confirm))
        except (AuthoringError, LockTimeout) as e:
            raise _authoring_error(e) from e

    @app.post("/api/servers/stub", dependencies=guarded)
    def server_stub(payload: dict[str, Any] = Body(...)) -> dict[str, str]:  # noqa: B008
        """The module a local server would get without code: a starting point to edit."""
        from pydantic import ValidationError
        try:
            tools = [toolcode.ToolDef.model_validate(t) for t in payload.get("tools") or []]
        except ValidationError as e:
            raise HTTPException(status_code=422, detail=json.loads(e.json())) from e
        sid = str(payload.get("id") or "my_tools")
        return {"path": toolcode.module_path(sid),
                "code": toolcode.stub(sid, str(payload.get("instructions") or ""), tools)}

    # ---- what experiments are made of --------------------------------------------------------------------------

    @app.get("/api/tasks", dependencies=guarded)
    def tasks() -> list[dict[str, Any]]:
        from harness_factory.provision.provisioner import task_class
        f = state.factory()
        return [{"id": t.id, "version": t.version, "domain": t.domain, "split": t.split.value, "status": t.status,
                 "task_class": task_class(t, f.declared), "allowed_tools": list(t.allowed_tools),
                 "instruction": t.instruction, "tags": list(t.tags)}
                for t in f.tasks.all(include_held_out=True)]

    @app.get("/api/models", dependencies=guarded)
    def models() -> list[dict[str, Any]]:
        f = state.factory()
        out = []
        for p in f.providers.providers:
            out.append({"provider": p.id, "kind": p.kind, "status": p.status, "auth_env": p.auth_env,
                        "key_set": p.auth_env is None or bool(os.environ.get(p.auth_env)),
                        "models": [{"canonical": m.canonical, "provider_model_id": m.provider_model_id,
                                    "free": m.free, "price_in": m.price_in, "price_out": m.price_out}
                                   for m in p.models]})
        return out

    @app.get("/api/faults", dependencies=guarded)
    def faults() -> list[dict[str, Any]]:
        return [{"id": fid, "description": f.description} for fid, f in state.factory().faults.items()]

    # ---- results ---------------------------------------------------------------------------------------------

    def _rows_and_baseline(baseline: str | None):
        from harness_factory.results.cost import CostBaseline
        f = state.factory()
        rows = f.store.rows()
        if baseline:
            b = f.store.get_baseline(baseline)
            if b is None:
                raise HTTPException(status_code=404, detail=f"no baseline {baseline!r}")
            return f, rows, b
        return f, rows, CostBaseline.from_rows(rows, note="live")

    @app.get("/api/results", dependencies=guarded)
    def results(limit: int = Query(50, ge=1, le=1000)) -> list[dict[str, Any]]:
        return [_jsonable(r) for r in state.factory().store.recent(limit)]

    @app.get("/api/results/cells", dependencies=guarded)
    def cells(baseline: str | None = None) -> dict[str, Any]:
        f, _rows, b = _rows_and_baseline(baseline)
        b, agg = f.store.aggregate(b)
        return {"baseline": _jsonable(b.model_dump(mode="json")), "cells": [c.model_dump(mode="json") for c in agg]}

    @app.get("/api/baselines", dependencies=guarded)
    def baselines() -> list[dict[str, Any]]:
        return [b.model_dump(mode="json") for b in state.factory().store.list_baselines()]

    @app.get("/api/report", dependencies=guarded)
    def report(baseline: str | None = None, models: str | None = None,
               title: str = "Campaign report") -> dict[str, str]:
        from harness_factory.results.report import build_report
        f, rows, b = _rows_and_baseline(baseline)
        return {"markdown": build_report(f, rows, b, title=title, models=models.split(",") if models else None)}

    @app.get("/api/runs/{run_id}/transcript", dependencies=guarded)
    def transcript(run_id: int) -> dict[str, Any]:
        row = next((r for r in state.factory().store.rows(latest=False) if r["id"] == run_id), None)
        if row is None:
            raise HTTPException(status_code=404, detail=f"no run {run_id}")
        path = row.get("transcript_path")
        if not path:
            raise HTTPException(status_code=404, detail=f"run {run_id} has no transcript")
        p = Path(path).resolve()
        if not p.is_relative_to(state.var_dir) or not p.is_file():
            raise HTTPException(status_code=404, detail=f"transcript of run {run_id} is not readable here")
        return json.loads(p.read_text())

    return app


def _jsonable(row: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(row, default=str))


def app_from_env() -> FastAPI:
    """For `uvicorn harness_factory.api.app:app_from_env --factory`."""
    return create_app(token=os.environ.get("HF_API_TOKEN") or None)
