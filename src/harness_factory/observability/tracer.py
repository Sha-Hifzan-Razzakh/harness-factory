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

from harness_factory import settings
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
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{settings.OTLP_ENDPOINT}/v1/traces")))
    trace.set_tracer_provider(provider)
    _otel_provider = provider
    _otel_tracer = trace.get_tracer("harness_factory")
    return _otel_tracer


# The open spans of the current asyncio task, innermost last, as (trace_id, span_id). A context variable, not a
# list on the Tracer: concurrent tool calls and V5 workers of one run each run in their own task (which copies the
# context), so each nests its spans under its own parent instead of under whichever sibling opened last. Context-local,
# so no run writes state another run (or task) can see.
_OPEN: ContextVar[tuple[tuple[str, str], ...]] = ContextVar("hf_open_spans", default=())

_OPENINFERENCE_KIND = {SpanKind.run: OIKind.AGENT, SpanKind.model_call: OIKind.LLM,
                       SpanKind.tool_call: OIKind.TOOL, SpanKind.retrieval: OIKind.RETRIEVER,
                       SpanKind.grade: OIKind.EVALUATOR}


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
    if s.status == "error":
        otel.set_status(Status(StatusCode.ERROR, s.error or "error"))


class Tracer:
    def __init__(self, run_key: str) -> None:
        self.run_key = run_key
        self.trace_id = uuid.uuid4().hex
        self.spans: list[Span] = []

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
