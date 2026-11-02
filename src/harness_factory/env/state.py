"""StateInspector and StateDiff (B1). Graders score what changed, not what the model said.

Named state.py rather than inspect.py so it never shadows Python's standard inspect module.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

BIZ_FILE = "biz.json"     # the biz server's state, in the run's state folder next to the database


class EnvState(BaseModel):
    files: dict[str, str] = Field(default_factory=dict)            # relative path -> sha256
    tables: dict[str, list[list]] = Field(default_factory=dict)     # table -> sorted rows
    biz: dict[str, Any] | None = None                              # the business state document, if seeded


class StateDiff(BaseModel):
    files_added: list[str] = Field(default_factory=list)
    files_removed: list[str] = Field(default_factory=list)
    files_changed: list[str] = Field(default_factory=list)
    rows_added: dict[str, list[list]] = Field(default_factory=dict)
    rows_removed: dict[str, list[list]] = Field(default_factory=dict)
    # Business state, per record ("invoices/INV-1042"); a top-level part that isn't a collection of records
    # (e.g. "meta") is one entry.
    biz_added: list[str] = Field(default_factory=list)
    biz_removed: list[str] = Field(default_factory=list)
    biz_changed: list[str] = Field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not (self.files_added or self.files_removed or self.files_changed or self.rows_added
                    or self.rows_removed or self.biz_added or self.biz_removed or self.biz_changed)

    def summary(self) -> str:
        parts = []
        for label, items in (("+files", self.files_added), ("-files", self.files_removed),
                             ("~files", self.files_changed)):
            if items:
                parts.append(f"{label} {', '.join(items)}")
        for label, tables in (("+rows", self.rows_added), ("-rows", self.rows_removed)):
            for t, rows in tables.items():
                parts.append(f"{label} {t}:{len(rows)}")
        for label, items in (("+biz", self.biz_added), ("-biz", self.biz_removed), ("~biz", self.biz_changed)):
            if items:
                parts.append(f"{label} {', '.join(items)}")
        return "; ".join(parts) or "no change"


def _records(doc: dict[str, Any]) -> dict[str, Any]:
    """Business state flattened to records: "collection/id" for every collection of records, else the part."""
    out: dict[str, Any] = {}
    for part, value in doc.items():
        if isinstance(value, dict) and all(isinstance(v, dict) for v in value.values()):
            out.update({f"{part}/{rid}": rec for rid, rec in value.items()})
        else:
            out[part] = value
    return out


class StateInspector:
    def __init__(self, workspace: Path, db_path: Path, biz_path: Path | None = None) -> None:
        self.workspace = workspace
        self.db_path = db_path
        self.biz_path = biz_path or db_path.with_name(BIZ_FILE)

    def capture(self) -> EnvState:
        files = {}
        if self.workspace.exists():
            for f in sorted(p for p in self.workspace.rglob("*") if p.is_file()):
                files[str(f.relative_to(self.workspace))] = hashlib.sha256(f.read_bytes()).hexdigest()
        tables: dict[str, list[list]] = {}
        if self.db_path.exists():
            with sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True) as c:
                names = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")]
                for t in names:
                    rows = [list(r) for r in c.execute(f'SELECT * FROM "{t}"')]
                    tables[t] = sorted(rows, key=repr)
        biz = json.loads(self.biz_path.read_text()) if self.biz_path.is_file() else None
        return EnvState(files=files, tables=tables, biz=biz)

    @staticmethod
    def diff(before: EnvState, after: EnvState) -> StateDiff:
        d = StateDiff()
        d.files_added = sorted(set(after.files) - set(before.files))
        d.files_removed = sorted(set(before.files) - set(after.files))
        d.files_changed = sorted(k for k in set(before.files) & set(after.files) if before.files[k] != after.files[k])
        for t in set(before.tables) | set(after.tables):
            b = {repr(r) for r in before.tables.get(t, [])}
            a = {repr(r) for r in after.tables.get(t, [])}
            added = [r for r in after.tables.get(t, []) if repr(r) not in b]
            removed = [r for r in before.tables.get(t, []) if repr(r) not in a]
            if added:
                d.rows_added[t] = added
            if removed:
                d.rows_removed[t] = removed
        b, a = _records(before.biz or {}), _records(after.biz or {})
        d.biz_added = sorted(set(a) - set(b))
        d.biz_removed = sorted(set(b) - set(a))
        d.biz_changed = sorted(k for k in set(a) & set(b) if a[k] != b[k])
        return d
