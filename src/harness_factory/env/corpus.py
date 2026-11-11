"""Recorded, versioned web corpora (ADR 0018, ENV-01, KNW-01): what the web server serves.

A corpus is a folder in the catalog, next to the tasks:

    catalog/corpora/<id>/
      corpus.yaml          id, title, description, redirects, gone; and the frozen `version` and `page_count`
      pages/**/*.md        one page per file: a YAML header (url, title, fetched, published?) and the body

Its version is its content address: a hash of everything the server can serve (every page's URL, title, dates
and text, every redirect and recorded 404/410), not of file names or labels, and with line endings normalised so a
Windows checkout has the same version. `hfx corpus freeze` writes the version and page count into corpus.yaml;
a corpus whose files no longer match it is refused at provisioning (as tool drift is), and `hfx validate` says so.

A task names its corpus with `env_seed.corpus: <id>`; the version is part of that task's hash. The factory
compiles a version once (pages, redirects, 404s and the BM25 index of webindex.py) into a content-addressed cache,
`var/corpora/<id>/<index id>/corpus.json`, and copies it read-only into each run's state folder, where the web
server reads it. The index id (version + engine) is recorded in the run's snapshot.

Large corpora can live in DVC instead of git (ADR 0018): `pages/` is then a DVC-tracked folder (`pages.dvc`), and
nothing else changes, because the version is computed from the materialised pages, not from where they are kept.
"""

from __future__ import annotations

import os
import re
import tempfile
from datetime import date
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from harness_factory import atomicfile, settings
from harness_factory.env.mcp.impl import webindex
from harness_factory.hashing import canonical_json, content_hash

MANIFEST = "corpus.yaml"
PAGES_DIR = "pages"
COMPILED = "corpus.json"               # the web server's CORPUS_FILE
CORPORA = "corpora"                    # the catalog folder, a sibling of tasks/
_ID = r"^[a-z0-9][a-z0-9-]*$"
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_FRONT = re.compile(r"\A---\n(.*?)\n---\n?(.*)\Z", re.DOTALL)


class CorpusError(ValueError):
    pass


def _iso(v) -> str:
    s = v.isoformat() if isinstance(v, date) else str(v)
    if not _DATE.match(s):
        raise ValueError(f"dates are YYYY-MM-DD, got {v!r}")
    return s


class Page(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str
    title: str = Field(min_length=1)
    fetched: str                        # when the snapshot took it
    published: str | None = None        # when the page says it was written, if it says
    text: str

    @model_validator(mode="before")
    @classmethod
    def _dates(cls, data):
        if isinstance(data, dict):
            data = dict(data)
            for k in ("fetched", "published"):
                if data.get(k) is not None:
                    data[k] = _iso(data[k])
        return data


class Redirect(BaseModel):
    from_: str = Field(alias="from")
    to: str
    status: Literal[301, 302, 307, 308] = 301


class Gone(BaseModel):
    url: str
    status: Literal[404, 410] = 404


class Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=_ID)
    title: str
    description: str = ""
    recorded: str | None = None         # label: when the snapshot was taken
    redirects: list[Redirect] = Field(default_factory=list)
    gone: list[Gone] = Field(default_factory=list)
    version: str | None = None          # frozen by `hfx corpus freeze`
    page_count: int | None = None

    @model_validator(mode="before")
    @classmethod
    def _recorded(cls, data):
        if isinstance(data, dict) and data.get("recorded") is not None:
            data = {**data, "recorded": _iso(data["recorded"])}
        return data


class Corpus(BaseModel):
    root: Path
    manifest: Manifest
    pages: list[Page]                   # in URL order

    @property
    def id(self) -> str:
        return self.manifest.id

    def content(self) -> dict:
        """Everything the server can serve: the corpus's identity."""
        return {"id": self.id,
                "pages": [p.model_dump(exclude_none=True) for p in self.pages],
                "redirects": sorted([r.from_, r.to, r.status] for r in self.manifest.redirects),
                "gone": sorted([g.url, g.status] for g in self.manifest.gone)}

    @property
    def version(self) -> str:
        return content_hash(self.content(), 16)

    @property
    def index_id(self) -> str:
        return content_hash({"corpus": self.id, "version": self.version, "engine": webindex.ENGINE_ID}, 16)

    def stale(self) -> str | None:
        """Why the manifest's frozen version doesn't describe these files, or None."""
        m = self.manifest
        if m.version is None:
            return f"corpus {self.id} has no frozen version; run `hfx corpus freeze`"
        if m.version != self.version or m.page_count != len(self.pages):
            return (f"corpus {self.id} changed since it was frozen (manifest {m.version}, {m.page_count} pages; files "
                    f"{self.version}, {len(self.pages)} pages): run `hfx corpus freeze`, which is a new version")
        return None

    def info(self) -> dict:
        """What a run's snapshot records (KNW-01)."""
        return {"id": self.id, "version": self.version, "index_id": self.index_id, "engine": webindex.ENGINE_ID,
                "pages": len(self.pages), "redirects": len(self.manifest.redirects), "gone": len(self.manifest.gone)}

    def compile(self) -> dict:
        """The web server's view: pages, redirects and 404s keyed by URL, and the index."""
        pages = [p.model_dump(exclude_none=True) for p in self.pages]
        return {"id": self.id, "version": self.version, "index_id": self.index_id,
                "pages": {p["url"]: {k: v for k, v in p.items() if k != "url"} for p in pages},
                "redirects": {r.from_: {"to": r.to, "status": r.status} for r in self.manifest.redirects},
                "gone": {g.url: g.status for g in self.manifest.gone},
                "index": webindex.build_index(pages)}


def corpora_dir(task_dir: Path) -> Path:
    """Where a task's corpora are: `corpora/` next to the folder of task folders (catalog/tasks/<id>)."""
    return task_dir.parent.parent / CORPORA


def _parse_page(path: Path, rel: str) -> Page:
    raw = path.read_bytes().decode("utf-8").replace("\r\n", "\n")
    m = _FRONT.match(raw)
    if m is None:
        raise CorpusError(f"{rel}: a page starts with a YAML header between --- lines (url, title, fetched)")
    head = yaml.safe_load(m.group(1)) or {}
    if not isinstance(head, dict) or "text" in head:
        raise CorpusError(f"{rel}: the header is a mapping of url, title, fetched, published")
    try:
        return Page(**head, text=m.group(2).strip())
    except ValueError as e:
        raise CorpusError(f"{rel}: {e}") from e


def load_corpus(root: Path) -> Corpus:
    root = root.resolve()
    mf = root / MANIFEST
    if not mf.is_file():
        raise CorpusError(f"no corpus at {root} (no {MANIFEST})")
    try:
        manifest = Manifest.model_validate(yaml.safe_load(mf.read_text(encoding="utf-8")) or {})
    except ValueError as e:
        raise CorpusError(f"{mf}: {e}") from e
    if manifest.id != root.name:
        raise CorpusError(f"{mf}: id {manifest.id!r} must be the folder's name {root.name!r}")
    pages_dir = root / PAGES_DIR
    if not pages_dir.is_dir():
        hint = (f"its pages are kept in DVC: run `dvc pull {(root / 'pages.dvc').as_posix()}`"
                if (root / f"{PAGES_DIR}.dvc").is_file() else f"it has no {PAGES_DIR}/ folder")
        raise CorpusError(f"corpus {manifest.id}: {hint}")
    pages = [_parse_page(p, p.relative_to(root).as_posix()) for p in sorted(pages_dir.rglob("*.md"))]
    corpus = Corpus(root=root, manifest=manifest, pages=sorted(pages, key=lambda p: p.url))
    problems = _problems(corpus)
    if problems:
        raise CorpusError(f"corpus {manifest.id}: " + "; ".join(problems))
    return corpus


def _problems(c: Corpus) -> list[str]:
    out = []
    if not c.pages:
        out.append("it has no pages")
    urls: dict[str, int] = {}
    for u in [p.url for p in c.pages] + [r.from_ for r in c.manifest.redirects] + [g.url for g in c.manifest.gone]:
        if webindex.normalize_url(u) != u:
            out.append(f"write {u!r} as {webindex.normalize_url(u)!r} (https, lower-case host, no trailing slash "
                       f"or fragment)")
        urls[u] = urls.get(u, 0) + 1
    out += [f"{u} is listed {n} times (pages, redirects and gone are one URL each)" for u, n in urls.items() if n > 1]
    pages = {p.url for p in c.pages}
    gone = {g.url for g in c.manifest.gone}
    hops = {r.from_: r.to for r in c.manifest.redirects}
    for start, first in hops.items():
        seen, at = [start], first
        while at in hops and at not in seen:
            seen.append(at)
            at = hops[at]
        if at in seen:
            out.append(f"redirect loop: {' -> '.join([*seen, at])}")
        elif at not in pages | gone:
            out.append(f"redirect {start} ends at {at}, which is neither a page nor a recorded 404/410")
    return out


def find(task_dir: Path, corpus_id: str) -> Corpus:
    return load_corpus(corpora_dir(task_dir) / corpus_id)


def compiled(corpus: Corpus) -> Path:
    """The compiled corpus file for this version and engine, built on first use and then reused (KNW-01: indexed
    once per version). Content-addressed and written atomically, so concurrent runs and processes can share it:
    two writers write the same bytes, and a reader never sees half a file."""
    dest = settings.CORPORA_DIR / corpus.id / corpus.index_id / COMPILED
    if dest.is_file():
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".corpus-", suffix=".tmp", dir=dest.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(canonical_json(corpus.compile()))
        atomicfile.replace(tmp, dest)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return dest


def all_corpora(catalog: Path | None = None) -> list[Path]:
    root = (catalog or settings.CATALOG) / CORPORA
    return sorted(p.parent for p in root.glob(f"*/{MANIFEST}")) if root.is_dir() else []


def freeze(root: Path) -> tuple[Corpus, bool]:
    """Write the corpus's version and page count into its manifest. Returns (corpus, whether it changed)."""
    corpus = load_corpus(root)
    if corpus.stale() is None:
        return corpus, False
    mf = root / MANIFEST
    lines = [ln for ln in mf.read_text(encoding="utf-8").splitlines() if not re.match(r"^(version|page_count):", ln)]
    while lines and not lines[-1].strip():
        lines.pop()
    lines += [f"version: {corpus.version}", f"page_count: {len(corpus.pages)}"]
    mf.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return load_corpus(root), True
