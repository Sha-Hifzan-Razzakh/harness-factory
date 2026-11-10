"""Reference solutions (B1). Each task folder has reference.py with:

    PLAN  = [{"tool": "fs.write_file", "args": {...}}, ...]   # tool calls that solve the task
    FINAL = "..."                                             # the final answer text

A step marked `"optional": True` (e.g. a `retrieve.search` call, ADR 0017) is one a harness may not offer: the
scripted model skips it when its tool isn't offered, and the plan must still solve the task without it.

A task becomes 'verified' only when its reference solution passes its own oracle (TSK-04).
Scripted backends derive their turns from the same plan, so offline runs exercise the real path.
"""

from __future__ import annotations

import importlib.util
import json
import re
from dataclasses import dataclass
from typing import Any

from harness_factory.substrate.emulation import latest_tool_result, latest_user_text, unframed
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


PLAN_ARG_CHARS = 120     # a planner names a call's arguments; it doesn't write them out (a file's content)


def _brief(args: dict) -> str:
    """A plan line's arguments: each string value cut at PLAN_ARG_CHARS, as a planner would sketch the call."""
    short = {k: (v[:PLAN_ARG_CHARS] + "..." if isinstance(v, str) and len(v) > PLAN_ARG_CHARS else v)
             for k, v in args.items()}
    return json.dumps(short)


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

        An optional step whose tool isn't offered right now is skipped (the retrieval tool exists only in recipes that
    retrieve), so one plan drives recipes with and without it.

    Every style answers a compaction request (the compactor slot) with a fixed summary of it, without moving the
    plan. Every style plans when called in the planner slot (V4, V5): one numbered line per remaining plan step,
    each long string argument cut at PLAN_ARG_CHARS (the step itself calls the tool with the full arguments). In a V4
    step ("Step i of n: ...") or a V5 worker's part ("Your part: ..."), it makes the next plan call and then closes
    the step, so one plan step runs per V4 step or V5 worker.

    Structured plans (the planner prompt asks for JSON with "depends_on"): it replies with one JSON step per
    reference call, id `s<k>`. A call that only reads depends on the last earlier call that changes something; a
    call that changes something depends on that one and on every read since, so reads between two writes may run
    concurrently and the world each call sees is the reference's. Steps are then driven by their id ("[s3] ..." in
    the step or worker prompt), not by a position in the plan, so concurrent V5 workers sharing this backend each do
    their own step: the step's call, again after an error under `retry` (or first with missing arguments under
    `noisy`), then a one-line close, the final answer for the plan's last step.
    Offered a framework's handoff tool (`transfer_to_<agent>`, e.g. a supervisor's, ADR 0016) and none of the next
    step's tool, it hands off while plan steps remain; the agent it hands to (the same script) does them.
    """

    INVENTED_TOOL = "fs__search_file"
    DELEGATE = "delegate__run"
    HANDOFF_PREFIX = "transfer_to_"

    MAX_RETRIES = 3

    STAGE_DONE = "Stage complete."
    STEP_DONE = "Step complete."
    _STEP = re.compile(r"(?:^|\n)(?:Step \d+ of \d+:|Your part:)")
    _STRUCTURED_STEP = re.compile(r"(?:^|\n)(?:Step \d+ of \d+:|Your part:) \[(s\d+)\] ")

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
        self.started: set[str] = set()        # structured: step ids whose call was made
        self._reads: dict[str, bool] = {}     # structured: tool ref -> its declared side effect is READ

    @staticmethod
    def _last_tool_failed(messages: list[dict]) -> bool:
        """What a careful model would notice: the last tool result is an error, or empty. Tool results are native
        (role tool) or, for an emulated model, the "[Tool result ...]" messages the emulation writes."""
        text = latest_tool_result(messages)
        if text is None:
            return False
        if isinstance(text, list):          # content parts (e.g. the OpenAI Agents SDK's tool output)
            text = "".join(p.get("text") or "" for p in text if isinstance(p, dict))
        text = text.strip()
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
        if getattr(slot, "role", None) == "compactor":       # a summary request (ADR 0015): the plan doesn't move
            from harness_factory.substrate.context import scripted_summary
            msg = {"role": "assistant", "content": scripted_summary(messages)}
            return Completion(message=msg, tokens_in=approx_tokens(messages), tokens_out=approx_tokens(msg))
        if getattr(slot, "role", None) == "planner" and self._wants_json(messages):
            shown = "\n".join(m.get("content") or "" for m in messages)     # the planner prompt lists its tools
            msg = self._msg(content=json.dumps({"steps": self._structured_plan(shown)}))
            return Completion(message=msg, tokens_in=approx_tokens(messages), tokens_out=approx_tokens(msg))
        if getattr(slot, "role", None) == "planner":
            lines = [f"{n}. Call {s['tool']} with {_brief(s['args'])}"
                     for n, s in enumerate(self.plan[self.i:], 1)] or ["1. Give the final answer."]
            msg = self._msg(content="\n".join(lines))
            return Completion(message=msg, tokens_in=approx_tokens(messages), tokens_out=approx_tokens(msg))
        if self.delegate_pending and self.DELEGATE in offered:
            self.delegate_pending = False
            ask = latest_user_text(messages)
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
        sid = self._structured_marker(messages)
        if sid is not None:
            msg = self._structured_turn(sid, messages, offered)
            return Completion(message=msg, tokens_in=approx_tokens(messages), tokens_out=approx_tokens(msg))
        self.retries = 0
        while self.i < len(self.plan) and self.plan[self.i].get("optional") \
                and model_name(self.plan[self.i]["tool"]) not in offered:
            self.i += 1
        marker = next((unframed(m.get("content") or "") for m in reversed(messages)
                       if m.get("role") == "user" and self._STEP.search(m.get("content") or "")), None)
        if marker != self.step_marker:
            self.step_marker, self.step_start = marker, self.i
        if marker and self.i > self.step_start and latest_tool_result(messages) is not None:
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
        elif handoff := sorted(n for n in offered if n.startswith(self.HANDOFF_PREFIX)):
            msg = self._msg(handoff[0], {})
        else:
            msg = self._msg(content=self.STAGE_DONE)
        return Completion(message=msg, tokens_in=approx_tokens(messages), tokens_out=approx_tokens(msg))

    # ------------------------------------------------------------------ structured plans (planning.structured)

    @staticmethod
    def _wants_json(messages: list[dict]) -> bool:
        return any(m.get("role") == "system" and '"depends_on"' in (m.get("content") or "") for m in messages)

    def _is_read(self, ref: str) -> bool:
        if not self._reads:
            from harness_factory import settings
            from harness_factory.env.mcp.servers import SideEffect, load_tool_catalog
            declared = load_tool_catalog(settings.TOOLS_FILE)
            self._reads = {t.ref(srv.id): t.side_effect == SideEffect.READ for srv in declared.servers for t in srv.tools}
        return self._reads.get(ref, False)

    def _structured_plan(self, shown: str = "") -> list[dict]:
        """One step per reference call not yet made (a replan leaves out the ones done). An optional step whose tool
        the planner wasn't shown is left out, as the unstructured path skips it (ADR 0017); its id stays unused, so
        s<k> still names plan[k-1]."""
        steps, last_write, reads = [], None, []
        for k, call in enumerate(self.plan, 1):
            sid = f"s{k}"
            if call.get("optional") and model_name(call["tool"]) not in shown:
                continue
            if self._is_read(call["tool"]):
                deps = [last_write] if last_write else []
                reads.append(sid)
            else:
                deps = ([last_write] if last_write else []) + reads
                last_write, reads = sid, []
            if sid in self.started:
                continue
            steps.append({"id": sid, "instruction": f"Call {call['tool']} with {_brief(call['args'])}",
                          "tools": [model_name(call["tool"])], "depends_on": deps,
                          "done_when": f"{call['tool']} has answered"})
        return steps or [{"id": f"s{len(self.plan) + 1}", "instruction": "Give the final answer.",
                          "done_when": "the answer is given"}]

    def _structured_marker(self, messages: list[dict]) -> str | None:
        """The id of the structured step this conversation is on (its latest step or part prompt), or None."""
        for m in reversed(messages):
            if m.get("role") == "user":
                found = self._STRUCTURED_STEP.findall(m.get("content") or "")
                if found:
                    return found[-1]
                if self._STEP.search(m.get("content") or ""):
                    return None
        return None

    def _structured_turn(self, sid: str, messages: list[dict], offered: set[str]) -> dict:
        k = int(sid[1:]) - 1
        call = self.plan[k] if 0 <= k < len(self.plan) else None
        last = call is None or k == len(self.plan) - 1
        close = self.final if last else self.STEP_DONE
        if call is None or model_name(call["tool"]) not in offered:
            return self._msg(content=close)
        start = max(i for i, m in enumerate(messages) if m.get("role") == "user"
                    and f"[{sid}] " in (m.get("content") or ""))
        name, args = model_name(call["tool"]), call["args"]
        made = [m for m in messages[start:] if m.get("role") == "assistant"
                for c in (m.get("tool_calls") or []) if c["function"]["name"] == name
                and json.loads(c["function"]["arguments"] or "{}") == args]
        if not made:
            if self.noisy_pending:
                self.noisy_pending = False
                return self._msg(name, {})
            self.started.add(sid)
            return self._msg(name, args)
        if self.style == "retry" and self._last_tool_failed(messages) and len(made) <= self.MAX_RETRIES:
            return self._msg(name, args)
        return self._msg(content=close)
