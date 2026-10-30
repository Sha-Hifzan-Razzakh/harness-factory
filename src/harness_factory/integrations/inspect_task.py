"""Inspect AI adapter (spike, see docs/adr/0001-inspect-ai.md): run factory tasks as an Inspect eval.

Inspect is the outer shell: dataset, epochs, the eval log and its viewer. The factory stays the engine:
recipes, provisioning, sandboxes, MCP, graders, RunKey and the results store are unchanged. Every Inspect
sample is one factory run_one(); its EffectivenessRecord becomes the sample's score and transcript.

    inspect eval src/harness_factory/integrations/inspect_task.py --model mockllm/model \\
        -T recipe=v3-staged -T model=scripted/replay --epochs 3
    inspect view                                   # browse transcripts, scores, metrics

Inspect's own --model is not used: the factory's ModelGateway calls the model named by -T model / provider.
mockllm/model satisfies Inspect's requirement for a model without calling anything.
Epochs map to seeds (epoch 1 -> seed 0), so --epochs N gives the N-seed reliability runs.
"""

from __future__ import annotations

import json
from typing import Any

from inspect_ai import Task, task
from inspect_ai.dataset import Sample
from inspect_ai.model import (
    ChatMessage,
    ChatMessageAssistant,
    ChatMessageSystem,
    ChatMessageTool,
    ChatMessageUser,
    ModelOutput,
)
from inspect_ai.scorer import Score, Target, bootstrap_stderr, mean, scorer
from inspect_ai.solver import Generate, TaskState, solver
from inspect_ai.tool import ToolCall

from harness_factory.provision.provisioner import task_class
from harness_factory.runner.runner import Factory, run_one
from harness_factory.substrate.tools import tool_ref

RECORD_KEY = "harness_factory"


def to_inspect_messages(messages: list[dict[str, Any]]) -> list[ChatMessage]:
    """Factory transcript (OpenAI-style dicts) -> Inspect chat messages, so the viewer shows every tool call."""
    names: dict[str, str] = {}
    out: list[ChatMessage] = []
    for m in messages:
        role = m.get("role")
        if role == "system":
            out.append(ChatMessageSystem(content=m.get("content") or ""))
        elif role == "user":
            out.append(ChatMessageUser(content=m.get("content") or ""))
        elif role == "assistant":
            calls = []
            for c in m.get("tool_calls") or []:
                fn = c["function"]
                try:
                    args, err = json.loads(fn.get("arguments") or "{}"), None
                except json.JSONDecodeError as e:
                    args, err = {}, str(e)
                names[c["id"]] = tool_ref(fn["name"])
                calls.append(ToolCall(id=c["id"], function=tool_ref(fn["name"]), arguments=args, parse_error=err))
            out.append(ChatMessageAssistant(content=m.get("content") or "", tool_calls=calls or None))
        elif role == "tool":
            out.append(ChatMessageTool(content=m.get("content") or "", tool_call_id=m.get("tool_call_id"),
                                       function=names.get(m.get("tool_call_id") or "")))
    return out


@solver
def run_harness(recipe: str, model: str, provider: str, factory: Factory):
    async def solve(state: TaskState, generate: Generate) -> TaskState:
        t = factory.tasks.get(state.metadata["task_id"])
        rec = await run_one(factory, t, factory.recipes[recipe], model, provider, seed=state.epoch - 1)
        state.messages = to_inspect_messages(rec.messages)
        state.output = ModelOutput.from_content(model=f"{provider}/{model}", content=rec.output or "")
        state.metadata[RECORD_KEY] = rec.model_dump(mode="json", exclude={"messages"})
        state.completed = True
        return state

    return solve


@scorer(metrics={"composite": [mean(), bootstrap_stderr()], "outcome": [mean()], "trajectory": [mean()],
                 "success": [mean()]})
def effectiveness():
    """The factory's own grades, reported to Inspect. Nothing is re-graded here."""
    async def score(state: TaskState, target: Target) -> Score:
        rec = state.metadata[RECORD_KEY]
        s = rec["scores"]
        failed = [c["id"] for g in rec["grades"] for c in g.get("checks", []) if not c["passed"]]
        return Score(
            value={"composite": rec["composite"], "outcome": s["outcome"], "trajectory": s["trajectory"],
                   "success": 1.0 if rec["status"] == "SUCCESS" else 0.0},
            answer=rec["status"],
            explanation=(f"failed checks: {', '.join(failed)}" if failed else "all checks passed")
                        + (f"; violations: {', '.join(v['kind'] for v in rec['violations'])}" if rec["violations"] else ""),
            metadata={"run_key": rec["run_key"], "task_class": rec["task_class"], "trace_path": rec["trace_path"],
                      "cost": rec["cost"]},
        )

    return score


@task
def harness_factory(recipe: str = "v2-base", model: str = "scripted/replay", provider: str = "scripted",
                    tasks: str | None = None) -> Task:
    """One factory cell sweep as an Inspect task. `tasks` is an optional comma-separated list of task ids."""
    f = Factory.load()
    chosen = [f.tasks.get(t) for t in tasks.split(",")] if tasks else f.tasks.all()
    samples = [Sample(id=t.id, input=t.instruction,
                      metadata={"task_id": t.id, "task_version": t.key(), "task_class": task_class(t, f.declared)})
               for t in chosen]
    return Task(dataset=samples, solver=run_harness(recipe, model, provider, f), scorer=effectiveness(),
                name=f"hf_{recipe.replace('-', '_')}", metadata={"recipe": recipe, "recipe_hash":
                f.recipes[recipe].content_hash(), "model": model, "provider": provider})
