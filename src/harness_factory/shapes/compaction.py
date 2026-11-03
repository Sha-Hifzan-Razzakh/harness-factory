"""Context compaction (MEM-05, ADR 0015): replace a conversation's older turns with a summary the model writes.

Called from shapes/common.model_turn before a model call whose estimated prompt passes the recipe's
`config.compaction.at` share of the context, so every shape compacts the same way. The conversation is split as

    head        the leading system message(s) and the task message: always kept verbatim
    region      older turns: replaced by one summary message
    tail        the last `keep_last_turns` assistant turns with their tool results (and the instructions that
                directly precede them): kept verbatim

The cut is always before an assistant or user message, never before a tool result, so a tool call and its result
are never separated; a region whose calls and results don't pair up is not compacted at all. The latest
instruction in the region (a V3 stage or V4 step prompt) is kept after the summary, so the model still knows which
part of the work it is in.

The summary is a model call through the gateway, on the run's model ("compactor" slot: the recipe's own when it
declares one, else the calling slot's settings). It is never free: it uses a step and its tokens count in the
budget, in C and in the quota. It is traced as a `compaction` span (estimated tokens before and after, tokens
dropped) holding the summary's model_call span, and the replaced messages are kept in the run's snapshot, so the
transcript still shows the whole conversation.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from harness_factory.observability.span import SpanKind
from harness_factory.recipes.model import CompactionSpec, ModelSlot
from harness_factory.substrate.context import CHARS_PER_TOKEN, ContextWindow, estimate_tokens
from harness_factory.substrate.errors import HarnessError
from harness_factory.substrate.prompts import PromptRegistry

SUMMARY_PREFIX = "Summary of the earlier conversation (older turns were compacted to save context):\n"
RECOVERY_PREFIX = "Your last reply was rejected"          # shapes.common.RECOVERY_NOTE
TRANSCRIPT_MESSAGE_CHARS = 2_000                           # per message in the summary request


@dataclass
class Plan:
    head: list[dict[str, Any]]
    region: list[dict[str, Any]]
    instruction: dict[str, Any] | None                     # latest instruction in the region, kept verbatim
    tail: list[dict[str, Any]]


@dataclass
class Compacted:
    history: list[dict[str, Any]] | None = None             # None: nothing was compacted
    stop: str | None = None
    error: str | None = None
    calls: int = 0


def is_summary(m: dict[str, Any]) -> bool:
    return m.get("role") == "user" and str(m.get("content") or "").startswith(SUMMARY_PREFIX)


def _head_len(history: list[dict[str, Any]]) -> int:
    for i, m in enumerate(history):
        if m.get("role") == "user":
            return i + 1
    return sum(1 for m in history if m.get("role") == "system")


def _paired(region: list[dict[str, Any]]) -> bool:
    calls = {c["id"] for m in region if m.get("role") == "assistant" for c in m.get("tool_calls") or []}
    results = {m.get("tool_call_id") for m in region if m.get("role") == "tool"}
    return calls == results


def plan(history: list[dict[str, Any]], keep_last_turns: int) -> Plan | None:
    """Where to cut, or None when there is nothing to compact (too few turns, or calls and results don't pair)."""
    head = _head_len(history)
    starts = [i for i in range(head, len(history)) if history[i].get("role") == "assistant"]
    if len(starts) <= keep_last_turns:
        return None
    cut = starts[-keep_last_turns]
    while cut > head and history[cut - 1].get("role") == "user" and not is_summary(history[cut - 1]):
        cut -= 1                                           # an instruction stays with the turn it led to
    region = history[head:cut]
    if not any(m.get("role") == "assistant" for m in region) or not _paired(region):
        return None
    tail = history[cut:]
    instruction = None
    if not any(_instruction(m) for m in tail):            # else the tail already says which part of the work
        instruction = next((m for m in reversed(region) if _instruction(m)), None)
    return Plan(head=history[:head], region=region, instruction=instruction, tail=tail)


def _instruction(m: dict[str, Any]) -> bool:
    return (m.get("role") == "user" and not is_summary(m)
            and not str(m.get("content") or "").startswith(RECOVERY_PREFIX))


def transcript(region: list[dict[str, Any]], max_chars: int) -> str:
    """The region as plain text for the summary request: tool calls with their arguments, results, replies."""
    names = {c["id"]: c["function"]["name"] for m in region if m.get("role") == "assistant"
             for c in m.get("tool_calls") or []}
    lines = []
    for m in region:
        role = m.get("role")
        text = str(m.get("content") or "")
        if len(text) > TRANSCRIPT_MESSAGE_CHARS:
            text = text[:TRANSCRIPT_MESSAGE_CHARS] + f" [... {len(text) - TRANSCRIPT_MESSAGE_CHARS} more characters]"
        if role == "assistant":
            if text:
                lines.append(f"[assistant] {text}")
            for c in m.get("tool_calls") or []:
                lines.append(f"[assistant calls {c['function']['name']}] {c['function']['arguments']}")
        elif role == "tool":
            lines.append(f"[result of {names.get(m.get('tool_call_id'), 'tool')}] {text}")
        else:
            lines.append(f"[{role}] {text}")
    out = "\n".join(lines)
    if len(out) > max_chars:                               # keep the most recent part; say what was left out
        out = f"[... {len(out) - max_chars} earlier characters omitted]\n" + out[-max_chars:]
    return out


def compactor_slot(recipe_slots: dict[str, ModelSlot], slot: ModelSlot) -> ModelSlot:
    """The slot of the summary call: the recipe's `compactor` slot when it has one, else the calling slot's
    settings; always on the run's model and seed (validation keeps a compactor model unpinned)."""
    base = recipe_slots.get("compactor") or slot
    return base.model_copy(update={"role": "compactor", "canonical_model": slot.canonical_model, "seed": slot.seed})


async def compact(inst, slot: ModelSlot, history: list[dict[str, Any]], spec: CompactionSpec,
                  window: ContextWindow, est_before: int, tools: list[dict[str, Any]]) -> Compacted:
    cut = plan(history, spec.keep_last_turns)
    if cut is None:
        return Compacted()
    limit = inst.budget.exceeded_by()
    if limit:
        return Compacted(stop=f"budget:{limit}")
    cslot = compactor_slot(inst.recipe.model_slots, slot)
    system = (inst.prompts or PromptRegistry()).render(spec.prompt)
    room = max(1, window.context_len - cslot.max_output_tokens - estimate_tokens([{"role": "system",
                                                                                  "content": system}]) - 64)
    request = [{"role": "system", "content": system},
               {"role": "user", "content": transcript(cut.region, int(room * CHARS_PER_TOKEN))}]
    est_request = estimate_tokens(request)
    with inst.tracer.span(SpanKind.compaction, "compaction", replaced_messages=len(cut.region),
                          kept_messages=len(cut.head) + len(cut.tail), est_tokens_before=est_before,
                          context_len=window.context_len, **({"scope": inst.scope} if inst.scope else {})) as span:
        try:
            comp = await inst.gateway.complete(cslot, request, [], attributes={"purpose": "compaction",
                                                                               "est_tokens_in": est_request,
                                                                               "context_len": window.context_len})
        except HarnessError as e:
            span.status, span.error = "error", e.kind.value
            return Compacted(stop=f"error:{e.kind.value}", error=e.message)
        inst.budget.used_steps += 1                        # never free: a step, and its tokens
        inst.budget.used_tokens += comp.tokens_in + comp.tokens_out
        summary = str(comp.message.get("content") or "").strip() or "(the summary was empty)"
        new = [*cut.head, {"role": "user", "content": SUMMARY_PREFIX + summary},
               *([cut.instruction] if cut.instruction else []), *cut.tail]
        est_after = estimate_tokens(new, tools)
        span.attributes.update(est_tokens_after=est_after, tokens_dropped=max(0, est_before - est_after),
                               summary_chars=len(summary), summary_preview=summary[:500])
    inst.snapshot.setdefault("compactions", []).append(
        {"scope": inst.scope, "est_tokens_before": est_before, "est_tokens_after": est_after, "summary": summary,
         "replaced": json.loads(json.dumps(cut.region, default=str))})
    return Compacted(history=new, calls=1)
