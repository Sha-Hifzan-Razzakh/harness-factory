"""What authoring takes in (one model per kind of catalog item) and what it gives back (a ChangeResult)."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from harness_factory.authoring.toolcode import ToolDef

Kind = Literal["prompt", "skill", "recipe", "server", "fault"]
KINDS: tuple[str, ...] = ("prompt", "skill", "recipe", "server", "fault")

SLUG = re.compile(r"^[a-z0-9][a-z0-9_\-]{0,62}$")
PROMPT_REF = re.compile(r"^[a-z0-9_]+/[a-z0-9_\-]+$")


def _slug(v: str) -> str:
    if not SLUG.match(v):
        raise ValueError(f"{v!r}: use lowercase letters, digits, '-' or '_' (start with a letter or digit)")
    return v


class PromptIn(BaseModel):
    ref: str                     # "<layer>/<name>", e.g. "system/terse"; stored as prompts/<ref>.j2
    text: str = Field(min_length=1)

    @field_validator("ref")
    @classmethod
    def _ref(cls, v: str) -> str:
        if not PROMPT_REF.match(v):
            raise ValueError(f"{v!r}: a prompt ref is <layer>/<name>, e.g. system/terse")
        return v


class SkillIn(BaseModel):
    id: str
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    body: str = Field(min_length=1)

    @field_validator("id")
    @classmethod
    def _id(cls, v: str) -> str:
        return _slug(v)


class RecipeIn(BaseModel):
    """A recipe file. With `base`, `fields` holds only what changes (a matrix recipe: one dimension); without,
    `fields` is the whole recipe. The model slot stays empty: the model is a campaign axis."""
    id: str
    base: str | None = "v2-base"
    fields: dict[str, Any] = Field(default_factory=dict)
    note: str = ""               # first-line comment: what this recipe changes and why

    @field_validator("id")
    @classmethod
    def _id(cls, v: str) -> str:
        return _slug(v)


class ServerIn(BaseModel):
    """A tool server: a new capability goes on its own server, so existing environment hashes stay.

    Local (stdio): give `code` (a whole server module) or leave it empty for a stub generated from the tools'
    params. Remote (streamable_http): an existing MCP server; `url`, a pinned `version` and the *name* of the env
    var that holds its token (never the token itself)."""
    id: str
    transport: Literal["stdio", "streamable_http"] = "stdio"
    instructions: str = ""
    tools: list[ToolDef] = Field(min_length=1)
    code: str | None = None
    url: str | None = None
    version: str | None = None
    auth_env: str | None = None
    tenant: dict[str, Any] | None = None
    agents: dict[str, dict[str, Any]] = Field(default_factory=dict)   # tool name -> {max_calls, price_in, ...}

    @field_validator("id")
    @classmethod
    def _sid(cls, v: str) -> str:
        if not re.match(r"^[a-z][a-z0-9_]{1,30}$", v):
            raise ValueError(f"{v!r}: a server id is lowercase letters, digits or '_' (it names a Python module)")
        return v

    @field_validator("auth_env")
    @classmethod
    def _env_name(cls, v: str | None) -> str | None:
        if v is not None and not re.match(r"^[A-Z][A-Z0-9_]*$", v):
            raise ValueError("auth_env is the NAME of an environment variable (e.g. KB_MCP_TOKEN), not a token")
        return v


class FaultIn(BaseModel):
    id: str
    description: str = ""
    rules: list[dict[str, Any]] = Field(min_length=1)

    @field_validator("id")
    @classmethod
    def _id(cls, v: str) -> str:
        return _slug(v)


PAYLOADS: dict[str, type[BaseModel]] = {"prompt": PromptIn, "skill": SkillIn, "recipe": RecipeIn,
                                        "server": ServerIn, "fault": FaultIn}


class Issue(BaseModel):
    where: str                   # "recipe v2-x", "prompt system/terse", "server calc", ...
    detail: str


class FileChange(BaseModel):
    path: str                    # relative to the repository root
    action: Literal["write", "delete"]


class Impact(BaseModel):
    """What the change does to existing identities. Empty means no existing result is affected."""
    recipes_changed: list[str] = Field(default_factory=list)        # existing recipes whose hash changes
    recipes_added: list[str] = Field(default_factory=list)
    recipes_removed: list[str] = Field(default_factory=list)
    env_changed: dict[str, list[str]] = Field(default_factory=dict)  # server -> existing recipes that open it
    stale_results: int | None = None     # stored runs of the changed recipes at their current identity

    @property
    def touches_existing(self) -> bool:
        return bool(self.recipes_changed or self.recipes_removed or any(self.env_changed.values()))


class ChangeResult(BaseModel):
    kind: str
    id: str
    action: Literal["create", "update", "delete"]
    status: Literal["applied", "dry_run", "needs_confirm", "rejected"]
    files: list[FileChange] = Field(default_factory=list)
    issues: list[Issue] = Field(default_factory=list)       # blocking: nothing was written
    notes: list[str] = Field(default_factory=list)          # not blocking
    impact: Impact = Field(default_factory=Impact)

    @property
    def ok(self) -> bool:
        return self.status in ("applied", "dry_run")


class Item(BaseModel):
    """One catalog item as listed or shown."""
    kind: str
    id: str
    path: str | None = None
    summary: str = ""
    hash: str | None = None
    used_by: list[str] = Field(default_factory=list)
    builtin: bool = False
    issues: list[str] = Field(default_factory=list)
    detail: dict[str, Any] = Field(default_factory=dict)
    text: str | None = None              # file contents, on show
