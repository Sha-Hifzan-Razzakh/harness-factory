"""RunKey and Cell (B7). Same run key => same task, harness, model, provider model, tools, seed and faults.

env_hash covers what the recipe references by name but does not contain: the declared tool specs (with their
schema hashes) of the servers the run opens, and the provider's model id behind the canonical model.
"""

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
    env_hash: str = "none"

    def as_str(self) -> str:
        return "|".join([self.task_version, self.task_hash, self.recipe_hash, self.canonical_model,
                         self.provider, str(self.seed), self.fault_config_hash, self.env_hash])

    def short(self) -> str:
        return content_hash(self.as_str(), 12)

    @staticmethod
    def from_str(s: str) -> RunKey:
        tv, th, rh, m, p, seed, f, env = s.split("|")
        return RunKey(task_version=tv, task_hash=th, recipe_hash=rh, canonical_model=m, provider=p,
                      seed=int(seed), fault_config_hash=f, env_hash=env)


class Cell(BaseModel):
    task_class: str
    recipe_hash: str
    canonical_model: str

    @property
    def id(self) -> str:
        return f"{self.task_class}|{self.recipe_hash}|{self.canonical_model}"
