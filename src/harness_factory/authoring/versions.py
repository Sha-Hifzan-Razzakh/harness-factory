"""Version history of catalog items: an append-only journal plus content-addressed blobs.

Every change made through the authoring service (create, update, archive, restore) is one entry: who, when, why,
the item's files before and after, its hash before and after, and the recipes whose identity it changed. Changes
made outside it (an editor, `git pull`) are found by `AuthoringService.sync()` and recorded as `external`.
Nothing here commits to git (ADR 0009): files stay the source of truth, this records how they got there.

Layout, under settings.HISTORY_DIR (default var/catalog_history/; <root>/var/catalog_history for another root):
    journal.jsonl            one JSON entry per line, in order
    blobs/<sha256>.txt       file contents, written once
"""

from __future__ import annotations

import difflib
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

Action = Literal["baseline", "create", "update", "archive", "restore", "external", "purge", "dependency", "tag"]
CONTENT_ACTIONS = ("baseline", "create", "update", "archive", "restore", "external", "purge", "dependency")


class Entry(BaseModel):
    seq: int
    at: str                                          # UTC ISO time
    author: str
    kind: str
    id: str
    action: Action
    version: int | None = None                       # the item's version after this entry (None: tag, purge)
    hash_before: str | None = None
    hash_after: str | None = None                    # the item's identity hash (recipe hash for a recipe)
    parts_before: dict[str, str] = Field(default_factory=dict)   # part -> blob sha
    parts_after: dict[str, str] = Field(default_factory=dict)
    note: str = ""                                   # why
    source: str = "authoring"                        # authoring | external | import
    recipe_hashes: dict[str, str] = Field(default_factory=dict)  # recipes this version produced: id -> hash
    impact: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)    # tag entries: the tags added; with a leading '-' removed
    cause: str | None = None                         # dependency entries: "skill sql-analysis v3"


def now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class Journal:
    def __init__(self, history_dir: Path) -> None:
        self.dir = history_dir
        self.file = self.dir / "journal.jsonl"
        self.blobs = self.dir / "blobs"

    # ---- blobs ---------------------------------------------------------------------------------------------

    def put(self, text: str) -> str:
        sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
        p = self.blobs / f"{sha}.txt"
        if not p.is_file():
            self.blobs.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8", newline="")
        return sha

    def get(self, sha: str) -> str:
        return (self.blobs / f"{sha}.txt").read_text(encoding="utf-8")

    def put_parts(self, parts: dict[str, str]) -> dict[str, str]:
        return {k: self.put(v) for k, v in parts.items()}

    def get_parts(self, refs: dict[str, str]) -> dict[str, str]:
        return {k: self.get(v) for k, v in refs.items()}

    # ---- entries -------------------------------------------------------------------------------------------

    def entries(self, kind: str | None = None, item_id: str | None = None) -> list[Entry]:
        if not self.file.is_file():
            return []
        out = []
        for line in self.file.read_text(encoding="utf-8").splitlines():
            if line.strip():
                e = Entry.model_validate_json(line)
                if (kind is None or e.kind == kind) and (item_id is None or e.id == item_id):
                    out.append(e)
        return out

    def append(self, **fields: Any) -> Entry:
        """Callers hold the catalog lock, so sequence numbers and versions can't race."""
        all_entries = self.entries()
        seq = (all_entries[-1].seq + 1) if all_entries else 1
        e = Entry(seq=seq, at=now(), **fields)
        self.dir.mkdir(parents=True, exist_ok=True)
        with self.file.open("a", encoding="utf-8", newline="\n") as f:
            f.write(e.model_dump_json() + "\n")
        return e

    def versions(self, kind: str, item_id: str) -> list[Entry]:
        """Entries that set a content version, oldest first."""
        return [e for e in self.entries(kind, item_id) if e.action in CONTENT_ACTIONS and e.version is not None]

    def latest(self, kind: str, item_id: str) -> Entry | None:
        v = self.versions(kind, item_id)
        return v[-1] if v else None

    def next_version(self, kind: str, item_id: str) -> int:
        last = self.latest(kind, item_id)
        return (last.version or 0) + 1 if last else 1

    def version(self, kind: str, item_id: str, n: int) -> Entry:
        for e in self.versions(kind, item_id):
            if e.version == n:
                return e
        raise KeyError(f"{kind} {item_id} has no version {n}")

    def tags(self, kind: str, item_id: str) -> dict[int, list[str]]:
        """version -> its current tags."""
        out: dict[int, list[str]] = {}
        for e in self.entries(kind, item_id):
            if e.action != "tag" or e.version is None:
                continue
            cur = out.setdefault(e.version, [])
            for t in e.tags:
                if t.startswith("-"):
                    if t[1:] in cur:
                        cur.remove(t[1:])
                elif t not in cur:
                    cur.append(t)
        return {k: v for k, v in out.items() if v}

    def archived(self) -> list[Entry]:
        """Items whose latest content entry is an archive (and not purged since)."""
        last: dict[tuple[str, str], Entry] = {}
        for e in self.entries():
            if e.action in CONTENT_ACTIONS:
                last[(e.kind, e.id)] = e
        return [e for e in last.values() if e.action == "archive"]


def diff(before: dict[str, str], after: dict[str, str], label_a: str = "a", label_b: str = "b") -> str:
    """A unified diff over every part of two versions (a part missing on one side diffs against empty)."""
    out = []
    for part in sorted(set(before) | set(after)):
        a, b = before.get(part, ""), after.get(part, "")
        if a == b:
            continue
        out += difflib.unified_diff(a.splitlines(keepends=True), b.splitlines(keepends=True),
                                    fromfile=f"{label_a}/{part}", tofile=f"{label_b}/{part}")
    return "".join(x if x.endswith("\n") else x + "\n" for x in out)


def summary(e: Entry) -> dict[str, Any]:
    """What the activity feed and history lists show of an entry."""
    return {k: v for k, v in json.loads(e.model_dump_json()).items() if k not in ("parts_before", "parts_after")}
