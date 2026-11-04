"""Tool-call emulation (SUB-02, FWK-01): a model without native tool calling (caps.tool_calling: emulated) runs the
tool-using shapes. instructor prepares the request and validates the reply; the call stays inside ModelGateway; a
reply that fails validation is MODEL_PARSE and counts against Q like a native malformed tool call.

Offline: `scripted/emulated` writes the reference plan as JSON text; RawText below writes whatever a test gives it."""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import litellm
import pytest

from harness_factory.observability.span import SpanKind
from harness_factory.providers.catalog import ModelCaps, ServedModel
from harness_factory.provision.provisioner import run_key_for
from harness_factory.runner.runner import run_one
from harness_factory.shapes.common import RECOVERY_NOTE
from harness_factory.substrate import emulation
from harness_factory.substrate.errors import ErrorKind, HarnessError
from harness_factory.substrate.gateway import Completion, ModelGateway, ScriptedBackend

EMULATED = "scripted/emulated"

READ = '```json\n{"tool_calls": [{"name": "fs__read_file", "arguments": {"path": "config/app.ini"}}]}\n```'
WRITE = ('{"tool_calls": [{"name": "fs__write_file", "arguments": {"path": "config/app.ini", "content": '
         '"[server]\\nhost = 0.0.0.0\\nport = 9090\\n\\n[logging]\\nlevel = info\\n"}}]}')
FINAL = '{"tool_calls": [], "content": "Changed the port in config/app.ini to 9090."}'
BROKEN = '{"tool_calls": [{"name": "fs__read_file", "arguments": {"path": '           # truncated JSON
INVENTED = '{"tool_calls": [{"name": "fs__search_file", "arguments": {"q": "port"}}]}'  # not offered


class RawText:
    """A model without tool calling that replies with the given texts, in order. Records what it was sent."""

    reads_tools_from_prompt = True

    def __init__(self, texts: list[str]) -> None:
        self.texts = list(texts)
        self.seen: list[tuple[list[dict], list[dict]]] = []

    async def complete(self, messages, tools, slot, provider, served) -> Completion:
        self.seen.append((messages, tools))
        text = self.texts.pop(0) if self.texts else "done"
        return Completion(message={"role": "assistant", "content": text}, tokens_in=50, tokens_out=10)


def grade(rec, grader):
    return next(g for g in rec.grades if g["grader_id"] == grader)


def spans(rec):
    return [json.loads(line) for line in Path(rec.trace_path).read_text().splitlines()]


# ------------------------------------------------------------------ conversation translation

def test_plain_chat_has_no_tool_roles_and_leaves_the_run_messages_alone():
    call = {"id": "c1", "type": "function", "function": {"name": "fs__read_file", "arguments": '{"path": "a"}'}}
    run = [{"role": "system", "content": "S"}, {"role": "user", "content": "Task: x"},
           {"role": "assistant", "content": None, "tool_calls": [call]},
           {"role": "tool", "tool_call_id": "c1", "content": "error: no such file"}]
    before = json.dumps(run)
    chat = emulation.to_plain_chat(run)
    assert json.dumps(run) == before                                            # nothing modified in place
    assert [m["role"] for m in chat] == ["system", "user", "assistant", "user"]
    assert "tool_calls" not in chat[2] and json.loads(chat[2]["content"])["tool_calls"] == [
        {"name": "fs__read_file", "arguments": {"path": "a"}}]
    assert chat[3]["content"] == "[Tool result c1 fs__read_file]\nerror: no such file"
    # The scripted model reads tool results in either form; the emulation's closing request is not one.
    framed = emulation.with_tool_prompt(chat, [{"type": "function", "function": {"name": "fs__read_file"}}])
    assert emulation.latest_tool_result(framed) == emulation.latest_tool_result(run) == "error: no such file"
    assert emulation.latest_user_text(framed) == emulation.latest_user_text(run) == "Task: x"
    assert "fs__read_file" in framed[0]["content"] and framed[0]["content"].startswith("S\n\n")
    # instructor merges consecutive user messages: the closing request then ends the tool result / the task.
    from instructor import Mode
    from instructor.processing.response import handle_response_model
    model = emulation.reply_model(("fs__read_file",))
    _, req = handle_response_model(model, mode=Mode.MD_JSON, messages=framed)
    assert req["messages"][-1]["content"].startswith("[Tool result c1") and len(req["messages"]) == 4
    assert emulation.latest_tool_result(req["messages"]) == "error: no such file"
    first = emulation.with_tool_prompt(chat[:2], [{"type": "function", "function": {"name": "fs__read_file"}}])
    _, req = handle_response_model(model, mode=Mode.MD_JSON, messages=first)
    assert emulation.latest_user_text(req["messages"]) == "Task: x"
    assert emulation.latest_tool_result(req["messages"]) is None


# ------------------------------------------------------------------ native models: untouched

async def test_native_models_pass_straight_through(factory):
    native = factory.providers.get("groq")
    backend = ScriptedBackend([])
    assert emulation.ToolEmulation.wrap(backend, native) is backend           # no emulated model: no wrapper at all
    wrapped = emulation.ToolEmulation.wrap(RawText(["x"]), factory.providers.get("scripted"))
    assert isinstance(wrapped, emulation.ToolEmulation)
    tools = [{"type": "function", "function": {"name": "fs__read_file"}}]
    msgs = [{"role": "user", "content": "hi"}]
    await wrapped.complete(msgs, tools, None, None, factory.providers.served("scripted", "scripted/replay"))
    assert wrapped.inner.seen == [(msgs, tools)]                              # same objects: nothing rewritten


def test_hashes_of_native_models_are_unchanged_and_emulated_ones_carry_the_emulation(factory):
    task, recipe = factory.tasks.get("fs-01"), factory.recipes["v2-base"]
    declared = factory.declared
    for model, provider in (("scripted/replay", "scripted"), ("gpt-oss-120b", "groq")):
        served = factory.providers.served(provider, model)
        key = run_key_for(task, recipe, model, provider, 0, declared=declared, providers=factory.providers)
        assert key.env_hash == declared.env_hash(recipe.tools.mcp_servers, served.provider_model_id)
    emu = run_key_for(task, recipe, EMULATED, "scripted", 0, declared=declared, providers=factory.providers)
    assert emu.env_hash == declared.env_hash(recipe.tools.mcp_servers, f"emulated+emulated:{emulation.fingerprint()}")
    assert declared.env_hash(recipe.tools.mcp_servers, "emulated") != emu.env_hash


# ------------------------------------------------------------------ the P0 set, offline, through emulation

async def test_a_model_without_native_tools_solves_the_p0_set(factory, p0_tasks):
    for tid in p0_tasks:
        rec = await run_one(factory, factory.tasks.get(tid), factory.recipes["v2-base"], EMULATED, "scripted")
        assert rec.status.value == "SUCCESS", (tid, rec.error)
        assert rec.scores.trajectory == 1.0 and rec.cost.tool_calls > 0
        calls = [s for s in spans(rec) if s["kind"] == SpanKind.model_call.value]
        assert calls and all(s["status"] != "error" for s in calls)
        assert sum(s["attributes"].get("tool_calls", 0) for s in calls) == rec.cost.tool_calls


async def test_the_provider_is_never_sent_tools_or_tool_roles(factory):
    model = RawText([READ, WRITE, FINAL])
    rec = await run_one(factory, factory.tasks.get("fs-02"), factory.recipes["v2-base"], EMULATED, "scripted",
                        backend=model)
    assert rec.status.value == "SUCCESS" and rec.scores.trajectory == 1.0
    for messages, _ in model.seen:
        assert all(m["role"] in ("system", "user", "assistant") for m in messages)
        assert not any(m.get("tool_calls") for m in messages)
        assert "fs__write_file" in messages[0]["content"] and "json_schema" in messages[0]["content"]  # instructor
    assert model.seen[1][0][-1]["content"].startswith("[Tool result call_emu_1_0 fs__read_file]\n[server]")
    assert rec.cost.tokens_in == 150 and rec.cost.tokens_out == 30          # the backend's usage, counted once each


# ------------------------------------------------------------------ malformed replies count against Q

async def test_a_reply_that_fails_validation_ends_the_run_and_costs_q(factory):
    rec = await run_one(factory, factory.tasks.get("fs-02"), factory.recipes["v2-base"], EMULATED, "scripted",
                        backend=RawText([READ, BROKEN]))
    assert rec.status.value != "SUCCESS" and any(v.kind == "malformed_output" for v in rec.violations)
    assert rec.scores.trajectory == 0.5                    # one good call, one rejected: as a native rejection
    failed = [s for s in spans(rec) if s["kind"] == SpanKind.model_call.value and s["status"] == "error"]
    assert [s["error"] for s in failed] == [ErrorKind.MODEL_PARSE.value]
    assert "failed validation" in failed[0]["attributes"]["error_detail"]
    assert rec.cost.failed_calls == 1 and rec.cost.model_calls == 1


async def test_a_tool_that_was_not_offered_is_rejected_and_recovery_is_not_free(factory):
    task = factory.tasks.get("fs-02")
    rec = await run_one(factory, task, factory.recipes["v2-recover"], EMULATED, "scripted",
                        backend=RawText([INVENTED, READ, WRITE, FINAL]))
    assert rec.status.value == "SUCCESS" and rec.scores.outcome == 1.0
    assert rec.scores.trajectory == pytest.approx(1 - 1 / 3)               # 2 good calls + 1 rejected
    assert "rejected=1" in grade(rec, "trajectory")["detail"]
    clean = await run_one(factory, task, factory.recipes["v2-recover"], EMULATED, "scripted",
                          backend=RawText([READ, WRITE, FINAL]))
    assert rec.composite < clean.composite                                 # never as good as a clean run
    notes = [m for m in json.loads(Path(rec.transcript_path).read_text())["messages"]
             if m["role"] == "user" and m["content"].startswith(RECOVERY_NOTE[:20])]
    assert len(notes) == 1 and "fs__search_file" in notes[0]["content"]


async def test_prose_without_json_is_a_final_answer_not_a_rejection(factory):
    rec = await run_one(factory, factory.tasks.get("fs-02"), factory.recipes["v2-base"], EMULATED, "scripted",
                        backend=RawText([READ, WRITE, "All done: the port is now 9090."]))
    assert rec.status.value == "SUCCESS" and rec.scores.trajectory == 1.0
    assert rec.cost.failed_calls == 0


async def test_without_instructor_an_emulated_call_is_a_setup_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "instructor", None)                   # import instructor -> ImportError
    served = ServedModel(canonical="m", provider_model_id="m", caps=ModelCaps(tool_calling="emulated"))
    tools = [{"type": "function", "function": {"name": "fs__read_file"}}]
    with pytest.raises(HarnessError) as e:
        await emulation.ToolEmulation(RawText([READ])).complete([{"role": "user", "content": "x"}], tools, None,
                                                                None, served)
    assert e.value.kind == ErrorKind.PROVISION and "--extra emulation" in e.value.message


# ------------------------------------------------------------------ a real provider (LiteLLM mocked)

def _resp(content):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=None))],
                           usage=SimpleNamespace(prompt_tokens=100, completion_tokens=20))


async def test_litellm_emulated_model_gets_no_tools_and_errors_stay_in_the_gateway(factory, monkeypatch):
    groq = factory.providers.get("groq")                   # test-local: this factory's catalog only
    groq.models.append(ServedModel(canonical="no-tools-model", provider_model_id="vendor/no-tools", price_in=0.1,
                                   price_out=0.2, caps=ModelCaps(tool_calling="emulated")))
    sent = []
    script = [_resp('Calling the tool.\n```json\n{"tool_calls": [{"name": "fs__write_file", "arguments": '
                    '{"path": "notes/todo.txt", "content": "buy milk"}}]}\n```'),
              _resp("Created notes/todo.txt.")]

    async def fake(**kwargs):
        sent.append(kwargs)
        return script[len(sent) - 1]

    monkeypatch.setattr(litellm, "acompletion", fake)
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    rec = await run_one(factory, factory.tasks.get("fs-01"), factory.recipes["v2-base"], "no-tools-model", "groq")
    assert rec.status.value == "SUCCESS"
    assert all("tools" not in k for k in sent) and sent[0]["model"] == "groq/vendor/no-tools"
    assert all(m["role"] != "tool" for k in sent for m in k["messages"])
    assert sent[1]["messages"][-1]["content"].startswith("[Tool result call_emu_1_0 fs__write_file]\nwrote 8")
    assert rec.cost.tokens_in == 200 and rec.cost.usd > 0                   # usage and price as for any call

    async def denied(**kwargs):
        raise litellm.AuthenticationError("invalid api key", llm_provider="groq", model="vendor/no-tools")

    monkeypatch.setattr(litellm, "acompletion", denied)
    gw = ModelGateway(factory.providers, factory.ledger, rec_tracer(), "groq")
    slot = factory.recipes["v2-base"].with_model("no-tools-model").model_slots["executor"]
    with pytest.raises(HarnessError) as e:                 # classified by the gateway, not by the emulation
        await gw.complete(slot, [{"role": "user", "content": "x"}],
                          [{"type": "function", "function": {"name": "fs__read_file"}}])
    assert e.value.kind == ErrorKind.CONFIG


def rec_tracer():
    from harness_factory.observability.tracer import Tracer
    return Tracer("emulation-test")


async def test_wrapping_a_provider_keeps_json_mode_for_its_native_models(factory):
    """One emulated model wraps the provider's backend (#59); JSON mode (#51) must still reach the backend for the
    provider's native models, and for an emulated model's tool-less planner call."""
    class Recording:
        accepts_response_format = True

        def __init__(self):
            self.kwargs = []

        async def complete(self, messages, tools, slot, provider, served, **kw):
            self.kwargs.append(kw)
            return Completion(message={"role": "assistant", "content": "{}"})

    inner = Recording()
    gw = ModelGateway(factory.providers, factory.ledger, rec_tracer(), "scripted", backend=inner)
    assert isinstance(gw.backend, emulation.ToolEmulation) and gw.backend.accepts_response_format
    for model in ("scripted/replay", "scripted/emulated"):
        factory.providers.served("scripted", model).caps.structured_output = True   # test-local catalog
        slot = factory.recipes["v4-planner"].model_slots["planner"].model_copy(update={"canonical_model": model})
        await gw.complete(slot, [{"role": "user", "content": "plan"}], [], response_format={"type": "json_object"})
    assert inner.kwargs == [{"response_format": {"type": "json_object"}}] * 2
    assert not emulation.ToolEmulation(object()).accepts_response_format       # unchanged for backends without it


@pytest.mark.parametrize("recipe", ["v3-staged", "v4-planner", "v5-orchestrator", "v2-delegate"])
async def test_every_shape_takes_the_same_path_emulated_as_native(factory, recipe):
    task = factory.tasks.get("fs-02")
    model = "scripted/delegate" if recipe == "v2-delegate" else "scripted/replay"
    native = await run_one(factory, task, factory.recipes[recipe], model, "scripted")
    backend = None
    if recipe == "v2-delegate":                        # the delegating style, writing its calls as text
        from harness_factory.substrate.emulation import TextReplies
        from harness_factory.tasks.reference import PlanBackend, load_reference
        backend = TextReplies(PlanBackend(load_reference(task), "delegate"))
    emulated = await run_one(factory, task, factory.recipes[recipe], EMULATED, "scripted", backend=backend)
    assert native.status.value == emulated.status.value == "SUCCESS"
    assert (emulated.cost.model_calls, emulated.cost.tool_calls) == (native.cost.model_calls, native.cost.tool_calls)
    assert emulated.scores.trajectory == native.scores.trajectory
