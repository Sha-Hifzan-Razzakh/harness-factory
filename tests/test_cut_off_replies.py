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
from harness_factory.substrate.gateway import (
    LEAKED_TOOL_TOKENS,
    Completion,
    ScriptedBackend,
    cut_off_tool_calls,
    rejected_reply,
)

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
    assert cut_off_tool_calls(comp('{"sql": "SELECT 1"}', "length")) == ["sql__query"]   # HF-1: whole JSON at
    #                                                                                     the limit is cut off too
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


# HF-1 (RW-14, RW-15): three reply shapes the lab rounds saw passed on as a normal reply, so the run ended `done` as
# if the agent quit. Each is now malformed: re-asked with recover_malformed, otherwise a `malformed_output` stop.

FIX = {"tool_calls": [{"name": "fs__write_file", "arguments": {"path": "config/app.ini",
                                                              "content": "[server]\nhost = 0.0.0.0\nport = 9090\n\n"
                                                                         "[logging]\nlevel = info\n"}}]}
EMPTY_CUT = {"content": "", "finish_reason": "length"}                     # reasoning used every output token
HALF_CALL = {"tool_calls": [{"name": "fs__write_file", "arguments": {"path": "config/app.ini"}}],   # JSON closed,
             "finish_reason": "length"}                                                            # content missing
LEAKED = {"content": "<|start|>assistant<|channel|>commentary to=functions.fs__write_file?<|call|>"}
MENTIONS = {"content": "I edited the file with the write functions; the port is now 9090."}


def _recipe(factory, recover: int):
    base = factory.recipes["v2-recover"]
    return base.model_copy(update={"config": base.config.model_copy(update={"recover_malformed": recover})})


def _tool_spans(rec, name: str) -> list[dict]:
    return [s for s in _spans(rec) if s["kind"] == "tool_call" and s["name"] == name]


def _rejected(rec) -> list[str]:
    return [s["attributes"]["rejected"] for s in _model_spans(rec) if s["error"] == "MODEL_PARSE"]


def test_rejected_reply_shapes():
    slot = SimpleNamespace(max_output_tokens=1024)

    def comp(content=None, calls=None, reason="stop"):
        msg = {"role": "assistant", "content": content}
        if calls:
            msg["tool_calls"] = [{"id": "c", "type": "function", "function": {"name": n, "arguments": a}}
                                 for n, a in calls]
        return Completion(message=msg, finish_reason=reason)

    whole = [("fs__write_file", '{"path": "a.txt"}')]
    assert rejected_reply(comp(calls=whole, reason="length"), slot, True)[0] == "cut_off"
    assert rejected_reply(comp(calls=whole, reason="max_tokens"), slot, True)[0] == "cut_off"
    assert rejected_reply(comp(calls=whole, reason="tool_calls"), slot, True) is None
    for empty in (None, "", "  \n "):
        assert rejected_reply(comp(empty, reason="length"), slot, True)[0] == "cut_off"
        assert rejected_reply(comp(empty, reason="stop"), slot, True) is None       # an empty answer, not a cut
        assert rejected_reply(comp(empty, reason="length"), slot, False) is None    # compaction, judge: their own
    assert rejected_reply(comp("a long answer", reason="length"), slot, True) is None
    for token in LEAKED_TOOL_TOKENS:
        assert rejected_reply(comp(f"x {token} y"), slot, True)[0] == "leaked_tool_call"
        assert rejected_reply(comp(f"x {token} y"), slot, False) is None
    assert rejected_reply(comp(LEAKED["content"]), slot, True)[0] == "leaked_tool_call"
    for prose in (MENTIONS["content"], "Call functions.read_file to see it.", "a <call> tag", "to = functions"):
        assert rejected_reply(comp(prose), slot, True) is None
    # the note the model sees: why, and what fits; never the task's files, checks or grading
    _, why = rejected_reply(comp(calls=whole, reason="length"), slot, True)
    assert "output limit (1024 tokens)" in why and "shorter reply" in why and "several calls" in why
    assert "a.txt" not in why and len(why) <= 300                     # RECOVERY_NOTE shows the first 300 characters


BAD = pytest.mark.parametrize("bad", [EMPTY_CUT, HALF_CALL, LEAKED], ids=["empty-cut", "half-call", "leaked"])


@BAD
async def test_a_malformed_reply_is_re_asked_with_recovery(factory, bad):
    backend = Recording([READ, bad, FIX, {"content": "Set the port to 9090."}])
    rec = await run_one(factory, factory.tasks.get("fs-02"), _recipe(factory, 1), "scripted/replay", "scripted",
                        backend=backend)
    assert rec.status.value == "SUCCESS", rec.violations
    assert not any(v.kind == "malformed_output" for v in rec.violations)
    assert len(backend.requests) == 4
    note = backend.requests[2][-1]
    assert note["role"] == "user" and note["content"].startswith(RECOVERY_NOTE[:20])
    assert "config/app.ini" not in note["content"]
    assert not [m for m in backend.requests[2] if m["role"] == "assistant" and m.get("content")
                and m.get("content") == bad.get("content")]           # the bad reply is never sent back
    _assert_no_broken_arguments(backend.requests)
    assert len(_tool_spans(rec, "fs.write_file")) == 1               # only the good write ran, never the half call
    assert len(_rejected(rec)) == 1 and rec.scores.trajectory < 1.0   # the rejected reply is an invalid call in Q
    assert rec.cost.model_calls == 4


@BAD
async def test_without_recovery_a_malformed_reply_is_a_harness_stop(factory, bad):
    backend = Recording([READ, bad, {"content": "never reached"}])
    rec = await run_one(factory, factory.tasks.get("fs-02"), _recipe(factory, 0), "scripted/replay", "scripted",
                        backend=backend)
    assert rec.status.value in ("PARTIAL", "FAIL") and rec.status.value not in ("ERROR", "INCOMPLETE")
    assert any(v.kind == "malformed_output" for v in rec.violations)
    assert len(backend.requests) == 2 and not _tool_spans(rec, "fs.write_file")
    kind = "leaked_tool_call" if bad is LEAKED else "cut_off"
    assert [r.split(":")[0] for r in _rejected(rec)] == [kind]
    assert rec.cost.model_calls == 2 and rec.cost.tokens_out == sum(s["tokens_out"] for s in _model_spans(rec))


async def test_after_its_one_re_ask_a_second_malformed_reply_stops_the_run(factory):
    backend = Recording([READ, EMPTY_CUT, LEAKED, {"content": "never reached"}])
    rec = await run_one(factory, factory.tasks.get("fs-02"), _recipe(factory, 1), "scripted/replay", "scripted",
                        backend=backend)
    assert any(v.kind == "malformed_output" and "tool-call tokens" in v.detail for v in rec.violations)
    assert len(backend.requests) == 3 and len(_rejected(rec)) == 2


@pytest.mark.parametrize("last", [MENTIONS, {"content": "done", "finish_reason": "stop"}], ids=["mentions", "stop"])
async def test_whole_calls_and_ordinary_answers_run_as_before(factory, last):
    whole = {**FIX, "finish_reason": "tool_calls"}
    rec = await run_one(factory, factory.tasks.get("fs-02"), factory.recipes["v2-base"], "scripted/replay",
                        "scripted", backend=ScriptedBackend([READ, whole, last]))
    assert rec.status.value == "SUCCESS", rec.violations
    assert not _rejected(rec) and len(_tool_spans(rec, "fs.write_file")) == 1
    assert [s["attributes"].get("finish_reason") for s in _model_spans(rec)] == ["tool_calls", "tool_calls", "stop"]
