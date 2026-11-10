"""The scripted planner sketches each call's arguments; it doesn't write them out.

A reference whose last step writes a long file (a report of tens of thousands of characters) used to put the whole
content into the plan twice: in the plan the executor is shown, and again as that step's own prompt. On the
scripted model's 32K context that overflowed before the write, so V4 runs ended FAIL for a reason no real planner
would have. The plan line now cuts each string argument; the step itself still calls the tool with the full
arguments, so the world each call sees is the reference's.
"""

import json
from types import SimpleNamespace

from harness_factory.tasks.reference import PLAN_ARG_CHARS, PlanBackend, Reference

LONG = "x" * 30_000
REF = Reference(plan=[{"tool": "fs.read_file", "args": {"path": "notes/README.txt"}},
                      {"tool": "fs.write_file", "args": {"path": "out/report.json", "content": LONG}}],
                final="done")
TOOLS = [{"type": "function", "function": {"name": n, "parameters": {}}} for n in ("fs__read_file", "fs__write_file")]


async def ask(backend, slot_role, messages, tools=()):
    c = await backend.complete(messages, list(tools), SimpleNamespace(role=slot_role), "scripted", "replay")
    return c.message


async def test_a_plan_line_cuts_long_arguments_but_keeps_short_ones_whole():
    msg = await ask(PlanBackend(REF), "planner", [{"role": "user", "content": "Plan the task."}])
    lines = msg["content"].splitlines()
    assert lines[0] == '1. Call fs.read_file with {"path": "notes/README.txt"}'
    assert lines[1].startswith('2. Call fs.write_file with {"path": "out/report.json", "content": "xxx')
    assert len(lines[1]) < PLAN_ARG_CHARS + 100 and lines[1].endswith('...\"}')


async def test_the_step_still_calls_the_tool_with_the_full_arguments():
    b = PlanBackend(REF)
    await ask(b, "planner", [{"role": "user", "content": "Plan the task."}])
    first = await ask(b, "executor", [{"role": "user", "content": "Step 1 of 2: read"}], TOOLS)
    assert first["tool_calls"][0]["function"]["name"] == "fs__read_file"
    step2 = [{"role": "user", "content": "Step 1 of 2: read"}, first,
             {"role": "tool", "tool_call_id": "call_2", "content": "readme"},
             {"role": "user", "content": "Step 2 of 2: write"}]
    write = await ask(b, "executor", step2, TOOLS)
    args = json.loads(write["tool_calls"][0]["function"]["arguments"])
    assert args == {"path": "out/report.json", "content": LONG}


async def test_a_structured_plan_step_cuts_long_arguments_too():
    b = PlanBackend(REF)
    msg = await ask(b, "planner", [{"role": "system", "content": 'Reply with JSON steps with "depends_on".'},
                                   {"role": "user", "content": "Plan the task. Tools: fs__read_file, fs__write_file"}])
    steps = json.loads(msg["content"])["steps"]
    assert [s["id"] for s in steps] == ["s1", "s2"]
    assert len(steps[1]["instruction"]) < PLAN_ARG_CHARS + 100
