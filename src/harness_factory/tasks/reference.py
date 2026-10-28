"""Reference solutions (B1). Each task folder has reference.py with:

    PLAN  = [{"tool": "fs.write_file", "args": {...}}, ...]   # tool calls that solve the task
    FINAL = "..."                                             # the final answer text

A task becomes 'verified' only when its reference solution passes its own oracle (TSK-04).
Scripted backends derive their turns from the same plan, so offline runs exercise the real path.
"""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass
from typing import Any

from harness_factory.substrate.tools import model_name
from harness_factory.tasks.model import Task


@dataclass
class Reference:
    plan: list[dict[str, Any]]
    final: str


def load_reference(task: Task) -> Reference:
    if task.source_dir is None:
        raise ValueError("task has no source_dir")
    path = task.source_dir / "reference.py"
    if not path.is_file():
        raise FileNotFoundError(f"{task.key()} has no reference.py")
    spec = importlib.util.spec_from_file_location(f"hf_ref_{task.id.replace('-', '_')}", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return Reference(plan=list(mod.PLAN), final=str(mod.FINAL))


def scripted_turns(ref: Reference, style: str = "replay") -> list[dict[str, Any]]:
    """Model turns for the scripted backend.

    replay  one tool call per turn, then the final answer       (a competent model)
    noisy   first a call with missing arguments, then replay    (recovers from its own error)
    lazy    drops the last plan step, then answers              (stops early)
    """
    plan = list(ref.plan)
    turns: list[dict[str, Any]] = []
    if style == "noisy" and plan:
        turns.append({"tool_calls": [{"name": model_name(plan[0]["tool"]), "arguments": {}}]})
    if style == "lazy" and len(plan) > 0:
        plan = plan[:-1]
    turns += [{"tool_calls": [{"name": model_name(s["tool"]), "arguments": s["args"]}]} for s in plan]
    turns.append({"content": ref.final})
    return turns
