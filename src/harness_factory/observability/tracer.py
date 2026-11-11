"""Tracer: records Spans for one run, writes them as JSONL, and mirrors them to OpenTelemetry
when OTEL_EXPORTER_OTLP_ENDPOINT is set (the collector fans out to Phoenix and Langfuse)."""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from pathlib import Path

from openinference.semconv.trace import OpenInferenceSpanKindValues as OIKind
from openinference.semconv.trace import SpanAttributes as OI
from opentelemetry.sdk.trace import SpanProcessor

from harness_factory import settings
from harness_factory.observability import openinference
from harness_factory.observability.span import Span, SpanKind

_otel_tracer = None
_otel_provider = None


def _get_otel_tracer():
    global _otel_tracer, _otel_provider
    if _otel_tracer is not None or not settings.OTLP_ENDPOINT:
        return _otel_tracer
    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    provider = TracerProvider(resource=Resource.create({"service.name": "harness-factory"}))
    provider.add_span_processor(WithoutMcpSpans(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{settings.OTLP_ENDPOINT}/v1/traces"))))
    trace.set_tracer_provider(provider)
    _otel_provider = provider
    _otel_tracer = trace.get_tracer("harness_factory")
    return _otel_tracer


MCP_SDK_SCOPE = "mcp-python-sdk"         # the instrumentation scope of the MCP SDK's own "MCP send ..." spans


class WithoutMcpSpans(SpanProcessor):
    """A SpanProcessor that passes every span on to `inner` except the MCP SDK's (unless HF_TRACE_MCP_SPANS=1): our
    tool_call span already carries the call, its arguments and its result. Only that SDK's spans are dropped,
    never ours; they are leaves under our tool span, so nothing is left without its parent."""

    def __init__(self, inner) -> None:
        self.inner = inner

    @staticmethod
    def dropped(span) -> bool:
        scope = getattr(span, "instrumentation_scope", None)
        return getattr(scope, "name", None) == MCP_SDK_SCOPE and not settings.trace_mcp_spans()

    def on_start(self, span, parent_context=None) -> None:
        self.inner.on_start(span, parent_context=parent_context)

    def _on_ending(self, span) -> None:
        if not self.dropped(span) and hasattr(self.inner, "_on_ending"):
            self.inner._on_ending(span)

    def on_end(self, span) -> None:
        if not self.dropped(span):
            self.inner.on_end(span)

    def shutdown(self) -> None:
        self.inner.shutdown()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return self.inner.force_flush(timeout_millis)


# The open spans of the current asyncio task, innermost last, as (trace_id, span_id). A context variable, not a
# list on the Tracer: concurrent tool calls and V5 workers of one run each run in their own task (which copies the
# context), so each nests its spans under its own parent instead of under whichever sibling opened last. Context-local,
# so no run writes state another run (or task) can see.
_OPEN: ContextVar[tuple[tuple[str, str], ...]] = ContextVar("hf_open_spans", default=())

_OPENINFERENCE_KIND = {SpanKind.run: OIKind.AGENT, SpanKind.model_call: OIKind.LLM,
                       SpanKind.tool_call: OIKind.TOOL, SpanKind.retrieval: OIKind.RETRIEVER,
                       SpanKind.grade: OIKind.EVALUATOR, SpanKind.turn: OIKind.CHAIN}


def _annotate(otel, s: Span) -> None:
    """Attributes Phoenix (OpenInference) and Langfuse (gen_ai semantic conventions) both understand."""
    from opentelemetry.trace import Status, StatusCode

    otel.set_attribute(OI.OPENINFERENCE_SPAN_KIND, _OPENINFERENCE_KIND.get(s.kind, OIKind.CHAIN).value)
    otel.set_attribute("hf.run_key", s.run_key)
    otel.set_attribute("hf.kind", s.kind.value)
    if s.kind == SpanKind.model_call:
        otel.set_attribute(OI.LLM_MODEL_NAME, s.name)
        otel.set_attribute("gen_ai.request.model", s.name)
        otel.set_attribute(OI.LLM_TOKEN_COUNT_PROMPT, s.tokens_in)
        otel.set_attribute(OI.LLM_TOKEN_COUNT_COMPLETION, s.tokens_out)
        otel.set_attribute("gen_ai.usage.input_tokens", s.tokens_in)
        otel.set_attribute("gen_ai.usage.output_tokens", s.tokens_out)
    if s.kind == SpanKind.tool_call:
        otel.set_attribute(OI.TOOL_NAME, s.name)
        if s.tokens_in or s.tokens_out:                # an agent tool's reported usage (EXT-10)
            otel.set_attribute("gen_ai.usage.input_tokens", s.tokens_in)
            otel.set_attribute("gen_ai.usage.output_tokens", s.tokens_out)
    for k, v in s.attributes.items():
        if isinstance(v, (str, int, float, bool)):
            otel.set_attribute(f"hf.{k}", v)
    for k, v in s.otel.items():                        # OpenInference input/output, messages, metadata
        if isinstance(v, (str, int, float, bool)):
            otel.set_attribute(k, v)
    if s.otel_name:
        otel.update_name(s.otel_name)
    if s.status == "error":
        otel.set_status(Status(StatusCode.ERROR, s.error or "error"))


class Tracer:
    def __init__(self, run_key: str) -> None:
        self.run_key = run_key
        self.trace_id = uuid.uuid4().hex
        self.spans: list[Span] = []
        self.messages = openinference.MessageLog()     # per run: what HF_TRACE_MESSAGES=delta already sent
        self.otel_trace_id: str | None = None          # the exported trace's id (Phoenix, Langfuse, MLflow), if any

    # OpenTelemetry-only detail (Span.otel, never persisted). Each helper is a no-op when nothing exports spans.

    @staticmethod
    def exporting() -> bool:
        return _get_otel_tracer() is not None

    def llm_request(self, span: Span, messages: list[dict], tools: list[dict], params: dict, provider: str) -> None:
        """The messages a model call sends (all, or what its conversation hasn't sent yet), its tools and parameters.
        Set before the call, so a failed attempt carries its input too."""
        mode = settings.trace_messages()
        if mode == "off" or not self.exporting():
            return
        offset, sent = self.messages.pending(messages, mode)
        span.otel.update(openinference.llm_input(sent, tools, params, provider=provider, offset=offset,
                                                 limit=settings.trace_max_chars()))

    def llm_response(self, span: Span, messages: list[dict], message: dict, tokens_in: int, tokens_out: int) -> None:
        """The reply, and (delta) record that this conversation's messages went out with a successful call."""
        mode = settings.trace_messages()
        if mode == "off" or not self.exporting():
            return
        self.messages.commit(messages)
        span.otel.update(openinference.llm_output(message, tokens_in, tokens_out, settings.trace_max_chars()))

    def tool_io(self, span: Span, args, result: str | None) -> None:
        if self.exporting():
            span.otel.update(openinference.tool_io(args, result, settings.trace_max_chars()))

    def _parent(self) -> str | None:
        return next((sid for tid, sid in reversed(_OPEN.get()) if tid == self.trace_id), None)

    @contextmanager
    def span(self, kind: SpanKind, name: str, **attributes) -> Iterator[Span]:
        s = Span(trace_id=self.trace_id, span_id=uuid.uuid4().hex[:16], parent_id=self._parent(), kind=kind,
                 name=name, run_key=self.run_key, start_ns=time.time_ns(), attributes=dict(attributes))
        token = _OPEN.set((*_OPEN.get(), (self.trace_id, s.span_id)))
        otel_tracer = _get_otel_tracer()
        # The OTel span is opened live and made current, so nested spans (ours, and the MCP SDK's own
        # "MCP send tools/call" spans) share one trace tree per run in Phoenix and Langfuse.
        cm = otel_tracer.start_as_current_span(f"{kind.value}:{name}") if otel_tracer else nullcontext()
        with cm as otel:
            if otel is not None and self.otel_trace_id is None:
                self.otel_trace_id = format(otel.get_span_context().trace_id, "032x")
            try:
                yield s
            except Exception as e:  # recorded, then re-raised
                s.status = "error"
                s.error = s.error or type(e).__name__
                s.attributes.setdefault("exception", str(e)[:500])
                raise
            finally:
                s.end_ns = time.time_ns()
                try:
                    _OPEN.reset(token)
                except ValueError:          # closed in another context than it was opened in: drop just this one
                    _OPEN.set(tuple(o for o in _OPEN.get() if o[1] != s.span_id))
                self.spans.append(s)
                if otel is not None:
                    _annotate(otel, s)


    @staticmethod
    def flush() -> None:
        """Push buffered OTLP spans now; short CLI runs would otherwise exit before the batch is sent."""
        if _otel_provider is not None:
            _otel_provider.force_flush(timeout_millis=5000)

    def write_jsonl(self, path: Path | None = None) -> Path:
        settings.ensure_dirs()
        path = path or settings.TRACES_DIR / f"{self.trace_id}.jsonl"
        with path.open("w") as f:
            for s in sorted(self.spans, key=lambda x: x.start_ns):
                f.write(json.dumps(s.model_dump(mode="json")) + "\n")
        return path
