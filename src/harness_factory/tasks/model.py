"""Task, Oracle, Depth (B1). A task is versioned data; its identity is its content hash."""

from __future__ import annotations

import json
import re
from enum import Enum
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, model_validator

from harness_factory.hashing import content_hash, hash_paths

Domain = Literal["code", "docs", "data", "api", "research", "ops"]
RiskClass = Literal["R0", "R1", "R2", "R3"]


class Depth(str, Enum):
    SINGLE_STEP = "SINGLE_STEP"      # one call answers it
    KNOWN_PATH = "KNOWN_PATH"        # fixed sequence of steps
    UNKNOWN_PATH = "UNKNOWN_PATH"    # steps found while working


class Split(str, Enum):
    DEV = "DEV"
    TEST = "TEST"
    HELD_OUT = "HELD_OUT"


CheckKind = Literal["file_equals", "file_contains", "file_regex", "file_absent", "json_valid", "sql_rows",
                    "output_regex", "tool_called", "remote_equals", "tests_pass", "git_log_contains",
                    "biz_equals", "biz_count"]
GIT_CHECKS = ("git_log_contains",)


class WeightedCheck(BaseModel):
    id: str
    kind: CheckKind
    weight: float = Field(ge=0, le=1)  # 0 = guard check: earns nothing, but failing it is critical
    critical: bool = False
    path: str | None = None          # file_* checks, relative to the workspace; remote_equals: "<server>:<JSON
                                     # pointer>" into that server's test tenant after the run (EXT-07);
                                     # biz_equals / biz_count: a JSON pointer into the business state after the run
    text: str | None = None          # file_equals / file_contains; remote_equals / biz_equals: the value (a string,
                                     # or JSON, compared strictly on type); biz_count: the expected count
    negate: bool = False             # file_contains / file_regex / remote_equals / biz_equals: must NOT match
    query: str | None = None         # sql_rows; biz_count: optional JSON object of field values a record must have
    rows: list[list[Any]] | None = None
    pattern: str | None = None       # output_regex, file_regex (re.MULTILINE; add (?s) to cross lines);
                                     # tool_called (fnmatch on tool ref, successful calls only);
                                     # tests_pass: a pytest -k expression; git_log_contains: a commit message regex
    # tests_pass (ADR 0012): `path` is what to run in the workspace (default "."); `text` is a hidden test module
    # run from outside the workspace instead, with the workspace importable. git_log_contains: `ref` (default HEAD)
    # must have a commit after the seed whose message matches `pattern` (and that touches `path`, if given).
    ref: str | None = None

    @model_validator(mode="after")
    def _guard_is_critical(self) -> WeightedCheck:
        if self.weight == 0 and not self.critical:
            raise ValueError(f"check {self.id}: a zero-weight check must be critical")
        needs = {"file_equals": ("path", "text"), "file_contains": ("path", "text"), "file_regex": ("path", "pattern"),
                 "file_absent": ("path",), "json_valid": ("path",), "sql_rows": ("query", "rows"),
                 "output_regex": ("pattern",), "tool_called": ("pattern",),
                 "remote_equals": ("path", "text"), "tests_pass": (), "git_log_contains": ("pattern",),
                 "biz_equals": ("path", "text"), "biz_count": ("path", "text")}[self.kind]
        missing = [f for f in needs if getattr(self, f) is None]
        if missing:
            raise ValueError(f"check {self.id} ({self.kind}) needs {', '.join(missing)}")
        if self.kind == "remote_equals":
            server, sep, pointer = (self.path or "").partition(":")
            if not server or not sep or (pointer and not pointer.startswith("/")):
                raise ValueError(f"check {self.id}: remote_equals path is '<server>:<JSON pointer>', e.g. "
                                 f"'crm:/orders/ord-7/status'")
        if self.ref is not None and self.kind not in GIT_CHECKS:
            raise ValueError(f"check {self.id}: `ref` is for git checks")
        if self.kind == "tests_pass" and self.path and self.text:
            raise ValueError(f"check {self.id}: tests_pass runs either `path` (in the workspace) or `text` (hidden "
                             f"tests), not both")
        if self.kind in ("biz_equals", "biz_count") and self.path and not self.path.startswith("/"):
            raise ValueError(f"check {self.id}: {self.kind} path is a JSON pointer, e.g. '/invoices/INV-1042/status'")
        if self.kind == "biz_count":
            if not (self.text or "").isdigit():
                raise ValueError(f"check {self.id}: biz_count text is the expected count, a whole number")
            if self.query is not None:
                try:
                    where = json.loads(self.query)
                except ValueError as e:
                    raise ValueError(f"check {self.id}: biz_count query is a JSON object: {e}") from e
                if not isinstance(where, dict):
                    raise ValueError(f"check {self.id}: biz_count query is a JSON object of field values")
        if self.pattern is not None and self.kind in ("file_regex", "output_regex", "git_log_contains"):
            try:
                re.compile(self.pattern)
            except re.error as e:
                raise ValueError(f"check {self.id}: bad pattern: {e}") from e
        return self


class Oracle(BaseModel):
    checks: list[WeightedCheck]
    pass_threshold: float = 1.0

    @model_validator(mode="after")
    def _weights_sum_to_one(self) -> Oracle:
        total = sum(c.weight for c in self.checks)
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"oracle weights must sum to 1.0, got {total:.4f}")
        return self


class EnvSeed(BaseModel):
    files: str | None = None          # directory copied into the workspace (relative to the task dir)
    sqlite: str | None = None         # SQL script that builds the task database
    mock_api_state: dict[str, Any] = Field(default_factory=dict)   # server id -> its test tenant's seed (EXT-07)
    memory_seed: list[dict[str, Any]] = Field(default_factory=list)
    # A git repository over the seed files with one commit, made identically everywhere (ADR 0012). Hashed only
    # when set, so tasks without it keep their hash.
    git: bool = False
    biz_state: str | None = None      # JSON file: the biz server's business state (a fresh copy each run)

    def resolve(self, task_dir: Path) -> tuple[Path | None, Path | None]:
        f = (task_dir / self.files).resolve() if self.files else None
        s = (task_dir / self.sqlite).resolve() if self.sqlite else None
        return f, s

    def biz_seed(self, task_dir: Path) -> Path | None:
        return (task_dir / self.biz_state).resolve() if self.biz_state else None

    def data_hash(self, task_dir: Path) -> str:
        f, s = self.resolve(task_dir)
        return hash_paths([p for p in (f, s, self.biz_seed(task_dir)) if p is not None])


# Seed fields added after tasks were first hashed: left out of the task hash while unset, so existing task hashes
# stay the same (like recipes' _HASH_OMIT_WHEN_NONE).
_SEED_OMIT_WHEN_NONE = ("biz_state",)


class Task(BaseModel):
    id: str
    version: int = 1
    base_id: str | None = None
    domain: Domain
    instruction: str
    env_seed: EnvSeed = Field(default_factory=EnvSeed)
    allowed_tools: list[str]
    oracle: Oracle
    grader_ids: list[str] = Field(default_factory=lambda: ["state_diff", "trajectory"])
    risk: RiskClass = "R1"
    expected_depth: Depth = Depth.KNOWN_PATH
    split: Split = Split.DEV
    status: Literal["draft", "verified", "superseded"] = "draft"
    tags: list[str] = Field(default_factory=list)

    # set by the loader, not part of identity
    source_dir: Path | None = Field(default=None, exclude=True)

    def key(self) -> str:
        return f"{self.id}@{self.version}"

    @model_validator(mode="after")
    def _git_checks_need_a_repo(self) -> Task:
        gits = [c.id for c in self.oracle.checks if c.kind in GIT_CHECKS]
        if gits and not self.env_seed.git:
            raise ValueError(f"task {self.id}: git checks ({', '.join(gits)}) need `env_seed.git: true`")
        return self

    def content_hash(self) -> str:
        data = self.model_dump(mode="json", exclude={"status", "split", "tags"})   # labels, not identity
        # Fields added after tasks were first hashed: dropped when unset, so existing task hashes don't change.
        for k in _SEED_OMIT_WHEN_NONE:
            if data["env_seed"].get(k) is None:
                data["env_seed"].pop(k, None)
        for c in data["oracle"]["checks"]:
            if c.get("ref") is None:
                c.pop("ref", None)
        if not self.env_seed.git:
            data["env_seed"].pop("git", None)
        else:                      # how the seed repository is made is part of the task's environment
            from harness_factory.env.mcp.impl.proc import GIT_SEED
            data["git_seed"] = GIT_SEED
        if self.source_dir is not None:
            data["seed_hash"] = self.env_seed.data_hash(self.source_dir)
        return content_hash(data)


def load_task(task_dir: Path) -> Task:
    data = yaml.safe_load((task_dir / "task.yaml").read_text())
    task = Task.model_validate(data)
    task.source_dir = task_dir.resolve()
    return task
