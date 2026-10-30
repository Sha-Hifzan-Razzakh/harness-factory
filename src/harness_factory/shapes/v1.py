"""SingleCallShape (V1, B4): exactly one model response. Tool calls in it are executed; nothing comes after.

The cheapest shape. It can act (one response may hold several tool calls) but it never sees a tool result,
so tasks that must read before answering are expected to fail here. That failure is a measurement, not a bug.
Plain code rather than a graph: there is no control flow to express.
"""

from __future__ import annotations

from harness_factory.recipes.model import Variant
from harness_factory.shapes.base import HarnessInstance, ShapeRegistry
from harness_factory.shapes.common import DONE, finish, model_turn, run_tool_calls
from harness_factory.shapes.result import RunResult


@ShapeRegistry.register(Variant.V1_SINGLE_CALL)
class SingleCallShape:
    variant: Variant

    async def run(self, inst: HarnessInstance) -> RunResult:
        slot = inst.recipe.model_slots["executor"]
        msg, stop, err = await model_turn(inst, slot, inst.messages, inst.tools.tools_for_model())
        if stop:
            return finish(inst, inst.messages, stop, None, err)
        messages = inst.messages + [msg]
        results, stop = await run_tool_calls(inst, msg)
        return finish(inst, messages + results, stop or DONE, msg.get("content") or "")
