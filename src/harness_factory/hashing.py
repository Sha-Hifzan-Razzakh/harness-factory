"""Content hashing. Identity of tasks, recipes and configs is the hash of their canonical JSON."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel


def canonical_json(obj: Any) -> str:
    if isinstance(obj, BaseModel):
        obj = obj.model_dump(mode="json")
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def content_hash(obj: Any, length: int = 16) -> str:
    return hashlib.sha256(canonical_json(obj).encode()).hexdigest()[:length]


def hash_paths(paths: list[Path], length: int = 16) -> str:
    """Hash file contents and relative names, in a stable order."""
    h = hashlib.sha256()
    for p in sorted(paths):
        if p.is_dir():
            for f in sorted(x for x in p.rglob("*") if x.is_file()):
                h.update(str(f.relative_to(p)).encode())
                h.update(f.read_bytes())
        elif p.exists():
            h.update(p.name.encode())
            h.update(p.read_bytes())
    return h.hexdigest()[:length]
