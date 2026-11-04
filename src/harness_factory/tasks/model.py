"""Task, Oracle, Depth (B1). A task is versioned data; its identity is its content hash."""

from __future__ import annotations

import json
import math
import re
from enum import Enum
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, model_serializer, model_validator

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
                    "biz_equals", "biz_count", "xlsx_cell", "docx_contains",
                    "docx_paragraph_regex", "sql_rows_unordered", "number_close", "json_schema", "output_exact",
                    "rubric", "state_equals", "state_count"]
GIT_CHECKS = ("git_log_contains",)
STATE_CHECKS = ("state_equals", "state_count")    # any stateful mock server's JSON state: path "<state>:<pointer>"

Normalization = Literal["whitespace", "case"]

# Check fields added after the first tasks were written: left out of the task hash while unset, so adding them
# kept every existing task hash (like _HASH_OMIT_WHEN_NONE for recipes).
_CHECK_HASH_OMIT_WHEN_NONE = ("ref", "sheet", "cell", "expected", "tolerance", "style",
                              "value", "abs_tol", "rel_tol", "json_schema", "normalize", "rubric", "touches")

_SCALAR = (str, int, float, bool, type(None))


class Criterion(BaseModel):
    """One thing a rubric judge scores, 0 (not met), 0.5 (partly) or 1 (met)."""
    id: str = Field(pattern=r"^[A-Za-z0-9_.-]+$")
    text: str = Field(min_length=1)
    weight: float = Field(gt=0, le=1)


class Rubric(BaseModel):
    """What a judge model scores an open-ended deliverable against (ADR 0014). The judge sees only the criteria
    texts and the subject, never the threshold or the task's other checks; the agent never sees any of it."""
    criteria: list[Criterion] = Field(min_length=1)
    threshold: float = Field(default=0.7, gt=0, le=1)   # weighted criterion score at which the check passes

    @model_validator(mode="after")
    def _well_formed(self) -> Rubric:
        ids = [c.id for c in self.criteria]
        if len(set(ids)) != len(ids):
            raise ValueError(f"rubric criteria ids must be unique, got {ids}")
        total = sum(c.weight for c in self.criteria)
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"rubric criteria weights must sum to 1.0, got {total:.4f}")
        return self


class WeightedCheck(BaseModel):
    id: str
    kind: CheckKind
    weight: float = Field(ge=0, le=1)  # 0 = guard check: earns nothing, but failing it is critical
    critical: bool = False
    path: str | None = None          # file_* checks, relative to the workspace; remote_equals: "<server>:<JSON
                                     # pointer>" into that server's test tenant after the run (EXT-07);
                                     # biz_equals / biz_count: a JSON pointer into the business state after the run;
                                     # state_equals / state_count: "<state>:<JSON pointer>" into any JSON state
                                     # (env/state.JSON_STATES), e.g. "outbox:/sent"; same comparison as biz_*
    text: str | None = None          # file_equals / file_contains; remote_equals / biz_equals: the value (a string,
                                     # or JSON, compared strictly on type); biz_count: the expected count
    negate: bool = False             # file_contains / file_regex / remote_equals / biz_equals: must NOT match
    query: str | None = None         # sql_rows; biz_count: optional JSON object of field values a record must have
    rows: list[list[Any]] | None = None
    pattern: str | None = None       # output_regex, file_regex (re.MULTILINE; add (?s) to cross lines);
                                     # tool_called (fnmatch on tool ref, successful calls only);
                                     # tests_pass: a pytest -k expression; git_log_contains: a commit message regex;
                                     # docx_paragraph_regex (re.search per paragraph; negate: no paragraph matches)
                                     # number_close: where the number is in the file or answer (group 1, or
                                     # the whole match)
    # tests_pass (ADR 0012): `path` is what to run in the workspace (default "."); `text` is a hidden test module
    # run from outside the workspace instead, with the workspace importable. git_log_contains: `ref` (default HEAD)
    # must have a commit after the seed whose message matches `pattern` (and that touches `path`, if given).
    ref: str | None = None
    sheet: str | None = None         # xlsx_cell: the sheet name (path is the workbook)
    cell: str | None = None          # xlsx_cell: A1-style cell
    expected: str | int | float | bool | None = None   # xlsx_cell: the value; omitted = the cell is empty
    tolerance: float | None = Field(default=None, ge=0)  # xlsx_cell with a number: |actual - expected| <= this
    style: str | None = None         # docx_paragraph_regex: the matching paragraph must have this style
    value: float | None = None       # number_close: the expected number
    abs_tol: float | None = None     # number_close: |actual - value| <= max(abs_tol, rel_tol * max(|a|, |v|));
    rel_tol: float | None = None     # neither set = rel_tol 1e-9 (math.isclose); abs_tol alone = absolute only
    json_schema: dict[str, Any] | None = None   # json_schema: the schema the file must validate against
    normalize: list[Normalization] | None = None   # output_exact: "whitespace" collapses runs of whitespace,
                                                   # "case" ignores case; ends are always trimmed
    rubric: Rubric | None = None     # rubric: what the judge scores; the subject is the file at `path`, or the
                                     # final answer when there is no path (grading/judge.py, ADR 0014)
    touches: list[str] | None = Field(default=None, min_length=1)   # tool_called: the matching call must touch
                                     # the task's data: a SQL table its statement names, a workspace path or glob
                                     # its path argument matches, or `arg=value` for other tools (grading/touches.py)

    @model_validator(mode="after")
    def _guard_is_critical(self) -> WeightedCheck:
        if self.weight == 0 and not self.critical:
            raise ValueError(f"check {self.id}: a zero-weight check must be critical")
        needs = {"file_equals": ("path", "text"), "file_contains": ("path", "text"), "file_regex": ("path", "pattern"),
                 "file_absent": ("path",), "json_valid": ("path",), "sql_rows": ("query", "rows"),
                 "output_regex": ("pattern",), "tool_called": ("pattern",),
                 "remote_equals": ("path", "text"), "tests_pass": (), "git_log_contains": ("pattern",),
                 "biz_equals": ("path", "text"), "biz_count": ("path", "text"),
                 "xlsx_cell": ("path", "sheet", "cell"), "docx_contains": ("path", "text"),
                 "docx_paragraph_regex": ("path", "pattern"), "sql_rows_unordered": ("query", "rows"),
                 "number_close": ("value",), "json_schema": ("path", "json_schema"), "output_exact": ("text",),
                 "rubric": ("rubric",), "state_equals": ("path", "text"), "state_count": ("path", "text")}[self.kind]
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
            self._count_fields()
        if self.kind in STATE_CHECKS:
            self._state_fields()
        if self.kind == "xlsx_cell":
            if not re.fullmatch(r"[A-Za-z]{1,3}[1-9]\d*", self.cell or ""):
                raise ValueError(f"check {self.id}: xlsx_cell cell is one A1-style cell, e.g. 'C4'")
            if self.tolerance is not None and (isinstance(self.expected, bool)
                                               or not isinstance(self.expected, (int, float))):
                raise ValueError(f"check {self.id}: tolerance applies only to a number expected")
        for name in ("sheet", "cell", "expected", "tolerance"):
            if getattr(self, name) is not None and self.kind != "xlsx_cell":
                raise ValueError(f"check {self.id}: {name} is for xlsx_cell checks")
        if self.touches is not None and (self.kind != "tool_called" or not all(t.strip() for t in self.touches)):
            raise ValueError(f"check {self.id}: touches is for tool_called checks: tables, paths or arg=value, "
                             f"none empty")
        if self.style is not None and self.kind != "docx_paragraph_regex":
            raise ValueError(f"check {self.id}: style is for docx_paragraph_regex checks")
        if self.pattern is not None and self.kind in ("file_regex", "output_regex", "git_log_contains",
                                                      "docx_paragraph_regex", "number_close"):
            try:
                re.compile(self.pattern)
            except re.error as e:
                raise ValueError(f"check {self.id}: bad pattern: {e}") from e
        if self.kind == "sql_rows_unordered":
            for row in self.rows or []:
                for v in row:
                    if not isinstance(v, _SCALAR) or (isinstance(v, float) and math.isnan(v)):
                        raise ValueError(f"check {self.id}: sql_rows_unordered rows hold SQL values (text, numbers, "
                                         f"null), not {v!r}")
        if self.kind == "number_close":
            self._number_close_fields()
        if self.rubric is not None and self.kind != "rubric":
            raise ValueError(f"check {self.id}: only a rubric check takes a rubric")
        if self.kind == "rubric" and (self.weight == 0 or self.critical):
            raise ValueError(f"check {self.id}: a rubric check earns weight and is never critical (a judge's "
                             f"opinion can't fail a run outright); guard with a deterministic check")
        if self.kind == "json_schema":
            from jsonschema import SchemaError, validators
            try:
                validators.validator_for(self.json_schema).check_schema(self.json_schema)
            except SchemaError as e:
                raise ValueError(f"check {self.id}: bad JSON schema: {e.message}") from e
        return self

    def _count_fields(self) -> None:
        if not (self.text or "").isdigit():
            raise ValueError(f"check {self.id}: {self.kind} text is the expected count, a whole number")
        if self.query is not None:
            try:
                where = json.loads(self.query)
            except ValueError as e:
                raise ValueError(f"check {self.id}: {self.kind} query is a JSON object: {e}") from e
            if not isinstance(where, dict):
                raise ValueError(f"check {self.id}: {self.kind} query is a JSON object of field values")

    def _state_fields(self) -> None:
        """state_equals / state_count: like biz_equals / biz_count, on the named state ("calendar:/events")."""
        from harness_factory.env.state import JSON_STATES
        name, sep, pointer = (self.path or "").partition(":")
        if name not in JSON_STATES or not sep or (pointer and not pointer.startswith("/")):
            raise ValueError(f"check {self.id}: {self.kind} path is '<state>:<JSON pointer>' with state one of "
                             f"{', '.join(JSON_STATES)}, e.g. 'calendar:/events/EV-0107/start'")
        if self.kind == "state_count":
            self._count_fields()
        elif self.query is not None:
            raise ValueError(f"check {self.id}: query is for state_count, not state_equals")

    def state_name(self) -> str | None:
        """The JSON state a state check reads (biz checks read biz)."""
        if self.kind in ("biz_equals", "biz_count"):
            return "biz"
        return (self.path or "").partition(":")[0] if self.kind in STATE_CHECKS else None

    def _number_close_fields(self) -> None:
        """The number comes from exactly one place: a SQL scalar (query), a file (path, optionally pattern), or the
        final answer (pattern, or the whole answer)."""
        if self.query is not None and (self.path is not None or self.pattern is not None):
            raise ValueError(f"check {self.id}: number_close reads a SQL scalar (query) or text (path / pattern), "
                             f"not both")
        if not math.isfinite(self.value):
            raise ValueError(f"check {self.id}: number_close value must be a finite number")
        for name in ("abs_tol", "rel_tol"):
            tol = getattr(self, name)
            if tol is not None and not (math.isfinite(tol) and tol >= 0):
                raise ValueError(f"check {self.id}: {name} must be a finite number >= 0")


class Oracle(BaseModel):
    checks: list[WeightedCheck]
    pass_threshold: float = 1.0

    @model_validator(mode="after")
    def _weights_sum_to_one(self) -> Oracle:
        total = sum(c.weight for c in self.checks)
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"oracle weights must sum to 1.0, got {total:.4f}")
        if self.rubric_checks() and not any(c.weight > 0 for c in self.checks if c.kind != "rubric"):
            raise ValueError("an oracle with rubric checks needs a deterministic check with weight > 0: an "
                             "uncalibrated judge carries weight 0, and the outcome must still be measurable")
        return self

    def rubric_checks(self) -> list[WeightedCheck]:
        return [c for c in self.checks if c.kind == "rubric"]


class EnvSeed(BaseModel):
    files: str | None = None          # directory copied into the workspace (relative to the task dir)
    sqlite: str | None = None         # SQL script that builds the task database
    mock_api_state: dict[str, Any] = Field(default_factory=dict)   # server id -> its test tenant's seed (EXT-07)
    memory_seed: list[dict[str, Any]] = Field(default_factory=list)
    # A git repository over the seed files with one commit, made identically everywhere (ADR 0012). Hashed only
    # when set, so tasks without it keep their hash.
    git: bool = False
    biz_state: str | None = None      # JSON file: the biz server's business state (a fresh copy each run)
    # Other stateful mock servers' seeds (env/state.SERVER_STATES): state name -> JSON file, a fresh copy each run.
    server_state: dict[str, str] | None = None

    @model_validator(mode="after")
    def _known_states(self) -> EnvSeed:
        from harness_factory.env.state import SERVER_STATES
        bad = sorted(set(self.server_state or {}) - set(SERVER_STATES))
        if bad:
            raise ValueError(f"env_seed.server_state: unknown state {', '.join(bad)} (known: "
                             f"{', '.join(SERVER_STATES)}; biz is seeded by biz_state)")
        if self.server_state == {}:
            raise ValueError("env_seed.server_state is empty: leave it out")
        return self

    def resolve(self, task_dir: Path) -> tuple[Path | None, Path | None]:
        f = (task_dir / self.files).resolve() if self.files else None
        s = (task_dir / self.sqlite).resolve() if self.sqlite else None
        return f, s

    def biz_seed(self, task_dir: Path) -> Path | None:
        return (task_dir / self.biz_state).resolve() if self.biz_state else None

    def state_seeds(self, task_dir: Path) -> dict[str, Path]:
        """Every JSON state the task seeds, biz included: state name -> seed file."""
        out = {name: (task_dir / rel).resolve() for name, rel in sorted((self.server_state or {}).items())}
        biz = self.biz_seed(task_dir)
        return {"biz": biz, **out} if biz is not None else out

    def data_hash(self, task_dir: Path) -> str:
        f, s = self.resolve(task_dir)
        return hash_paths([p for p in (f, s, *self.state_seeds(task_dir).values()) if p is not None])


# Seed fields added after tasks were first hashed: left out of the task hash while unset, so existing task hashes
# stay the same (like recipes' _HASH_OMIT_WHEN_NONE).
_SEED_OMIT_WHEN_NONE = ("biz_state", "server_state")

# Perturbation types (TSK-04). A variant made from a base task by `hfx perturb` (tasks/perturb.py) records how it was
# made here; the block is part of the variant's hash. memory_poisoning is reserved for MEM-06 (memory stores, #78)
# and refused until it is built.
PerturbationType = Literal["tool_failure", "slow_tool", "injected_instruction", "oversized_output",
                           "ambiguous_wording", "memory_poisoning"]
PERTURBATION_TYPES: tuple[str, ...] = ("tool_failure", "slow_tool", "injected_instruction", "oversized_output",
                                       "ambiguous_wording")
RESERVED_PERTURBATIONS = {"memory_poisoning": "MEM-06 (memory poisoning) needs memory stores (#78); not built yet"}
FAULT_KINDS_BY_TYPE = {"tool_failure": {"error", "timeout", "rate_limit", "empty"}, "slow_tool": {"latency"}}


class Perturbation(BaseModel):
    """How a variant differs from its base. tool_failure / slow_tool carry `params.faults`: fault rules (env/faults)
    bound to every run of the variant, as part of its environment, so the task hash covers them."""
    type: PerturbationType
    params: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _known(self) -> Perturbation:
        if self.type in RESERVED_PERTURBATIONS:
            raise ValueError(f"perturbation {self.type}: {RESERVED_PERTURBATIONS[self.type]}")
        kinds = FAULT_KINDS_BY_TYPE.get(self.type)
        if kinds is None:
            if "faults" in self.params:
                raise ValueError(f"perturbation {self.type} binds no faults; only tool_failure and slow_tool do")
            return self
        rules = self.fault_rules()
        if not rules:
            raise ValueError(f"perturbation {self.type} needs params.faults: at least one fault rule")
        bad = sorted({r.kind for r in rules} - kinds)
        if bad:
            raise ValueError(f"perturbation {self.type} takes fault kinds {sorted(kinds)}, not {bad}")
        return self

    def fault_rules(self) -> list:
        from harness_factory.env.faults import FaultRule
        return [FaultRule.model_validate(r) for r in self.params.get("faults") or []]


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
    # TSK-04: set on a variant generated from base_id (tasks/perturb.py). Left out of model_dump, and so of the hash,
    # while unset: every other task keeps its hash.
    perturbation: Perturbation | None = None

    # set by the loader, not part of identity
    source_dir: Path | None = Field(default=None, exclude=True)

    @model_serializer(mode="wrap")
    def _omit_unset_perturbation(self, handler):
        data = handler(self)
        if isinstance(data, dict) and data.get("perturbation", 0) is None:
            data.pop("perturbation")
        return data

    def key(self) -> str:
        return f"{self.id}@{self.version}"

    @model_validator(mode="after")
    def _git_checks_need_a_repo(self) -> Task:
        gits = [c.id for c in self.oracle.checks if c.kind in GIT_CHECKS]
        if gits and not self.env_seed.git:
            raise ValueError(f"task {self.id}: git checks ({', '.join(gits)}) need `env_seed.git: true`")
        if self.perturbation is not None and not self.base_id:
            raise ValueError(f"task {self.id}: a perturbation variant names its base task (base_id)")
        seeded = set(self.env_seed.server_state or {}) | ({"biz"} if self.env_seed.biz_state else set())
        unseeded = sorted(f"{c.id} ({c.state_name()})" for c in self.oracle.checks
                          if c.kind in STATE_CHECKS and c.state_name() not in seeded)
        if unseeded:
            raise ValueError(f"task {self.id}: state checks on a state the task doesn't seed: {', '.join(unseeded)}")
        return self

    def bound_faults(self):
        """The fault profile bound to every run of this task (a tool_failure / slow_tool variant), or None. It is
        part of the task, not a campaign axis: its runs are clean runs of the variant (fault_config_hash "none")."""
        rules = self.perturbation.fault_rules() if self.perturbation is not None else []
        if not rules:
            return None
        from harness_factory.env.faults import FaultConfig
        return FaultConfig(id=f"task:{self.id}", description=f"bound by {self.id} ({self.perturbation.type})",
                           rules=rules)

    def content_hash(self) -> str:
        data = self.model_dump(mode="json", exclude={"status", "split", "tags"})   # labels, not identity
        # Fields added after tasks were first hashed: dropped when unset, so existing task hashes don't change.
        for k in _SEED_OMIT_WHEN_NONE:
            if data["env_seed"].get(k) is None:
                data["env_seed"].pop(k, None)
        for c in data["oracle"]["checks"]:
            for k in _CHECK_HASH_OMIT_WHEN_NONE:
                if c.get(k) is None:
                    c.pop(k, None)
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
