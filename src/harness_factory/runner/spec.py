"""RunKey and Cell (B7). Same run key => same environment, prompt and config."""

from __future__ import annotations

from pydantic import BaseModel

from harness_factory.hashing import content_hash


class RunKey(BaseModel):
    task_version: str          # "fs-01@1"
    task_hash: str
    recipe_hash: str
    canonical_model: str
    provider: str
    seed: int = 0
    fault_config_hash: str = "none"

    def as_str(self) -> str:
        return "|".join([self.task_version, self.task_hash, self.recipe_hash, self.canonical_model,
                         self.provider, str(self.seed), self.fault_config_hash])

    def short(self) -> str:
        return content_hash(self.as_str(), 12)

    @staticmethod
    def from_str(s: str) -> RunKey:
        tv, th, rh, m, p, seed, f = s.split("|")
        return RunKey(task_version=tv, task_hash=th, recipe_hash=rh, canonical_model=m, provider=p,
                      seed=int(seed), fault_config_hash=f)


class Cell(BaseModel):
    task_class: str
    recipe_hash: str
    canonical_model: str

    @property
    def id(self) -> str:
        return f"{self.task_class}|{self.recipe_hash}|{self.canonical_model}"
