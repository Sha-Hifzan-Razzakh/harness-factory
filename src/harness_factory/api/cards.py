"""Cards and options over the API: what the UI's Playbook, "What we can test with", Cards and Build pages read.
Building a card runs nothing; the final spec card reads the run the campaign stored."""

from __future__ import annotations

import json
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field


class SpecRequest(BaseModel):
    task: str
    recipe: str
    model: str
    provider: str
    seed: int = Field(0, ge=0)
    fault: str | None = None
    prompts: bool = True


def add_routes(app: FastAPI, state, guarded: list) -> None:
    from harness_factory.cards import build
    from harness_factory.cards.options import options
    from harness_factory.cards.render import cards_dir, save, to_markdown

    builders = {"model": build.model_card, "harness": build.harness_card, "task": build.task_card,
                "tool": build.tool_card, "fault": build.fault_card}

    def _out(card) -> dict[str, Any]:
        return {**card.model_dump(mode="json"), "markdown": to_markdown(card)}

    def _spec(req: SpecRequest):
        try:
            return build.spec_card(state.factory(), req.task, req.recipe, req.model, req.provider, req.seed,
                                   None if req.fault in (None, "", "none") else req.fault, req.prompts)
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e).strip("'\"")) from e

    @app.get("/api/options", dependencies=guarded)
    def get_options() -> dict[str, Any]:
        """Everything an experiment can be built from: shapes, recipes, recipe fields, models, tools, skills,
        prompts, faults, runtimes, tasks, check kinds, perturbation types, retrieval modes."""
        return options(state.factory())

    @app.post("/api/cards/spec", dependencies=guarded)
    def spec_card(req: SpecRequest) -> dict[str, Any]:
        """The spec card of one experiment, with its READY / BLOCKED verdict. Runs nothing."""
        return _out(_spec(req))

    @app.post("/api/cards/spec/final", dependencies=guarded)
    def final_card(req: SpecRequest) -> dict[str, Any]:
        """The final spec card: the spec card plus the newest stored run with its RunKey, saved to var/cards/spec."""
        card = _spec(req)
        row = build.latest_row(state.factory(), card)
        if row is None:
            raise HTTPException(status_code=404, detail=f"no stored run with run key {card.id} yet")
        card = build.attach_row(card, row)
        return {**_out(card), "saved": str(save(card, state.var_dir))}

    @app.get("/api/cards/saved", dependencies=guarded)
    def saved_cards() -> list[dict[str, Any]]:
        """Saved final spec cards, newest first."""
        d = cards_dir(state.var_dir) / "spec"
        out = []
        for p in sorted(d.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True) if d.is_dir() else []:
            c = json.loads(p.read_text(encoding="utf-8"))
            out.append({"name": p.stem, "title": c["title"], "summary": c["summary"], "verdict": c.get("verdict"),
                        "status": (c.get("result") or {}).get("status"), "E0": (c.get("result") or {}).get("E0")})
        return out

    @app.get("/api/cards/saved/{name}", dependencies=guarded)
    def saved_card(name: str) -> dict[str, Any]:
        p = (cards_dir(state.var_dir) / "spec" / f"{name}.json").resolve()
        if p.parent != (cards_dir(state.var_dir) / "spec").resolve() or not p.is_file():
            raise HTTPException(status_code=404, detail=f"no saved card {name!r}")
        c = json.loads(p.read_text(encoding="utf-8"))
        return {**c, "markdown": p.with_suffix(".md").read_text(encoding="utf-8")}

    @app.get("/api/cards/{kind}/{item_id:path}", dependencies=guarded)
    def card(kind: str, item_id: str) -> dict[str, Any]:
        """A building-block card: model, harness, task, tool or fault."""
        if kind not in builders:
            raise HTTPException(status_code=404, detail=f"unknown card kind {kind!r}; known: {', '.join(builders)}")
        try:
            return _out(builders[kind](state.factory(), item_id))
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e).strip("'\"")) from e
