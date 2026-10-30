"""Fault injection (B11: FaultConfig / FaultProxy): realistic tool failures, so robustness R can be measured.

A fault profile (catalog/faults/<id>.yaml) is a list of rules. Each rule names the tools it applies to, the kind
of failure, and when it fires (a rate, and/or specific call numbers per tool). Faults are injected inside the
ToolExecutor, after the call has passed allowance, argument and permission checks, so a refused call is never
reported as a fault.

Kinds (what the model sees is a realistic failure, never the word "injected"):
  error       the tool reports it is temporarily unavailable; nothing happened
  timeout     the call times out; nothing happened
  rate_limit  the service answers 429; nothing happened
  empty       the call "succeeds" with an empty result; nothing happened
  garbled     the call really runs (side effects happen), but its result comes back cut off

Determinism: whether call n of tool t fires is a pure function of (profile hash, run seed, t, n), so the same
RunKey always sees the same faults. The profile's content hash is RunKey.fault_config_hash: a faulted run is a
different experiment from its clean twin, and R compares the two at aggregation (results.store).
"""

from __future__ import annotations

import hashlib
from collections import Counter
from fnmatch import fnmatch
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, model_validator

from harness_factory.hashing import content_hash

FaultKind = Literal["error", "timeout", "rate_limit", "empty", "garbled"]
NO_FAULTS = "none"

MESSAGES: dict[str, str] = {
    "error": "Error: {tool} is temporarily unavailable. Try again.",
    "timeout": "Error: {tool} timed out.",
    "rate_limit": "Error 429: too many requests to {tool}. Retry shortly.",
}


class FaultRule(BaseModel):
    tools: list[str] = Field(default_factory=lambda: ["*"])   # fnmatch patterns on tool refs ("fs.write_file")
    kind: FaultKind
    rate: float = Field(1.0, ge=0.0, le=1.0)                   # chance each eligible call fires
    nth: list[int] | None = None                               # only these call numbers per tool (1-based)
    max_per_run: int | None = Field(None, ge=1)                # stop firing after this many in one run

    def matches(self, tool_ref: str) -> bool:
        return any(fnmatch(tool_ref, p) for p in self.tools)


class FaultConfig(BaseModel):
    id: str
    description: str = ""
    rules: list[FaultRule] = Field(min_length=1)

    @model_validator(mode="after")
    def _check(self) -> FaultConfig:
        if self.id == NO_FAULTS:
            raise ValueError(f"'{NO_FAULTS}' is reserved for clean runs")
        return self

    def content_hash(self) -> str:
        return content_hash(self.model_dump(mode="json", exclude={"description"}), 12)


def load_fault_profiles(root: Path) -> dict[str, FaultConfig]:
    out: dict[str, FaultConfig] = {}
    if root.is_dir():
        for p in sorted(root.glob("*.yaml")):
            cfg = FaultConfig.model_validate(yaml.safe_load(p.read_text()))
            if cfg.id in out:
                raise ValueError(f"duplicate fault profile id {cfg.id!r}")
            out[cfg.id] = cfg
    return out


def _draw(*parts: object) -> float:
    """A deterministic number in [0, 1) from its inputs."""
    digest = hashlib.sha256("|".join(map(str, parts)).encode()).digest()
    return int.from_bytes(digest[:8], "big") / 2**64


class FaultInjector:
    """Per run: decides, call by call, whether a fault fires. Holds per-run counters, so one injector per run."""

    def __init__(self, config: FaultConfig, seed: int) -> None:
        self.config = config
        self.key = config.content_hash()
        self.seed = seed
        self.calls: Counter = Counter()          # tool ref -> calls seen
        self.fired: Counter = Counter()          # rule index -> times fired
        self.log: list[tuple[str, int, str]] = []

    def decide(self, tool_ref: str) -> FaultKind | None:
        self.calls[tool_ref] += 1
        n = self.calls[tool_ref]
        for i, rule in enumerate(self.config.rules):
            if not rule.matches(tool_ref):
                continue
            if rule.nth is not None and n not in rule.nth:
                continue
            if rule.max_per_run is not None and self.fired[i] >= rule.max_per_run:
                continue
            if _draw(self.key, self.seed, tool_ref, n, i) < rule.rate:
                self.fired[i] += 1
                self.log.append((tool_ref, n, rule.kind))
                return rule.kind
        return None

    @staticmethod
    def message(kind: FaultKind, tool_ref: str) -> str:
        return MESSAGES[kind].format(tool=tool_ref)

    @staticmethod
    def garble(text: str) -> str:
        """Cut the real result off part-way, the way a dropped connection or a bad proxy would."""
        if len(text) <= 4:
            return ""
        return text[: max(1, len(text) // 3)]
