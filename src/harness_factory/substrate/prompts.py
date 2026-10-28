"""PromptRegistry and PromptAssembler (B3). Prompt assembly is deterministic; its hash goes in the trace."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from harness_factory import settings
from harness_factory.hashing import content_hash
from harness_factory.recipes.model import HarnessRecipe
from harness_factory.tasks.model import Task


class PromptRegistry:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or settings.PROMPTS
        self.env = Environment(loader=FileSystemLoader(str(self.root)), undefined=StrictUndefined,
                               keep_trailing_newline=False)

    def exists(self, ref: str) -> bool:
        return (self.root / f"{ref}.j2").is_file()

    def render(self, ref: str, **vars: Any) -> str:
        return self.env.get_template(f"{ref}.j2").render(**vars).strip()


class PromptAssembler:
    def __init__(self, registry: PromptRegistry | None = None) -> None:
        self.registry = registry or PromptRegistry()

    def assemble(self, recipe: HarnessRecipe, task: Task, tool_names: list[str]) -> list[dict[str, Any]]:
        system = self.registry.render(recipe.prompts.system, tools=tool_names, recipe=recipe)
        if recipe.prompts.operator:
            system += "\n\n" + self.registry.render(recipe.prompts.operator, recipe=recipe)
        user = self.registry.render(recipe.prompts.task, task=task)
        return [{"role": "system", "content": system}, {"role": "user", "content": user}]

    def stage_prompts(self, recipe: HarnessRecipe, task: Task, tool_names: list[str]) -> dict[str, str]:
        """V3: the instruction appended when each workflow stage starts. `tools` in the template is the list
        the model is offered in that stage (model-facing names), so the prompt never advertises a refused tool."""
        if recipe.workflow is None:
            return {}
        out = {}
        for st in recipe.workflow.stages:
            offered = [n for n in tool_names if st.allows(n.replace("__", ".", 1))]
            out[st.id] = self.registry.render(st.prompt, task=task, stage=st, tools=offered)
        return out

    @staticmethod
    def prompt_hash(messages: list[dict[str, Any]]) -> str:
        return content_hash(messages)
