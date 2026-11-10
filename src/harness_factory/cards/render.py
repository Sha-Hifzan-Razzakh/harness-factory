"""Cards as Markdown (files, the UI) and saved cards under var/cards/."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from harness_factory import settings
from harness_factory.cards.model import Card


def _cell(s: str) -> str:
    return str(s).replace("|", "\\|").replace("\n", " ")


def to_markdown(card: Card) -> str:
    out = [f"# {card.title}", "", card.summary]
    meta = [f"kind: {card.kind}"] + ([f"hash: `{card.hash}`"] if card.hash else [])
    if card.verdict:
        meta.insert(0, f"**{card.verdict}**")
    out += ["", " · ".join(meta)]
    if card.issues:
        out += ["", "## Issues", ""] + [f"- {i}" for i in card.issues]
    for s in card.sections:
        out += ["", f"## {s.title}", ""]
        if s.fields:
            out += ["| field | value |", "|---|---|"] + [f"| {_cell(k)} | {_cell(v)} |" for k, v in s.fields] + [""]
        if s.table is not None and s.table.rows:
            out += ["| " + " | ".join(map(_cell, s.table.headers)) + " |",
                    "|" + "---|" * len(s.table.headers)]
            out += ["| " + " | ".join(map(_cell, r)) + " |" for r in s.table.rows] + [""]
        if s.text:
            out += ["```text", s.text, "```", ""]
        if s.note:
            out += [f"_{s.note}_", ""]
    return "\n".join(out).rstrip() + "\n"


def cards_dir(var: Path | None = None) -> Path:
    return (var or settings.VAR) / "cards"


def save(card: Card, var: Path | None = None) -> Path:
    """Write <kind>/<id>[-<hash>].md and .json; a spec card with a result is stamped with the time it ran."""
    d = cards_dir(var) / card.kind
    d.mkdir(parents=True, exist_ok=True)
    stem = card.id if card.kind == "spec" or not card.hash else f"{card.id}-{card.hash}"
    if card.result is not None:
        stem += "-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    md = d / f"{stem}.md"
    md.write_text(to_markdown(card), encoding="utf-8")
    md.with_suffix(".json").write_text(json.dumps(card.model_dump(mode="json"), indent=2), encoding="utf-8")
    return md
