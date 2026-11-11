"""A reply cut off at the output limit mid tool call (#108, lab D2): never replayed, never an unscored ERROR; and
`finish_reason` on every model_call span.

sql-03@3 / v2-budget (Nemotron) returned exactly max_output_tokens with its sql.query arguments stopping
mid-string. The broken arguments were replayed, OpenRouter refused the next request (CONFIG) and the run was ERROR,
left out of the cell's mean.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import litellm
import pytest

from harness_factory.runner.runner import run_one
from harness_factory.shapes.common import RECOVERY_NOTE
from harness_factory.substrate.gateway import Completion, ScriptedBackend, cut_off_tool_calls

READ = {"tool_calls": [{"name": "fs__read_file", "arguments": {"path": "config/app.ini"}}]}
CUT = {"tool_calls": [{"name": "fs__write_file", "raw_arguments": '{"path": "config/app.ini", "content": "[ser'}],
       "finish_reason": "length"}


class Recording(ScriptedBackend):
    """ScriptedBackend that keeps every request's messages, to prove nothing unparseable is ever sent again."""

    def __init__(self, turns):
        super().__init__(turns)
        self.requests: list[list[dict]] = []

    async def complete(self, messages, tools, slot, provider, served, **kw):
        self.requests.append(json.loads(json.dumps(messages)))
        return await super().complete(messages, tools, slot, provider, served)


def _spans(rec) -> list[dict]:
    return [json.loads(line) for line in Path(rec.trace_path).read_text().splitlines()]


def _model_spans(rec) -> list[dict]:
    return [s for s in _spans(rec) if s["kind"] == "model_call"]


def _assert_no_broken_arguments(requests: list[list[dict]]) -> None:
    for request in requests:
        for m in request:
            for call in m.get("tool_calls") or []:
                assert isinstance(json.loads(call["function"]["arguments"]), dict)


def test_cut_off_detection():
    def comp(args, reason):
        call = {"id": "c", "type": "function", "function": {"name": "sql__query", "arguments": args}}
        return Completion(message={"role": "assistant", "tool_calls": [call]}, finish_reason=reason)

    assert cut_off_tool_calls(comp('{"sql": "SELECT', "length")) == ["sql__query"]
    assert cut_off_tool_calls(comp('{"sql": "SELECT', "max_tokens")) == ["sql__query"]
    assert cut_off_tool_calls(comp('{"sql": "SELECT 1"}', "length")) == []          # whole JSON: nothing cut
    assert cut_off_tool_calls(comp('[1, 2]', "length")) == ["sql__query"]            # arguments are an object
    assert cut_off_tool_calls(comp('{"sql": "SELECT', "tool_calls")) == []          # not at the limit: the tool
    assert cut_off_tool_calls(Completion(message={"role": "assistant", "content": "long"},   # answers as before
                                         finish_reason="length")) == []


async def test_a_cut_off_tool_call_ends_the_run_as_a_harness_stop(factory):
    backend = Recording([READ, CUT, {"content": "never reached"}])
    rec = await run_one(factory, factory.tasks.get("fs-02"), factory.recipes["v2-base"], "scripted/replay",
                        "scripted", backend=backend)
    assert rec.status.value in ("PARTIAL", "FAIL") and rec.status.value not in ("ERROR", "INCOMPLETE")
    assert any(v.kind == "malformed_output" and "cut off at the output limit" in v.detail for v in rec.violations)
    assert len(backend.requests) == 2                                   # the run stopped: no third request
    _assert_no_broken_arguments(backend.requests)
    spans = _model_spans(rec)
    assert [s["attributes"].get("finish_reason") for s in spans] == ["tool_calls", "length"]
    cut = spans[1]
    assert cut["status"] == "ok" and cut["error"] == "MODEL_PARSE"     # a real call: counted, marked rejected
    assert cut["attributes"]["rejected"].startswith("cut_off:") and cut["tokens_out"] > 0
    assert rec.scores.trajectory < 1.0                                  # the rejected reply counts against Q
    t = json.loads(Path(rec.transcript_path).read_text())
    _assert_no_broken_arguments([t["messages"]])                        # the transcript never holds it either


async def test_with_recovery_the_model_is_asked_again_without_the_broken_call(factory):
    backend = Recording([READ, CUT, {"tool_calls": [{"name": "fs__write_file", "arguments": {
        "path": "config/app.ini", "content": "[server]\nport = 8443\n"}}]}, {"content": "Set the port to 8443."}])
    recipe = factory.recipes["v2-recover"]                              # recover_malformed: 2
    rec = await run_one(factory, factory.tasks.get("fs-02"), recipe, "scripted/replay", "scripted", backend=backend)
    assert rec.status.value not in ("ERROR", "INCOMPLETE")
    assert not any(v.kind == "malformed_output" for v in rec.violations)
    _assert_no_broken_arguments(backend.requests)
    note = backend.requests[2][-1]
    assert note["role"] == "user" and note["content"].startswith(RECOVERY_NOTE[:20])
    assert "cut off at the output limit" in note["content"]
    assert rec.cost.tokens_out == sum(s["tokens_out"] for s in _model_spans(rec))   # the cut reply's tokens count


def _resp(content=None, tool_calls=None, finish_reason="stop"):
    msg = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason=finish_reason)],
                           usage=SimpleNamespace(prompt_tokens=100, completion_tokens=2048))


def _call(id_, name, args):
    return SimpleNamespace(id=id_, function=SimpleNamespace(name=name, arguments=args))


async def test_litellm_finish_reason_length_mid_call(factory, monkeypatch):
    calls = []
    script = [_resp(tool_calls=[_call("c1", "fs__read_file", '{"path": "config/app.ini"}')], finish_reason="tool_calls"),
              _resp(tool_calls=[_call("c2", "fs__write_file", '{"path": "config/app.ini", "content": "[ser')],
                    finish_reason="length")]

    async def fake(**kwargs):
        calls.append(kwargs)
        return script[len(calls) - 1]

    monkeypatch.setattr(litellm, "acompletion", fake)
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    rec = await run_one(factory, factory.tasks.get("fs-02"), factory.recipes["v2-base"], "gpt-oss-120b", "groq")
    assert len(calls) == 2 and rec.status.value not in ("ERROR", "INCOMPLETE")
    assert any(v.kind == "malformed_output" for v in rec.violations)
    assert [s["attributes"].get("finish_reason") for s in _model_spans(rec)] == ["tool_calls", "length"]
    assert rec.cost.tokens_out == 2 * 2048 and rec.cost.usd > 0       # the cut reply was paid for, so it counts


@pytest.mark.parametrize("reason", [None, "stop"])
async def test_a_reply_without_a_cut_runs_as_before(factory, reason):
    turns = [READ, {"content": "done", **({"finish_reason": reason} if reason else {})}]
    rec = await run_one(factory, factory.tasks.get("fs-02"), factory.recipes["v2-base"], "scripted/replay",
                        "scripted", backend=ScriptedBackend(turns))
    assert not any(v.kind == "malformed_output" for v in rec.violations)
    assert all(s["error"] is None for s in _model_spans(rec))
