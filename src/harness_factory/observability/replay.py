"""Replay stored JSONL traces over OTLP (`hfx traces replay`): load runs made before OpenTelemetry was on, or before
a backend was added to the collector (MLflow), into Phoenix, Langfuse and MLflow without running anything again.

Each JSONL file is one run's spans (ids, parents, start and end times, kinds, tokens, attributes). Every span is
re-created as an OpenTelemetry span with its original start and end time, under the replayed copy of its parent,
and annotated exactly as a live span is (`tracer._annotate`), so the backends read it the same way. The OTel ids are
new (a replay is a copy), each span says `hf.replayed: true`, and the root carries the JSONL trace id
(`hf.trace_id`). What was never persisted can't come back: the OpenInference messages and inputs that live spans
carry only in `Span.otel` (#116) are not in the JSONL, so a replayed model call shows its tokens and previews, not
the chat. A root of a run from before #116 is named `<recipe> · <task>` from its stored attributes.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

from harness_factory.observability.span import Span, SpanKind


def load_spans(path: Path) -> list[Span]:
    spans = [Span.model_validate(json.loads(line)) for line in path.read_text().splitlines() if line.strip()]
    return sorted(spans, key=lambda s: (s.start_ns, s.end_ns))


def trace_files(paths: Iterable[Path]) -> list[Path]:
    """JSONL files named directly, plus every *.jsonl in a named directory, in order."""
    out: list[Path] = []
    for p in paths:
        out += sorted(p.glob("*.jsonl")) if p.is_dir() else [p]
    return out


def _root_name(s: Span) -> str | None:
    recipe, task = s.attributes.get("recipe"), s.attributes.get("task")
    if s.kind == SpanKind.run and recipe and task:
        return f"{recipe} · {str(task).partition('@')[0]}"
    return None


def replay_file(path: Path, otel_tracer) -> int:
    """Re-create one run's spans on `otel_tracer`. Returns how many were sent."""
    from opentelemetry import context as otel_context
    from opentelemetry import trace

    from harness_factory.observability.tracer import _annotate

    spans = load_spans(path)
    made: dict[str, object] = {}
    for s in spans:
        parent = made.get(s.parent_id) if s.parent_id else None
        ctx = trace.set_span_in_context(parent) if parent is not None else otel_context.Context()
        otel = otel_tracer.start_span(f"{s.kind.value}:{s.name}", context=ctx, start_time=s.start_ns)
        _annotate(otel, s)
        if s.parent_id is None or parent is None:
            otel.set_attribute("hf.trace_id", s.trace_id)
            if name := _root_name(s):
                otel.update_name(name)
        otel.set_attribute("hf.replayed", True)
        made[s.span_id] = otel
    for s in reversed(spans):                       # children end before their parents, as they did live
        made[s.span_id].end(end_time=max(s.end_ns, s.start_ns))
    return len(spans)


def replay(paths: Iterable[Path], otel_tracer=None) -> tuple[int, int]:
    """(runs, spans) replayed. Uses the factory's exporter (OTEL_EXPORTER_OTLP_ENDPOINT) unless given a tracer."""
    from harness_factory.observability.tracer import Tracer, _get_otel_tracer

    otel_tracer = otel_tracer or _get_otel_tracer()
    if otel_tracer is None:
        raise RuntimeError("OTEL_EXPORTER_OTLP_ENDPOINT is not set: nowhere to send the spans (point it at the "
                           "collector, http://localhost:4318)")
    runs = spans = 0
    for f in trace_files(paths):
        spans += replay_file(f, otel_tracer)
        runs += 1
    Tracer.flush()
    return runs, spans
