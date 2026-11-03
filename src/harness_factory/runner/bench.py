"""A benchmark described in one file (`hfx bench <file>`), shaped like docs/design/benchmark.example.yaml.

The file is validated in two steps:
  1. the schema (`BenchmarkFile`): unknown keys, wrong types and [planned] fields that aren't built are refused;
  2. against the catalog (`check`): task ids, recipes, providers and models, fault profiles must exist.

It then becomes an ordinary campaign plan (`runner.plan`, one block per provider, as `hfx smoke` builds it), so
run identity, resume, projection and scoring are exactly those of `hfx smoke`. The file itself is stored with the
campaign (`store_file`): a copy and its sha256 in var/bench/<name>-<hash>/, next to a manifest listing the RunKeys
the file planned, so any stored result can be traced back to the file that produced it.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from harness_factory import settings
from harness_factory.env.faults import NO_FAULTS
from harness_factory.tasks.model import Split

BUILT_RUNNERS = ("native",)
PLANNED = "docs/design/test-matrix.md"


class BenchFileError(ValueError):
    """A benchmark file that can't run as written. The message lists every problem, one per line."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TaskSelection(_Strict):
    splits: list[Split] = [Split.DEV, Split.TEST]
    tag: str | None = None
    ids: list[str] | None = None

    @field_validator("splits")
    @classmethod
    def _no_held_out(cls, v: list[Split]) -> list[Split]:
        if not v:
            raise ValueError("name at least one split (DEV, TEST)")
        if Split.HELD_OUT in v:
            raise ValueError("HELD_OUT tasks are never used for choosing; they are run by `hfx select evaluate`")
        return v

    @model_validator(mode="after")
    def _ids_or_tag(self) -> TaskSelection:
        if self.ids is not None and self.tag is not None:
            raise ValueError("give `ids` or `tag`, not both")
        if self.ids is not None and not self.ids:
            raise ValueError("`ids` is empty; remove it to select by split and tag")
        return self


class ModelEntry(_Strict):
    model: str
    provider: str


class Limits(_Strict):
    concurrency: int = Field(4, ge=1)
    budget_usd: float | None = Field(None, ge=0)
    deadline_days: int | None = Field(None, ge=1)


class Compare(_Strict):
    base: str
    against: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def _distinct(self) -> Compare:
        if self.base in self.against:
            raise ValueError(f"`against` repeats the base recipe {self.base!r}")
        return self

    def pairs(self) -> list[tuple[str, str]]:
        return [(self.base, v) for v in self.against]


class Judge(_Strict):
    score: Literal["E"] = "E"
    freeze_cost_baseline: bool = False
    report: bool = True
    compare: Compare | None = None


class Calibration(_Strict):
    enabled: bool = False
    model: str | None = None
    providers: list[str] = []
    margin: float | None = None
    min_pairs: int | None = None

    @field_validator("enabled")
    @classmethod
    def _not_run_here(cls, v: bool) -> bool:
        if v:
            raise ValueError("hfx bench does not run calibration; set `enabled: false` and run the provider "
                             "calibration on its own (`hfx calibration`, PR #35)")
        return v


class BenchmarkFile(_Strict):
    name: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    note: str = ""
    tasks: TaskSelection = TaskSelection()
    recipes: list[str] = Field(min_length=1)
    models: list[ModelEntry] = Field(min_length=1)
    runners: list[str] = ["native"]
    seeds: int = Field(1, ge=1)
    faults: list[str] = [NO_FAULTS]
    limits: Limits = Limits()
    judge: Judge = Judge()
    calibration: Calibration = Calibration()

    @field_validator("recipes", "faults")
    @classmethod
    def _unique(cls, v: list[str]) -> list[str]:
        dup = sorted({x for x in v if v.count(x) > 1})
        if dup:
            raise ValueError(f"listed more than once: {', '.join(dup)}")
        return v

    @field_validator("runners")
    @classmethod
    def _built_runners(cls, v: list[str]) -> list[str]:
        planned = [r for r in v if r not in BUILT_RUNNERS]
        if planned:
            raise ValueError(f"runner(s) {', '.join(planned)} are planned, not built: only "
                             f"{', '.join(BUILT_RUNNERS)} runs today (see {PLANNED}, section 2)")
        if not v:
            raise ValueError("name at least one runner (native)")
        return v

    @model_validator(mode="after")
    def _cross_fields(self) -> BenchmarkFile:
        pairs = [(m.model, m.provider) for m in self.models]
        dup = sorted({f"{m} on {p}" for m, p in pairs if pairs.count((m, p)) > 1})
        if dup:
            raise ValueError(f"models listed more than once: {', '.join(dup)}")
        c = self.judge.compare
        if c:
            missing = [r for r in [c.base, *c.against] if r not in self.recipes]
            if missing:
                raise ValueError(f"judge.compare names recipe(s) not in `recipes`: {', '.join(missing)}")
        return self

    # ------------------------------------------------------------- derived

    def fault_ids(self) -> list[str | None]:
        return [None if x == NO_FAULTS else x for x in self.faults]

    def providers(self) -> list[str]:
        """Providers in file order: the campaign runs one provider at a time, like `hfx smoke`."""
        return list(dict.fromkeys(m.provider for m in self.models))

    def models_for(self, provider: str) -> list[str]:
        return [m.model for m in self.models if m.provider == provider]


def _where(loc: tuple) -> str:
    return ".".join(str(x) for x in loc)


def load(path: Path) -> tuple[BenchmarkFile, bytes]:
    """Parse and validate the file's schema. Returns the model and the raw bytes (what gets stored and hashed)."""
    if not path.is_file():
        raise BenchFileError(f"{path}: no such file")
    raw = path.read_bytes()
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as e:
        raise BenchFileError(f"{path}: not valid YAML: {e}") from e
    if not isinstance(data, dict):
        raise BenchFileError(f"{path}: expected a mapping of sections (name, tasks, recipes, models, ...)")
    try:
        return BenchmarkFile.model_validate(data), raw
    except ValidationError as e:
        lines = []
        for err in e.errors():
            msg = err["msg"].removeprefix("Value error, ")
            if err["type"] == "extra_forbidden":
                msg = "unknown field (typo, or not part of the benchmark file format)"
            lines.append(f"  {_where(err['loc'])}: {msg}" if err["loc"] else f"  {msg}")
        raise BenchFileError(f"{path}: {len(lines)} problem(s)\n" + "\n".join(lines)) from None


def select_tasks(f, sel: TaskSelection) -> list[str]:
    """Task ids the file selects: `ids` exactly as listed, or every task in `splits` carrying `tag`."""
    if sel.ids is not None:
        return list(sel.ids)
    return [t.id for t in f.tasks.all() if t.split in sel.splits and (sel.tag is None or sel.tag in t.tags)]


def check(f, bench: BenchmarkFile) -> list[str]:
    """Everything the catalog must provide for this file to run. Empty = runnable."""
    problems = []
    known_tasks = {t.id: t for t in f.tasks.all(include_held_out=True)}
    if bench.tasks.ids is not None:
        for tid in bench.tasks.ids:
            t = known_tasks.get(tid)
            if t is None:
                problems.append(f"tasks.ids: unknown task {tid!r}")
            elif t.split not in bench.tasks.splits:
                problems.append(f"tasks.ids: {tid} is a {t.split.value} task, not in splits "
                                f"{[s.value for s in bench.tasks.splits]}")
    elif not select_tasks(f, bench.tasks):
        problems.append(f"tasks: no task in splits {[s.value for s in bench.tasks.splits]}"
                        + (f" carries tag {bench.tasks.tag!r}" if bench.tasks.tag else ""))
    for rid in bench.recipes:
        if rid not in f.recipes:
            problems.append(f"recipes: unknown recipe {rid!r}; known: {', '.join(sorted(f.recipes))}")
    for m in bench.models:
        try:
            f.providers.served(m.provider, m.model)
        except KeyError as e:
            problems.append(f"models: {e.args[0]}")
    for fid in bench.faults:
        if fid != NO_FAULTS and fid not in f.faults:
            problems.append(f"faults: unknown profile {fid!r}; known: {', '.join([NO_FAULTS, *sorted(f.faults)])}")
    return problems


def build_plan(f, bench: BenchmarkFile) -> list:
    """The campaign plan: one `runner.plan` block per provider, in file order."""
    from harness_factory.runner.runner import plan
    task_ids = select_tasks(f, bench.tasks)
    specs = []
    for provider in bench.providers():
        specs += plan(f, bench.recipes, provider, bench.models_for(provider), bench.seeds, task_ids=task_ids,
                      faults=bench.fault_ids())
    return specs


def preflight_problems(f, projection, deadline_days: int | None, budget_usd: float | None) -> list[str]:
    """Refuse only what can't work (MOD-05), with the same rules as `hfx smoke`."""
    problems = []
    for p in projection:
        if f.ledger.exhausted(p.bucket):
            problems.append(f"{p.bucket}: today's quota is already spent (ledger: {settings.QUOTA_STATE.name}); "
                            f"run again with --resume after the reset (00:00 UTC)")
        elif deadline_days is not None and p.days > deadline_days:
            problems.append(f"{p.bucket}: needs about {p.days} days > limits.deadline_days {deadline_days}")
    total_usd = sum(p.usd for p in projection)
    if budget_usd is not None and total_usd > budget_usd:
        problems.append(f"projected ${total_usd:.2f} at list prices > limits.budget_usd {budget_usd}")
    return problems


def file_hash(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def bench_dir(bench: BenchmarkFile, raw: bytes) -> Path:
    """var/bench/<name>-<sha256[:12]>: the same file (byte for byte) always lands in the same directory, so a
    resume adds to it; an edited file is a different benchmark and gets its own."""
    return settings.VAR / "bench" / f"{bench.name}-{file_hash(raw)[:12]}"


def store_file(f, bench: BenchmarkFile, raw: bytes, source: Path, specs: list, *, resume: bool) -> Path:
    """Copy the file next to the campaign's report and record its hash and the RunKeys it planned. Called before
    the campaign starts, so an interrupted campaign is traceable too. Returns the directory."""
    from harness_factory.provision.provisioner import run_key_for
    d = bench_dir(bench, raw)
    d.mkdir(parents=True, exist_ok=True)
    (d / "benchmark.yaml").write_bytes(raw)
    manifest_path = d / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.is_file() else {}
    keys = [run_key_for(f.tasks.get(s.task_id), f.recipes[s.recipe_id], s.canonical_model, s.provider, s.seed,
                        declared=f.declared, providers=f.providers, fault=f.fault(s.fault)).as_str() for s in specs]
    invocations = manifest.get("invocations", [])
    invocations.append({"at": datetime.now(UTC).isoformat(timespec="seconds"), "resume": resume,
                        "source": str(source.resolve())})
    manifest.update({"name": bench.name, "file_sha256": file_hash(raw), "runs": len(specs), "run_keys": keys,
                     "invocations": invocations})
    manifest_path.write_text(json.dumps(manifest, indent=1))
    return d


def record_outcome(d: Path, **fields) -> None:
    """Add what the last invocation produced (baseline id, report path, counts) to the manifest."""
    path = d / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["invocations"][-1].update(fields)
    path.write_text(json.dumps(manifest, indent=1))


_PAIR = re.compile(r"^\s*([^:,\s]+)\s*:\s*([^:,\s]+)\s*$")


def parse_compare(text: str) -> list[tuple[str, str]]:
    """`base:variant,base:variant` -> [(base, variant), ...]; ΔE is variant minus base."""
    pairs = []
    for part in text.split(","):
        m = _PAIR.match(part)
        if not m:
            raise ValueError(f"{part.strip()!r}: expected base:variant, e.g. v2-base:v3-staged")
        if m.group(1) == m.group(2):
            raise ValueError(f"{part.strip()!r}: a recipe can't be paired with itself")
        pairs.append((m.group(1), m.group(2)))
    return pairs
