"""Catalog history over the API: versions, diffs, restore, purge, tags, the activity feed, sync (changes made
outside authoring), archived items and results per version. Every write goes through the authoring service."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field


class RestoreRequest(BaseModel):
    version: int = Field(ge=1)
    note: str = ""
    author: str | None = None


class PartsRequest(BaseModel):
    parts: dict[str, str] = Field(min_length=1)    # part -> its new text (see GET .../version/current)
    note: str = ""
    author: str | None = None


class TagRequest(BaseModel):
    version: int = Field(ge=1)
    tags: list[str] = Field(min_length=1)          # "baseline", "best"; a leading "-" removes a tag
    author: str | None = None


def add_routes(app: FastAPI, state, guarded: list) -> None:
    from harness_factory.api.app import _authoring_error, _change_response
    from harness_factory.authoring.fsops import LockTimeout
    from harness_factory.authoring.service import AuthoringError

    def svc():
        return state.service

    if state.service.results_url is None and state.results_url:      # results per version read the API's store
        state.service.results_url = state.results_url

    def _guard(fn, *args, **kw):
        try:
            return fn(*args, **kw)
        except (AuthoringError, LockTimeout) as e:
            raise _authoring_error(e) from e
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e).strip("'\"")) from e

    @app.get("/api/whoami", dependencies=guarded)
    def whoami() -> dict[str, str]:
        """The author version entries name by default (HF_AUTHOR, else the OS user)."""
        return {"author": svc().author}

    @app.get("/api/payload/{kind}/{item_id:path}", dependencies=guarded)
    def payload(kind: str, item_id: str) -> dict[str, Any]:
        """The item as its create/update payload: what an editor starts from."""
        return _guard(svc().payload, kind, item_id)

    @app.get("/api/activity", dependencies=guarded)
    def activity(limit: int = 200, kind: str | None = None) -> list[dict[str, Any]]:
        """Every recorded change, newest first."""
        return _guard(svc().activity, limit, kind)

    @app.post("/api/sync", dependencies=guarded)
    def sync(author: str | None = None) -> list[dict[str, Any]]:
        """Record changes made outside authoring (editor, git pull) and first versions of items with no history."""
        return _guard(svc().sync, author)

    @app.get("/api/archived", dependencies=guarded)
    def archived() -> list[dict[str, Any]]:
        return svc().archived_items()

    @app.get("/api/history/{kind}/{item_id:path}/diff", dependencies=guarded)
    def diff(kind: str, item_id: str, a: str, b: str = "current") -> dict[str, str]:
        """Unified diff between two versions (a number, or 'current')."""
        return {"diff": _guard(svc().diff_versions, kind, item_id, a, b)}

    @app.get("/api/history/{kind}/{item_id:path}/version/{version}", dependencies=guarded)
    def version(kind: str, item_id: str, version: str) -> dict[str, Any]:
        """A version's files (or 'current')."""
        return {"parts": _guard(svc().version_parts, kind, item_id, version)}

    @app.get("/api/history/{kind}/{item_id:path}/results", dependencies=guarded)
    def results(kind: str, item_id: str) -> dict[str, Any]:
        """Scored runs per version of this item: n, mean E0, SUCCESS share, and per recipe (or model)."""
        return _guard(svc().results_by_version, kind, item_id)

    @app.post("/api/history/{kind}/{item_id:path}/restore", dependencies=guarded)
    def restore(kind: str, item_id: str, req: RestoreRequest, dry_run: bool = False,
                confirm: bool = False) -> JSONResponse:
        """Restore a version as a new version, checked like any change (409 asks to confirm an identity change)."""
        return _change_response(_guard(svc().restore, kind, item_id, req.version, req.note, dry_run, confirm,
                                       req.author))

    @app.put("/api/parts/{kind}/{item_id:path}", dependencies=guarded)
    def update_parts(kind: str, item_id: str, req: PartsRequest, dry_run: bool = False,
                     confirm: bool = False) -> JSONResponse:
        """Edit an item's files as text (prompts, skills, recipes, faults, models), checked like any change."""
        return _change_response(_guard(svc().update_parts, kind, item_id, req.parts, req.note, dry_run, confirm,
                                       req.author))

    @app.post("/api/history/{kind}/{item_id:path}/tags", dependencies=guarded)
    def tags(kind: str, item_id: str, req: TagRequest) -> dict[str, Any]:
        return _guard(svc().tag, kind, item_id, req.version, req.tags, req.author)

    @app.delete("/api/history/{kind}/{item_id:path}", dependencies=guarded)
    def purge(kind: str, item_id: str, author: str | None = None) -> JSONResponse:
        """Remove an archived item's history for good; refused if any stored run used it."""
        return _change_response(_guard(svc().purge, kind, item_id, author))

    @app.get("/api/history/{kind}/{item_id:path}", dependencies=guarded)
    def history(kind: str, item_id: str) -> dict[str, Any]:
        """The item's versions, newest first, with tags; whether it was changed outside authoring since."""
        return _guard(svc().history, kind, item_id)
