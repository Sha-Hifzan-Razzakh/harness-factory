"""HarnessRecipe and its parts (B2). A recipe fully defines one harness; identity = content hash.

The model under test is a campaign axis, so ModelSlot.canonical_model is left empty in recipe files
and filled per cell. The recipe hash excludes it, so one recipe compares across models.
"""

from __future__ import annotations

import fnmatch
from enum import Enum
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, model_validator

from harness_factory import settings
from harness_factory.hashing import content_hash

PromptSources = dict[str, str]          # template ref ("system/default") -> template source text


def default_prompt_sources(root: Path | None = None) -> PromptSources:
    """Every .j2 under the prompts directory, keyed by ref. Missing templates are a validator finding, not a
    hashing error, so a ref with no file simply contributes nothing here."""
    root = root or settings.PROMPTS
    if not root.is_dir():
        return {}
    return {str(p.relative_to(root).with_suffix("")).replace("\\", "/"): p.read_text() for p in sorted(root.rglob("*.j2"))}

Tier = Literal["T1", "T2", "T3", "T4"]
SideEffectName = Literal["READ", "REVERSIBLE", "IRREVERSIBLE"]
PermissionModeName = Literal["AUTO", "ASK", "DENY"]


class Variant(str, Enum):
    V1_SINGLE_CALL = "V1_SINGLE_CALL"
    V2_TOOL_LOOP = "V2_TOOL_LOOP"
    V3_WORKFLOW_GRAPH = "V3_WORKFLOW_GRAPH"
    V4_PLANNER = "V4_PLANNER"
    V5_ORCHESTRATOR = "V5_ORCHESTRATOR"


class ModelSlot(BaseModel):
    role: Literal["planner", "executor", "judge", "profiler"] = "executor"
    canonical_model: str | None = None
    temperature: float = 0.0
    max_output_tokens: int = 4096
    seed: int | None = None


class ToolSlice(BaseModel):
    tool_ids: list[str]                       # patterns like "fs.*" or "sql.query"
    mcp_servers: list[str]
    deferred: bool = False
    defer_above_tokens: int = 8000
    binding: Literal["mcp", "native"] = "mcp"

    def allows(self, tool_ref: str) -> bool:
        return any(fnmatch.fnmatch(tool_ref, pat) for pat in self.tool_ids)


class SkillSet(BaseModel):
    skill_ids: list[str] = Field(default_factory=list)
    loading: Literal["none", "upfront", "on_demand"] = "none"


class PromptLayers(BaseModel):
    system: str = "system/default"
    operator: str | None = None
    task: str = "task/default"


class RuntimeConfig(BaseModel):
    max_steps: int = Field(30, gt=0)
    max_tool_calls: int = Field(50, gt=0)
    max_tokens: int = Field(200_000, gt=0)
    wall_time_s: int = Field(600, gt=0)
    retries: int = 2
    backoff_s: float = 2.0
    compaction_at: float = 0.8
    parallel_tool_calls: bool = False
    call_timeout_s: int | None = Field(None, gt=0)    # per model call; None = settings.CALL_TIMEOUT_S


class PolicySet(BaseModel):
    permission_mode: dict[SideEffectName, PermissionModeName] = Field(
        default_factory=lambda: {"READ": "AUTO", "REVERSIBLE": "AUTO", "IRREVERSIBLE": "DENY"}
    )
    approval_rules: list[str] = Field(default_factory=list)


class MemoryProfile(BaseModel):
    working: bool = True
    summary: bool = False
    vector: bool = False
    structured: bool = False
    scope: Literal["run", "session"] = "run"


class RetrievalConfig(BaseModel):
    mode: Literal["none", "bm25", "dense", "hybrid"] = "none"
    top_k: int = 8
    rerank: bool = False


class WorkflowStage(BaseModel):
    """One node of a V3 workflow: a bounded tool loop with its own instruction and tool subset."""
    id: str
    prompt: str                                       # prompts/<ref>.j2, appended as a user message at stage start
    tools: list[str] = Field(default_factory=list)    # fnmatch patterns, narrowed further by the run's allowed tools
    max_steps: int = Field(5, gt=0)                   # model calls in this stage before it is ended

    def allows(self, tool_ref: str) -> bool:
        return any(fnmatch.fnmatch(tool_ref, pat) for pat in self.tools)


class WorkflowSpec(BaseModel):
    """V3 workflow graph: stages run in order; the last stage may send the run back once ("RETRY: ...")."""
    stages: list[WorkflowStage] = Field(min_length=1)
    retry_from: str | None = None                     # stage id to return to when the last stage asks to retry
    max_retries: int = Field(1, ge=0)

    @model_validator(mode="after")
    def _check(self) -> WorkflowSpec:
        ids = [s.id for s in self.stages]
        if len(ids) != len(set(ids)):
            raise ValueError(f"duplicate stage ids: {ids}")
        if self.retry_from is not None and self.retry_from not in ids:
            raise ValueError(f"retry_from {self.retry_from!r} is not a stage id")
        return self

    def index(self, stage_id: str) -> int:
        return [s.id for s in self.stages].index(stage_id)


# Optional fields added after results were first recorded. When unset they are dropped before hashing, so
# adding a field never changes the identity of existing recipes (see CLAUDE.md, "Run identity"). Dotted = nested.
_HASH_OMIT_WHEN_NONE = ("workflow", "config.call_timeout_s")

# Campaign axes: set per run, never part of the recipe's identity. The model is a cell axis; the seed is a RunKey
# axis. Both are nulled before hashing.
_SLOT_AXES = ("canonical_model", "seed")


def _drop_none(data: dict, dotted: str) -> None:
    *path, last = dotted.split(".")
    node = data
    for key in path:
        node = node.get(key) or {}
    if isinstance(node, dict) and node.get(last) is None:
        node.pop(last, None)


class HarnessRecipe(BaseModel):
    id: str
    version: int = 1
    base: str | None = None
    variant: Variant
    headless: bool = True
    tier: Tier = "T2"
    model_slots: dict[str, ModelSlot] = Field(default_factory=lambda: {"executor": ModelSlot()})
    tools: ToolSlice
    skills: SkillSet = Field(default_factory=SkillSet)
    prompts: PromptLayers = Field(default_factory=PromptLayers)
    config: RuntimeConfig = Field(default_factory=RuntimeConfig)
    policies: PolicySet = Field(default_factory=PolicySet)
    memory: MemoryProfile = Field(default_factory=MemoryProfile)
    retrieval: RetrievalConfig | None = None
    grader_hooks: list[str] = Field(default_factory=list)
    workflow: WorkflowSpec | None = None              # required by V3_WORKFLOW_GRAPH, rejected elsewhere

    def prompt_refs(self) -> list[str]:
        refs = [self.prompts.system, self.prompts.task] + ([self.prompts.operator] if self.prompts.operator else [])
        if self.workflow:
            refs += [st.prompt for st in self.workflow.stages]
        return refs

    def content_hash(self, prompts: PromptSources | None = None) -> str:
        """Identity of the harness: the recipe minus its campaign axes, plus the source of every prompt
        template it references. Editing a template changes behaviour, so it changes the hash.

        `prompts` maps template ref -> source text; by default it is read from the prompts directory.
        """
        data = self.model_dump(mode="json")
        for slot in data["model_slots"].values():
            for axis in _SLOT_AXES:
                slot[axis] = None
        for dotted in _HASH_OMIT_WHEN_NONE:
            _drop_none(data, dotted)
        sources = prompts if prompts is not None else default_prompt_sources()
        data["prompt_sources"] = {ref: sources[ref] for ref in sorted(set(self.prompt_refs())) if ref in sources}
        return content_hash(data)

    def with_model(self, canonical_model: str, role: str = "executor") -> HarnessRecipe:
        slots = {k: v.model_copy() for k, v in self.model_slots.items()}
        slots.setdefault(role, ModelSlot(role=role))
        slots[role] = slots[role].model_copy(update={"canonical_model": canonical_model})
        return self.model_copy(update={"model_slots": slots})

    def to_mlflow_params(self) -> dict[str, str]:
        return {
            "recipe_id": self.id, "recipe_hash": self.content_hash(), "variant": self.variant.value,
            "tier": self.tier, "tools": ",".join(self.tools.tool_ids), "skills_loading": self.skills.loading,
            "memory": "+".join(k for k in ("working", "summary", "vector", "structured") if getattr(self.memory, k)),
            "max_steps": str(self.config.max_steps), "max_tool_calls": str(self.config.max_tool_calls),
        }


def load_recipe(path: Path) -> HarnessRecipe:
    return HarnessRecipe.model_validate(yaml.safe_load(path.read_text()))


def load_recipes(root: Path) -> dict[str, HarnessRecipe]:
    out: dict[str, HarnessRecipe] = {}
    for p in sorted(root.rglob("*.yaml")):
        r = load_recipe(p)
        out[r.id] = r
    return out
