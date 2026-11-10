"""Catalog add-ons (ADR 0021): catalog folders that live outside this repository and plug into the factory.

An add-on is a folder named in `HF_ADDONS` (several are separated by os.pathsep). It can hold what a private or
separately versioned catalog needs, in the same layout as `catalog/`:

    <add-on>/addon.yaml               id, python_path, libraries, servers (below)
    <add-on>/catalog/tools.yaml       more MCP servers, declared and frozen like catalog/tools.yaml
    <add-on>/catalog/tasks/<id>/      more tasks
    <add-on>/catalog/recipes/**.yaml  more recipes (they may extend built-in ones with `base:`)
    <add-on>/catalog/conformance/     a conformance fixture per add-on server
    <add-on>/catalog/skills/<id>/     more skills (SKILL.md), by id like catalog/skills/
    <add-on>/prompts/**.j2            more prompt templates (system, task, stage...), by ref like prompts/

`addon.yaml`:

    id: my-addon
    python_path: [src]                # folders, relative to the add-on, its server modules import from
    libraries:                        # allow-list entries only this add-on's servers may import (ADR 0013)
    - {dist: some-lib, version: 1.2.3, imports: some_lib}
    - {dist: private-lib, version: 0.1.0, imports: private_lib, source: "git+https://...@<commit>"}
    servers:                          # server module -> the libraries it may import
      my_addon.server: [some-lib, private-lib]

Nothing in the repository depends on an add-on, and with `HF_ADDONS` unset the factory sees exactly its own
catalog. An add-on adds; it never replaces: a server, task or recipe id, a module or a library that clashes with
the built-in catalog or another add-on is refused, and so is a prompt ref or a skill id the built-in catalog or
another add-on already has. Add-on servers run in the local sandbox; the Docker sandbox
image carries only the built-in servers and refuses them. Their identity works as for built-in ones: tool specs,
pinned libraries and task contents go into the same hashes, wherever the files live.
"""

from __future__ import annotations

import functools
import os
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

ENV = "HF_ADDONS"


class AddonError(ValueError):
    pass


class _LibrarySpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dist: str
    version: str
    imports: str
    source: str | None = None


class _AddonFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    python_path: list[str] = Field(default_factory=list)
    libraries: list[_LibrarySpec] = Field(default_factory=list)
    servers: dict[str, list[str]] = Field(default_factory=dict)


class Addon(BaseModel):
    """One loaded add-on: where it is and what it declares."""
    model_config = ConfigDict(frozen=True)
    id: str
    root: Path
    python_path: tuple[Path, ...] = ()
    libraries: tuple[_LibrarySpec, ...] = ()
    servers: dict[str, tuple[str, ...]] = Field(default_factory=dict)

    @property
    def catalog(self) -> Path:
        return self.root / "catalog"

    @property
    def tools_file(self) -> Path:
        return self.catalog / "tools.yaml"

    @property
    def tasks_dir(self) -> Path:
        return self.catalog / "tasks"

    @property
    def recipes_dir(self) -> Path:
        return self.catalog / "recipes"

    @property
    def conformance_dir(self) -> Path:
        return self.catalog / "conformance"

    @property
    def skills_dir(self) -> Path:
        return self.catalog / "skills"

    @property
    def prompts_dir(self) -> Path:
        return self.root / "prompts"


def load_addon(root: Path) -> Addon:
    root = root.resolve()
    f = root / "addon.yaml"
    if not f.is_file():
        raise AddonError(f"{ENV}: {root} is not an add-on (no addon.yaml)")
    try:
        data = _AddonFile.model_validate(yaml.safe_load(f.read_text()) or {})
    except (ValidationError, yaml.YAMLError) as e:
        raise AddonError(f"{f}: {e}") from None
    paths = tuple((root / p).resolve() for p in data.python_path)
    for p in paths:
        if not p.is_dir() or not p.is_relative_to(root):
            raise AddonError(f"{f}: python_path {p} is not a folder inside the add-on")
    dists = {lib.dist for lib in data.libraries}
    for module, libs in data.servers.items():
        if module == "harness_factory" or module.startswith("harness_factory."):
            raise AddonError(f"{f}: server module {module} is in the factory's own package")
        unknown = sorted(set(libs) - dists)
        if unknown:
            raise AddonError(f"{f}: server {module} names libraries the add-on doesn't declare: {', '.join(unknown)}")
    return Addon(id=data.id, root=root, python_path=paths, libraries=tuple(data.libraries),
                 servers={m: tuple(v) for m, v in data.servers.items()})


def load(value: str | None) -> tuple[Addon, ...]:
    """The add-ons a HF_ADDONS value names, in order; ids, modules and libraries must not repeat."""
    out: list[Addon] = []
    for part in (value or "").split(os.pathsep):
        if part.strip():
            a = load_addon(Path(part.strip()).expanduser())
            for b in out:
                if a.id == b.id or a.root == b.root:
                    raise AddonError(f"{ENV}: add-on {a.id!r} ({a.root}) is listed twice")
                both = set(a.servers) & set(b.servers)
                if both:
                    raise AddonError(f"{ENV}: add-ons {b.id!r} and {a.id!r} both declare {', '.join(sorted(both))}")
                clash = {x.dist for x in a.libraries} & {x.dist for x in b.libraries}
                if clash:
                    raise AddonError(f"{ENV}: add-ons {b.id!r} and {a.id!r} both pin {', '.join(sorted(clash))}")
            out.append(a)
    return tuple(out)


@functools.lru_cache(maxsize=4)
def _cached(value: str) -> tuple[Addon, ...]:
    return load(value)


def active() -> tuple[Addon, ...]:
    """The add-ons named in the environment now. Read-only: loading is cached per HF_ADDONS value."""
    from harness_factory import settings  # noqa: F401  (importing it loads .env, where HF_ADDONS may be set)
    return _cached(os.environ.get(ENV, ""))


def tasks_dirs() -> list[Path]:
    return [a.tasks_dir for a in active() if a.tasks_dir.is_dir()]


def recipes_dirs() -> list[Path]:
    return [a.recipes_dir for a in active() if a.recipes_dir.is_dir()]


def conformance_dirs() -> list[Path]:
    return [a.conformance_dir for a in active() if a.conformance_dir.is_dir()]


def prompts_dirs() -> list[Path]:
    """The active add-ons' prompt folders, after checking that no ref repeats one the built-in prompts or an
    earlier add-on already have (a template that shadowed another would change a recipe without saying so)."""
    from harness_factory import settings
    dirs = [a.prompts_dir for a in active() if a.prompts_dir.is_dir()]
    seen = _refs(settings.PROMPTS)
    for d in dirs:
        refs = _refs(d)
        clash = sorted(refs & seen)
        if clash:
            raise AddonError(f"{d}: prompt refs already exist: {', '.join(clash)}")
        seen |= refs
    return dirs


def skills_dirs() -> list[Path]:
    """The active add-ons' skill folders, after checking that no skill id repeats one the built-in skills or an
    earlier add-on already have (a skill that shadowed another would change every recipe using it)."""
    from harness_factory import settings
    dirs = [a.skills_dir for a in active() if a.skills_dir.is_dir()]
    seen = _skill_ids(settings.SKILLS_DIR)
    for d in dirs:
        ids = _skill_ids(d)
        clash = sorted(ids & seen)
        if clash:
            raise AddonError(f"{d}: skill ids already exist: {', '.join(clash)}")
        seen |= ids
    return dirs


def _skill_ids(root: Path) -> set[str]:
    return {p.parent.name for p in root.glob("*/SKILL.md")} if root.is_dir() else set()


def _refs(root: Path) -> set[str]:
    if not root.is_dir():
        return set()
    return {str(p.relative_to(root).with_suffix("")).replace("\\", "/") for p in root.rglob("*.j2")}


def tools_files() -> list[tuple[Addon, Path]]:
    return [(a, a.tools_file) for a in active() if a.tools_file.is_file()]


def owner(module: str | None) -> Addon | None:
    """The add-on whose server this module is, if any."""
    return next((a for a in active() if module in a.servers), None) if module else None


def server_env(module: str | None) -> dict[str, str]:
    """What an add-on server process needs on top of the sandbox env: its folders on PYTHONPATH. {} for others."""
    a = owner(module)
    if a is None or not a.python_path:
        return {}
    rest = os.environ.get("PYTHONPATH", "")
    return {"PYTHONPATH": os.pathsep.join([*map(str, a.python_path), *([rest] if rest else [])])}
