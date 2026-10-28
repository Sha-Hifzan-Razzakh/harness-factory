"""Reference solutions (B1). Each task folder has reference.py with:

    PLAN  = [{"tool": "fs.write_file", "args": {...}}, ...]   # tool calls that solve the task
    FINAL = "..."                                             # the final answer text

A task becomes 'verified' only when its reference solution passes its own oracle (TSK-04).
Scripted backends derive their turns from the same plan, so offline runs exercise the real path.
"""

from __future__ import annotations

import importlib.util
import json
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


class PlanBackend:
    """Scripted model that follows the reference plan but only calls tools it is offered right now.

    Each call: if the next plan step's tool is in the offered tool list, call it; otherwise end the turn.
    That lets the same script drive every shape: V2 offers all tools, a V3 stage offers its subset, and V1
    gets one response. Styles:

    replay  follows the plan                              (a competent model)
    noisy   one call with missing arguments first          (recovers from its own error)
    lazy    drops the last plan step                       (stops early)
    """

    STAGE_DONE = "Stage complete."

    def __init__(self, ref: Reference, style: str = "replay") -> None:
        if style not in ("replay", "noisy", "lazy"):
            raise ValueError(f"unknown scripted style {style!r}")
        self.plan = list(ref.plan[:-1] if style == "lazy" and ref.plan else ref.plan)
        self.final = ref.final
        self.noisy_pending = style == "noisy"
        self.i = 0
        self.calls = 0

    def _msg(self, name: str | None = None, args: dict | None = None, content: str | None = None) -> dict:
        self.calls += 1
        if name is None:
            return {"role": "assistant", "content": content}
        call = {"id": f"call_{self.calls}", "type": "function",
                "function": {"name": name, "arguments": json.dumps(args or {})}}
        return {"role": "assistant", "content": None, "tool_calls": [call]}

    async def complete(self, messages, tools, slot, provider, served):
        from harness_factory.substrate.gateway import Completion, approx_tokens

        offered = {t["function"]["name"] for t in tools}
        nxt = model_name(self.plan[self.i]["tool"]) if self.i < len(self.plan) else None
        if nxt and nxt in offered and self.noisy_pending:
            self.noisy_pending = False
            msg = self._msg(nxt, {})
        elif nxt and nxt in offered:
            msg = self._msg(nxt, self.plan[self.i]["args"])
            self.i += 1
        elif nxt is None:
            msg = self._msg(content=self.final)
        else:
            msg = self._msg(content=self.STAGE_DONE)
        return Completion(message=msg, tokens_in=approx_tokens(messages), tokens_out=approx_tokens(msg))
