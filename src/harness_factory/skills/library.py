"""Skill library (skills dimension): catalog/skills/<id>/SKILL.md, a short how-to a recipe can give the model.

A SKILL.md starts with a YAML header (name, description) and then the body. A recipe names skills by id and
picks how they reach the model (`skills.loading`):

  upfront     every named skill's body is appended to the system prompt (prompts/skills/upfront.j2)
  on_demand   the system prompt lists name + description only (prompts/skills/on_demand.j2); the model reads a
              body with the skills.load tool, served from the run's state folder (outside the workspace)

A skill's full text is part of the recipe hash, like a prompt template's source: editing a skill changes every
recipe that uses it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from harness_factory import settings


@dataclass(frozen=True)
class Skill:
    id: str
    name: str
    description: str
    body: str
    text: str            # the whole file, as hashed and as installed into the sandbox


def parse_skill(skill_id: str, text: str) -> Skill:
    if not text.startswith("---\n") or "\n---\n" not in text[4:]:
        raise ValueError(f"skill {skill_id!r}: SKILL.md must start with a --- YAML header ---")
    header, body = text[4:].split("\n---\n", 1)
    meta = yaml.safe_load(header) or {}
    missing = [k for k in ("name", "description") if not meta.get(k)]
    if missing:
        raise ValueError(f"skill {skill_id!r}: header needs {', '.join(missing)}")
    return Skill(id=skill_id, name=str(meta["name"]), description=" ".join(str(meta["description"]).split()),
                 body=body.strip(), text=text)


def load_skills(root: Path | None = None) -> dict[str, Skill]:
    root = root or settings.SKILLS_DIR
    out: dict[str, Skill] = {}
    if root.is_dir():
        for p in sorted(root.glob("*/SKILL.md")):
            out[p.parent.name] = parse_skill(p.parent.name, p.read_text())
    return out
