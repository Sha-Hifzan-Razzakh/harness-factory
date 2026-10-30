"""Reference solutions (B1). Each task folder has reference.py with:

    PLAN  = [{"tool": "fs.write_file", "args": {...}}, ...]   # tool calls that solve the task
    FINAL = "..."                                             # the final answer text

A task becomes 'verified' only when its reference solution passes its own oracle (TSK-04).
Scripted backends derive their turns from the same plan, so offline runs exercise the real path.
"""

from __future__ import annotations

import importlib.util
import json
import re
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
    retry   follows the plan, and repeats a step whose tool reported an error or returned nothing,
            up to 3 times                                  (robust to flaky tools; the others are not)
    invent  its first call names a tool that doesn't exist, which the provider rejects (like Groq's
            tool_use_failed in Phase 1's code-01); then follows the plan if it is asked again
    delegate when offered the delegate tool, hands the whole task to the helper first; the helper (one shared
            script, so the same plan) does the work, then the parent gives the final answer. Without a delegate
            tool it is replay

    Every style plans when called in the planner slot (V4, V5): one numbered line per remaining plan step. In a V4
    step ("Step i of n: ...") or a V5 worker's part ("Your part: ..."), it makes the next plan call and then closes
    the step, so one plan step runs per V4 step or V5 worker.
    """

    INVENTED_TOOL = "fs__search_file"
    DELEGATE = "delegate__run"

    MAX_RETRIES = 3

    STAGE_DONE = "Stage complete."
    STEP_DONE = "Step complete."
    _STEP = re.compile(r"(?:^|\n)(?:Step \d+ of \d+:|Your part:)")

    def __init__(self, ref: Reference, style: str = "replay") -> None:
        if style not in ("replay", "noisy", "lazy", "retry", "invent", "delegate"):
            raise ValueError(f"unknown scripted style {style!r}")
        self.style = style
        self.last_step: dict | None = None
        self.retries = 0
        self.plan = list(ref.plan[:-1] if style == "lazy" and ref.plan else ref.plan)
        self.final = ref.final
        self.noisy_pending = style == "noisy"
        self.invent_pending = style == "invent"
        self.delegate_pending = style == "delegate"
        self.i = 0
        self.calls = 0
        self.step_marker: str | None = None   # the V4 step / V5 part prompt being worked on
        self.step_start = 0                   # plan index when it started

    @staticmethod
    def _last_tool_failed(messages: list[dict]) -> bool:
        """What a careful model would notice: the last tool result is an error, or empty."""
        if not messages or messages[-1].get("role") != "tool":
            return False
        text = (messages[-1].get("content") or "").strip()
        return not text or text.lower().startswith(("error", "permission denied")) or "timed out" in text.lower()

    def _msg(self, name: str | None = None, args: dict | None = None, content: str | None = None) -> dict:
        self.calls += 1
        if name is None:
            return {"role": "assistant", "content": content}
        call = {"id": f"call_{self.calls}", "type": "function",
                "function": {"name": name, "arguments": json.dumps(args or {})}}
        return {"role": "assistant", "content": None, "tool_calls": [call]}

    async def complete(self, messages, tools, slot, provider, served):
        from harness_factory.substrate.errors import ErrorKind, HarnessError
        from harness_factory.substrate.gateway import Completion, approx_tokens

        offered = {t["function"]["name"] for t in tools}
        if getattr(slot, "role", None) == "planner":
            lines = [f"{n}. Call {s['tool']} with {json.dumps(s['args'])}"
                     for n, s in enumerate(self.plan[self.i:], 1)] or ["1. Give the final answer."]
            msg = self._msg(content="\n".join(lines))
            return Completion(message=msg, tokens_in=approx_tokens(messages), tokens_out=approx_tokens(msg))
        if self.delegate_pending and self.DELEGATE in offered:
            self.delegate_pending = False
            ask = next((m.get("content") or "" for m in reversed(messages) if m.get("role") == "user"), "")
            msg = self._msg(self.DELEGATE, {"task": ask.removeprefix("Task: ")})
            return Completion(message=msg, tokens_in=approx_tokens(messages), tokens_out=approx_tokens(msg))
        if self.invent_pending and offered:
            self.invent_pending = False
            self.calls += 1
            raise HarnessError(ErrorKind.MODEL_PARSE, f"tool call validation failed: attempted to call tool "
                                                      f"'{self.INVENTED_TOOL}' which was not in request.tools")
        if self.style == "retry" and self._last_tool_failed(messages) and self.last_step \
                and self.retries < self.MAX_RETRIES and model_name(self.last_step["tool"]) in offered:
            self.retries += 1
            msg = self._msg(model_name(self.last_step["tool"]), self.last_step["args"])
            return Completion(message=msg, tokens_in=approx_tokens(messages), tokens_out=approx_tokens(msg))
        self.retries = 0
        marker = next((m.get("content") for m in reversed(messages)
                       if m.get("role") == "user" and self._STEP.search(m.get("content") or "")), None)
        if marker != self.step_marker:
            self.step_marker, self.step_start = marker, self.i
        if marker and self.i > self.step_start and messages[-1].get("role") == "tool":
            msg = self._msg(content=self.final if self.i >= len(self.plan) else self.STEP_DONE)
            return Completion(message=msg, tokens_in=approx_tokens(messages), tokens_out=approx_tokens(msg))
        nxt = model_name(self.plan[self.i]["tool"]) if self.i < len(self.plan) else None
        if nxt and nxt in offered and self.noisy_pending:
            self.noisy_pending = False
            msg = self._msg(nxt, {})
        elif nxt and nxt in offered:
            msg = self._msg(nxt, self.plan[self.i]["args"])
            self.last_step = self.plan[self.i]
            self.i += 1
        elif nxt is None:
            msg = self._msg(content=self.final)
        else:
            msg = self._msg(content=self.STAGE_DONE)
        return Completion(message=msg, tokens_in=approx_tokens(messages), tokens_out=approx_tokens(msg))
