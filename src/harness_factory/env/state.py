"""StateInspector and StateDiff (B1). Graders score what changed, not what the model said.

Named state.py rather than inspect.py so it never shadows Python's standard inspect module.
"""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

from pydantic import BaseModel, Field


class EnvState(BaseModel):
    files: dict[str, str] = Field(default_factory=dict)            # relative path -> sha256
    tables: dict[str, list[list]] = Field(default_factory=dict)     # table -> sorted rows


class StateDiff(BaseModel):
    files_added: list[str] = Field(default_factory=list)
    files_removed: list[str] = Field(default_factory=list)
    files_changed: list[str] = Field(default_factory=list)
    rows_added: dict[str, list[list]] = Field(default_factory=dict)
    rows_removed: dict[str, list[list]] = Field(default_factory=dict)

    @property
    def is_empty(self) -> bool:
        return not (self.files_added or self.files_removed or self.files_changed or self.rows_added
                    or self.rows_removed)

    def summary(self) -> str:
        parts = []
        for label, items in (("+files", self.files_added), ("-files", self.files_removed),
                             ("~files", self.files_changed)):
            if items:
                parts.append(f"{label} {', '.join(items)}")
        for label, tables in (("+rows", self.rows_added), ("-rows", self.rows_removed)):
            for t, rows in tables.items():
                parts.append(f"{label} {t}:{len(rows)}")
        return "; ".join(parts) or "no change"


class StateInspector:
    def __init__(self, workspace: Path, db_path: Path) -> None:
        self.workspace = workspace
        self.db_path = db_path

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
        return EnvState(files=files, tables=tables)

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
        return d
