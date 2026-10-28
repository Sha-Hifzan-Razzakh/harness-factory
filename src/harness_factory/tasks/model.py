"""Task, Oracle, Depth (B1). A task is versioned data; its identity is its content hash."""

from __future__ import annotations

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


CheckKind = Literal["file_equals", "file_contains", "file_absent", "sql_rows", "output_regex", "tool_called"]


class WeightedCheck(BaseModel):
    id: str
    kind: CheckKind
    weight: float = Field(ge=0, le=1)  # 0 = guard check: earns nothing, but failing it is critical
    critical: bool = False
    path: str | None = None          # file_* checks, relative to the workspace
    text: str | None = None          # file_equals / file_contains
    negate: bool = False             # file_contains: must NOT contain
    query: str | None = None         # sql_rows
    rows: list[list[Any]] | None = None
    pattern: str | None = None       # output_regex; tool_called (fnmatch on tool ref, successful calls only)

    @model_validator(mode="after")
    def _guard_is_critical(self) -> WeightedCheck:
        if self.weight == 0 and not self.critical:
            raise ValueError(f"check {self.id}: a zero-weight check must be critical")
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
    mock_api_state: dict[str, Any] = Field(default_factory=dict)
    memory_seed: list[dict[str, Any]] = Field(default_factory=list)

    def resolve(self, task_dir: Path) -> tuple[Path | None, Path | None]:
        f = (task_dir / self.files).resolve() if self.files else None
        s = (task_dir / self.sqlite).resolve() if self.sqlite else None
        return f, s

    def data_hash(self, task_dir: Path) -> str:
        f, s = self.resolve(task_dir)
        return hash_paths([p for p in (f, s) if p is not None])


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

    def content_hash(self) -> str:
        data = self.model_dump(mode="json", exclude={"status", "split", "tags"})   # labels, not identity
        if self.source_dir is not None:
            data["seed_hash"] = self.env_seed.data_hash(self.source_dir)
        return content_hash(data)


def load_task(task_dir: Path) -> Task:
    data = yaml.safe_load((task_dir / "task.yaml").read_text())
    task = Task.model_validate(data)
    task.source_dir = task_dir.resolve()
    return task
