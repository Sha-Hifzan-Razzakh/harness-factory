"""Phoenix-readable traces (#116): OpenInference attributes on the run, model-call and tool-call spans, exported
through an in-memory OpenTelemetry exporter. Observability only: the JSONL trace and every score stay the same."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from harness_factory.observability import tracer as tracer_mod
from harness_factory.observability.span import SpanKind
from harness_factory.runner.runner import run_one

SECRET = "sk-test-0123456789abcdefSECRET"


@pytest.fixture
def otel(monkeypatch):
    """Spans exported in memory: the factory's tracer points at a private provider for this test only."""
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(tracer_mod.WithoutMcpSpans(SimpleSpanProcessor(exporter)))
    monkeypatch.setattr(tracer_mod, "_otel_tracer", provider.get_tracer("harness_factory"))
    yield exporter
    provider.shutdown()


def run(factory, task="fs-01", recipe="v2-base"):
    return asyncio.run(run_one(factory, factory.tasks.get(task), factory.recipes[recipe], "scripted/replay",
                               "scripted", 0, campaign_id="campaign-test"))


def jsonl(path) -> list[dict]:
    return [json.loads(line) for line in Path(path).read_text().splitlines()]


def by_kind(exporter, kind: str) -> list:
    return [s for s in exporter.get_finished_spans() if s.attributes.get("hf.kind") == kind]


def test_root_llm_and_tool_spans_read_as_a_run_a_chat_and_json(factory, otel):
    rec = run(factory)
    (root,) = by_kind(otel, "run")
    a = root.attributes
    assert root.name == "v2-base · fs-01" and a["hf.run_key"]                     # the run key stays
    assert a["openinference.span.kind"] == "AGENT" and a["agent.name"] == "v2-base"
    assert a["session.id"] == "campaign-test"
    assert factory.tasks.get("fs-01").instruction[:40] in a["input.value"]
    assert a["output.value"] == rec.output or a["output.value"].startswith("(no final message: ended by")
    meta = json.loads(a["metadata"])
    assert meta["task"] == "fs-01@1" or meta["task"].startswith("fs-01")
    assert meta["recipe"] == "v2-base" and meta["model"] == "scripted/replay" and meta["provider"] == "scripted"
    assert meta["status"] == rec.status.value and meta["model_calls"] >= 1 and meta["tool_calls"] >= 1
    assert meta.get("system_prompt")

    llm = by_kind(otel, "model_call")
    assert llm and all(s.attributes["llm.input_messages.0.message.role"] for s in llm)
    assert all(s.attributes["llm.output_messages.0.message.role"] == "assistant" for s in llm)
    first = llm[0].attributes
    assert first["llm.input_messages.0.message.role"] == "system"
    assert first["llm.output_messages.0.message.tool_calls.0.tool_call.function.name"]
    assert json.loads(first["llm.invocation_parameters"])["max_tokens"] > 0
    assert first["llm.provider"] == "scripted" and "llm.tools.0.tool.json_schema" in first
    assert first["input.mime_type"] == "application/json" and json.loads(first["input.value"])["messages"]

    tools = by_kind(otel, "tool_call")
    assert tools and all(s.attributes["input.mime_type"] == "application/json" for s in tools)
    assert all(isinstance(json.loads(s.attributes["input.value"]), dict) for s in tools)
    assert all("output.value" in s.attributes and s.attributes["tool.parameters"] for s in tools)


def test_delta_sends_a_conversation_once(factory, otel, monkeypatch):
    monkeypatch.delenv("HF_TRACE_MESSAGES", raising=False)          # delta is the default
    run(factory, "fs-02")
    llm = by_kind(otel, "model_call")
    assert len(llm) >= 2
    second = llm[1].attributes
    assert second["llm.input_messages.0.message.role"] != "system"     # the system prompt went with call 1
    assert second["hf.input_messages_offset"] >= 2
    roles = [v for k, v in second.items() if k.endswith(".message.role") and k.startswith("llm.input_messages")]
    assert "tool" in roles                                             # what call 1's tool calls returned


def test_full_repeats_every_message(factory, otel, monkeypatch):
    monkeypatch.setenv("HF_TRACE_MESSAGES", "full")
    run(factory, "fs-02")
    llm = by_kind(otel, "model_call")
    assert all(s.attributes["llm.input_messages.0.message.role"] == "system" for s in llm)
    assert all("hf.input_messages_offset" not in s.attributes for s in llm)


def test_off_sends_no_messages(factory, otel, monkeypatch):
    monkeypatch.setenv("HF_TRACE_MESSAGES", "off")
    run(factory)
    llm = by_kind(otel, "model_call")
    assert llm and not any(k.startswith(("llm.input_messages", "llm.output_messages"))
                           for s in llm for k in s.attributes)
    assert all(s.attributes["llm.token_count.prompt"] >= 0 for s in llm)         # what was there before stays


def test_bad_setting_is_refused(monkeypatch):
    from harness_factory import settings
    monkeypatch.setenv("HF_TRACE_MESSAGES", "everything")
    with pytest.raises(ValueError, match="full, delta or off"):
        settings.trace_messages()


def test_no_config_value_reaches_an_attribute(factory, otel, monkeypatch):
    """Provider keys and other environment values never go into span attributes: only messages and slot params."""
    for var in ("OPENAI_API_KEY", "GROQ_API_KEY", "OPENROUTER_API_KEY", "HF_API_TOKEN"):
        monkeypatch.setenv(var, SECRET)
    run(factory)
    values = [str(v) for s in otel.get_finished_spans() for v in s.attributes.values()]
    assert values and not any(SECRET in v for v in values)


def test_the_jsonl_trace_and_scores_dont_change_with_tracing(factory, otel, monkeypatch, p0_tasks):
    """Grading equality: the same scores, statuses and persisted spans with OpenTelemetry on and off."""
    on = {t: run(factory, t) for t in p0_tasks}
    monkeypatch.setattr(tracer_mod, "_otel_tracer", None)
    off = {t: run(factory, t) for t in p0_tasks}
    for t in p0_tasks:
        assert (on[t].composite, on[t].scores.outcome, on[t].status) == \
            (off[t].composite, off[t].scores.outcome, off[t].status), t
        dumped, plain = (jsonl(on[t].trace_path), jsonl(off[t].trace_path))
        assert dumped and not any("otel" in d or "otel_name" in d for d in dumped)
        assert [d["attributes"].keys() for d in dumped] == [d["attributes"].keys() for d in plain]


def test_a_failed_attempt_carries_its_input_and_the_retry_sends_it_again(otel, monkeypatch):
    """A retryable failure: both attempts' spans have the messages (the log commits only on success)."""
    from harness_factory.observability.tracer import Tracer
    from harness_factory.providers.catalog import load_provider_catalog
    from harness_factory.providers.quota import QuotaLedger
    from harness_factory.recipes.model import ModelSlot
    from harness_factory.substrate.errors import ErrorKind, HarnessError
    from harness_factory.substrate.gateway import Completion, ModelGateway

    class Flaky:
        calls = 0

        async def complete(self, messages, tools, slot, provider, served, **kw):
            self.calls += 1
            if self.calls == 1:
                raise HarnessError(ErrorKind.PROVIDER_ERROR, "try again")
            return Completion(message={"role": "assistant", "content": "done"}, tokens_in=3, tokens_out=1, usd=0)

    async def no_sleep(_):
        return None

    monkeypatch.setattr(ModelGateway, "_sleep", staticmethod(no_sleep))
    tracer = Tracer("t")
    from harness_factory import settings
    catalog = load_provider_catalog(settings.PROVIDERS_FILE)
    gw = ModelGateway(catalog, QuotaLedger(catalog), tracer, "scripted", backend=Flaky())
    msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": "go"}]
    asyncio.run(gw.complete(ModelSlot(canonical_model="scripted/replay"), msgs, []))
    llm = [s for s in otel.get_finished_spans() if s.attributes.get("hf.kind") == "model_call"]
    assert len(llm) == 2 and llm[0].status.status_code.name == "ERROR"
    assert all(s.attributes["llm.input_messages.0.message.content"] == "sys" for s in llm)
    assert llm[1].attributes["llm.output_messages.0.message.content"] == "done"
    assert [s.kind for s in tracer.spans] == [SpanKind.model_call, SpanKind.model_call]


def test_mcp_sdk_spans_are_dropped_unless_asked_for(monkeypatch):
    class Scope:
        def __init__(self, name):
            self.name = name

    class FakeSpan:
        def __init__(self, scope):
            self.instrumentation_scope = Scope(scope)

    class Inner:
        def __init__(self):
            self.ended = []

        def on_end(self, span):
            self.ended.append(span.instrumentation_scope.name)

    inner = Inner()
    p = tracer_mod.WithoutMcpSpans(inner)
    monkeypatch.delenv("HF_TRACE_MCP_SPANS", raising=False)
    p.on_end(FakeSpan("mcp-python-sdk"))
    p.on_end(FakeSpan("harness_factory"))
    assert inner.ended == ["harness_factory"]
    monkeypatch.setenv("HF_TRACE_MCP_SPANS", "1")
    p.on_end(FakeSpan("mcp-python-sdk"))
    assert inner.ended == ["harness_factory", "mcp-python-sdk"]


def test_long_values_are_capped(monkeypatch):
    from harness_factory.observability import openinference
    monkeypatch.setenv("HF_TRACE_MAX_CHARS", "200")
    from harness_factory import settings
    limit = settings.trace_max_chars()
    out = openinference.tool_io({"path": "a"}, json.dumps({"x": "y" * 1000}), limit)
    assert len(out["output.value"]) < 260 and out["output.mime_type"] == "text/plain"     # cut JSON is text
    msg = openinference.message_attributes("llm.input_messages", 0, {"role": "user", "content": "z" * 1000}, limit)
    assert msg["llm.input_messages.0.message.content"].endswith("[800 more characters]")
