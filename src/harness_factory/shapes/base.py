"""Harness protocol and ShapeRegistry (B4). A new shape is registered, never wired into core code."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar, Protocol

from harness_factory.observability.tracer import Tracer
from harness_factory.recipes.model import HarnessRecipe, Variant
from harness_factory.runner.spec import RunKey
from harness_factory.shapes.result import RunResult
from harness_factory.substrate.budget import BudgetGuard
from harness_factory.substrate.gateway import ModelGateway
from harness_factory.substrate.tools import ToolExecutor
from harness_factory.tasks.model import Task


@dataclass
class HarnessInstance:
    """One provisioned harness, used for exactly one run."""
    run_key: RunKey
    recipe: HarnessRecipe
    task: Task
    gateway: ModelGateway
    tools: ToolExecutor
    budget: BudgetGuard
    tracer: Tracer
    messages: list[dict[str, Any]]
    snapshot: dict[str, Any] = field(default_factory=dict)
    stage_prompts: dict[str, str] = field(default_factory=dict)   # V3: rendered instruction per stage id
    recoveries: int = 0                                           # malformed replies re-asked so far this run
    scope: str | None = None                                      # tool-call scope for this level (a delegate's)
    delegate: Any = None                                          # shapes.delegate.DelegateContext, or None


class Harness(Protocol):
    variant: Variant

    async def run(self, instance: HarnessInstance) -> RunResult: ...


class ShapeRegistry:
    _plugins: ClassVar[dict[Variant, type]] = {}

    @classmethod
    def register(cls, variant: Variant):
        def deco(plugin: type) -> type:
            plugin.variant = variant
            cls._plugins[variant] = plugin
            return plugin
        return deco

    @classmethod
    def get(cls, variant: Variant) -> Harness:
        if variant not in cls._plugins:
            raise KeyError(f"no shape registered for {variant.value}; registered: {[v.value for v in cls._plugins]}")
        return cls._plugins[variant]()

    @classmethod
    def variants(cls) -> list[Variant]:
        return list(cls._plugins)
