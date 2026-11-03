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
  latency     the call really runs, after a real delay (`delay_s`: fixed seconds, or [min, max] drawn per call);
              the run's wall clock keeps running, as for any slow tool. A delay that reaches the tool's timeout_s
              is a real timeout: the call waits timeout_s, then fails like a tool that never answered (nothing ran)
  malformed   the call really runs and succeeds, but its result is wrong in shape while still parsing (see
              `malform`). Only calls that only read: a rule matching a write never fires on it

`timeout` stays an immediate timeout error (a real wait of timeout_s would make every `transient` run slower and
change what that existing profile measures); `latency` is the slow-tool kind.

Determinism: whether call n of tool t fires is a pure function of (profile hash, run seed, t, n), so the same
RunKey always sees the same faults. So are a latency rule's delay and the way a malformed result is reshaped,
which depend on (profile hash, run seed, t, n) too, never on the clock or a global random state. The profile's content hash is RunKey.fault_config_hash: a faulted run is a
different experiment from its clean twin, and R compares the two at aggregation (results.store).
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, model_validator

from harness_factory.hashing import content_hash

FaultKind = Literal["error", "timeout", "rate_limit", "empty", "garbled", "latency", "malformed"]
NO_FAULTS = "none"
# Kinds where the real call runs and its own outcome stands when it fails by itself (e.g. the model's bad arguments):
# the span then records the kind under `fault_inert`, not `fault`, so Q counts that error as the model's.
PASS_THROUGH: frozenset[str] = frozenset({"latency", "malformed"})
# Rule fields added after the first profiles: left out of the profile hash when unset, so existing hashes stay.
_HASH_OMIT_WHEN_NONE = ("delay_s",)

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
    delay_s: float | tuple[float, float] | None = None          # latency only: seconds, or [min, max] per call

    @model_validator(mode="after")
    def _check_delay(self) -> FaultRule:
        if self.kind != "latency":
            if self.delay_s is not None:
                raise ValueError(f"delay_s is for kind latency, not {self.kind}")
            return self
        if self.delay_s is None:
            raise ValueError("a latency rule needs delay_s: seconds, or [min, max]")
        lo, hi = self.delay_s if isinstance(self.delay_s, tuple) else (self.delay_s, self.delay_s)
        if lo < 0 or hi < lo:
            raise ValueError(f"delay_s must be >= 0 and [min, max] with min <= max, got {self.delay_s}")
        return self

    def matches(self, tool_ref: str) -> bool:
        return any(fnmatch(tool_ref, p) for p in self.tools)

    def delay_for(self, draw: float) -> float:
        """This rule's delay for one call, from a draw in [0, 1) (milliseconds precision)."""
        assert self.delay_s is not None
        lo, hi = self.delay_s if isinstance(self.delay_s, tuple) else (self.delay_s, self.delay_s)
        return round(lo + (hi - lo) * draw, 3)


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
        data = self.model_dump(mode="json", exclude={"description"})
        for rule in data["rules"]:
            for k in _HASH_OMIT_WHEN_NONE:
                if rule.get(k) is None:
                    rule.pop(k, None)
        return content_hash(data, 12)


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


@dataclass(frozen=True)
class Fault:
    """One fault that fired: which kind, on which call. `call` is the 1-based call number of `tool` in the run."""
    kind: FaultKind
    tool: str
    call: int
    rule: int                       # index of the rule that fired in the profile
    delay_s: float | None = None    # latency: how long this call waits before it runs


class FaultInjector:
    """Per run: decides, call by call, whether a fault fires. Holds per-run counters, so one injector per run."""

    def __init__(self, config: FaultConfig, seed: int) -> None:
        self.config = config
        self.key = config.content_hash()
        self.seed = seed
        self.calls: Counter = Counter()          # tool ref -> calls seen
        self.fired: Counter = Counter()          # rule index -> times fired
        self.log: list[tuple[str, int, str]] = []

    def fire(self, tool_ref: str, reads: bool = True) -> Fault | None:
        """Count this call of `tool_ref` and return the fault that fires on it, if any (the first rule that does).
        reads: whether this call only reads; a `malformed` rule is skipped on a call that doesn't (it neither fires
        nor uses up its max_per_run), since a write's acknowledgement isn't data the model works from."""
        self.calls[tool_ref] += 1
        n = self.calls[tool_ref]
        for i, rule in enumerate(self.config.rules):
            if not rule.matches(tool_ref):
                continue
            if rule.kind == "malformed" and not reads:
                continue
            if rule.nth is not None and n not in rule.nth:
                continue
            if rule.max_per_run is not None and self.fired[i] >= rule.max_per_run:
                continue
            if _draw(self.key, self.seed, tool_ref, n, i) < rule.rate:
                self.fired[i] += 1
                self.log.append((tool_ref, n, rule.kind))
                delay = rule.delay_for(_draw(self.key, self.seed, tool_ref, n, i, "delay")) \
                    if rule.kind == "latency" else None
                return Fault(kind=rule.kind, tool=tool_ref, call=n, rule=i, delay_s=delay)
        return None

    def decide(self, tool_ref: str, reads: bool = True) -> FaultKind | None:
        """The kind of the fault that fires on this call, or None (`fire` without the details)."""
        fault = self.fire(tool_ref, reads)
        return fault.kind if fault else None

    def malform(self, fault: Fault, text: str) -> tuple[str, str] | None:
        """The malformed version of a successful result for this fault's call, and how it was reshaped; None when
        the result has nothing to reshape. Deterministic per (profile, seed, tool, call)."""
        return malform(text, _draw(self.key, self.seed, fault.tool, fault.call, "malformed"))

    @staticmethod
    def message(kind: FaultKind, tool_ref: str) -> str:
        return MESSAGES[kind].format(tool=tool_ref)

    @staticmethod
    def garble(text: str) -> str:
        """Cut the real result off part-way, the way a dropped connection or a bad proxy would."""
        if len(text) <= 4:
            return ""
        return text[: max(1, len(text) // 3)]


# ------------------------------------------------------------- malformed results
#
# A malformed result parses as what the tool normally returns but is wrong in shape, like an API that changed under
# the harness or a proxy that mangles fields. Per result type (the first that applies):
#
#   JSON object     one of: a key renamed (snake_case -> camelCase, else Capitalized), a key dropped (objects with
#                   2+ keys), or the object wrapped as {"data": ...}
#   JSON array      one of: wrapped as {"items": [...], "count": n}, or (array of objects) one key renamed in every
#                   element. Several JSON values in a row (how a list of objects arrives) count as one array
#   JSON scalar     wrapped as {"value": ...}
#   delimited rows  2+ lines with the same number of tab, comma or "|" separators: the header line stays and every
#                   other row's fields shift right by one, so values sit under the wrong headers
#   key-value lines 2+ "key = value" / "key: value" lines (ini, yaml, grep's path:line: text): the values shift
#                   down one key (the last value goes to the first key); keys and other lines stay in place
#   lines           2+ other non-empty lines: rotated by one (the last line first)
#   one line        2+ words: the first two swap places
#
# For JSON the variant is picked by the call's draw; JSON comes back with indent 2, as the MCP servers send it.
# Nothing in a reshaped result mentions a fault.

_KV = re.compile(r"^(\s*)([^\s=:][^=:]*?)(\s*[=:]\s*)(.*)$")
_DELIMITERS = ("\t", ",", "|")


def _rename(key: str, taken: set[str]) -> str:
    camel = re.sub(r"_([a-z0-9])", lambda m: m.group(1).upper(), key)
    for cand in (camel, key[:1].upper() + key[1:], key.lower()):
        if cand != key and cand not in taken:
            return cand
    return key + "_"


def _renamed(obj: dict, key: str) -> dict:
    new = _rename(key, set(obj))
    return {(new if k == key else k): v for k, v in obj.items()}


def _json_values(text: str) -> list | None:
    """The JSON values `text` is made of (one, or several back to back), or None when it isn't JSON. Only text
    that starts like a JSON object, array or string counts: a file holding just `42` or `true` is text."""
    if text.lstrip()[:1] not in ('{', '[', '"'):
        return None
    dec, i, out = json.JSONDecoder(), 0, []
    while True:
        while i < len(text) and text[i].isspace():
            i += 1
        if i == len(text):
            return out or None
        try:
            value, i = dec.raw_decode(text, i)
        except ValueError:
            return None
        out.append(value)


def _dump(value) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False)


def _malform_json(values: list, draw: float) -> tuple[str, str]:
    stream = len(values) > 1
    value = values if stream else values[0]
    if isinstance(value, dict):
        keys = list(value)
        options = ["wrap"] + (["rename_key"] if keys else []) + (["drop_key"] if len(keys) >= 2 else [])
        how = options[int(draw * len(options))]
        if how == "wrap":
            return _dump({"data": value}), "json-object: wrapped in data"
        key = keys[int(draw * len(options) * len(keys)) % len(keys)]
        if how == "drop_key":
            return _dump({k: v for k, v in value.items() if k != key}), f"json-object: dropped {key}"
        new = _renamed(value, key)
        return _dump(new), f"json-object: renamed {key}"
    if isinstance(value, list):
        common = [k for k in value[0] if all(k in e for e in value)] \
            if value and all(isinstance(e, dict) for e in value) else []
        options = ["wrap"] + (["rename_key"] if common else [])
        how = options[int(draw * len(options))]
        if how == "wrap":
            return _dump({"items": value, "count": len(value)}), "json-array: wrapped in items"
        key = common[int(draw * len(options) * len(common)) % len(common)]
        renamed = [_renamed(e, key) for e in value]
        text = "\n".join(_dump(e) for e in renamed) if stream else _dump(renamed)
        return text, f"json-array: renamed {key} in every element"
    return _dump({"value": value}), "json-scalar: wrapped in value"


def _malform_text(text: str) -> tuple[str, str] | None:
    lines = text.split("\n")
    rows = [i for i, line in enumerate(lines) if line.strip()]
    if len(rows) >= 2:
        for d in _DELIMITERS:
            counts = {lines[i].count(d) for i in rows}
            if len(counts) == 1 and counts.pop() >= 1:
                out = list(lines)
                for i in rows[1:]:
                    fields = lines[i].split(d)
                    out[i] = d.join([fields[-1], *fields[:-1]])
                return "\n".join(out), "delimited: columns shifted"
        kv = [(i, m) for i in rows if (m := _KV.match(lines[i]))]
        if len(kv) >= 2:
            out = list(lines)
            values = [m.group(4) for _, m in kv]
            for (i, m), v in zip(kv, [values[-1], *values[:-1]], strict=True):
                out[i] = f"{m.group(1)}{m.group(2)}{m.group(3)}{v}"
            return "\n".join(out), "key-value: values shifted"
        out = list(lines)
        for i, j in zip(rows, [rows[-1], *rows[:-1]], strict=True):
            out[i] = lines[j]
        return "\n".join(out), "lines: rotated"
    if len(rows) == 1:
        line = lines[rows[0]]
        words = line.split(" ")
        idx = [k for k, w in enumerate(words) if w]
        if len(idx) >= 2:
            words[idx[0]], words[idx[1]] = words[idx[1]], words[idx[0]]
            out = list(lines)
            out[rows[0]] = " ".join(words)
            return "\n".join(out), "one line: fields swapped"
    return None


def malform(text: str, draw: float) -> tuple[str, str] | None:
    """`text` reshaped so it still parses but is wrong in shape (see the table above), and a label saying how;
    None when it has nothing to reshape (empty, one word, or reshaping changes nothing). draw is in [0, 1)."""
    values = _json_values(text)
    reshaped = _malform_json(values, draw) if values is not None else _malform_text(text)
    if reshaped is None or reshaped[0] == text:
        return None
    return reshaped
