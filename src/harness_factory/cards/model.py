"""Spec cards: one readable, serialisable summary per building block and per experiment.

A card is data first (pydantic, JSON for the API and UI) and text second (`render.to_markdown`). Building a card
never runs anything and never writes to the catalog; it reads the same objects a run reads (Factory), so what a
card says is what a run would do.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

CardKind = Literal["model", "harness", "task", "tool", "fault", "spec"]


class Table(BaseModel):
    headers: list[str]
    rows: list[list[str]]


class Section(BaseModel):
    title: str
    fields: list[tuple[str, str]] = Field(default_factory=list)   # label -> value, shown as a two-column list
    table: Table | None = None
    text: str | None = None                                        # preformatted (prompts, YAML)
    note: str | None = None                                        # one line under the section


class Card(BaseModel):
    kind: CardKind
    id: str
    title: str
    summary: str                        # one line: what this is
    hash: str | None = None             # content hash, when the item has one
    sections: list[Section] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)       # problems that block or weaken it
    verdict: Literal["READY", "BLOCKED"] | None = None    # spec cards only
    result: dict | None = None                             # spec cards after a run: status, scores, paths

    def section(self, title: str) -> Section | None:
        return next((s for s in self.sections if s.title == title), None)
