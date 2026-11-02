"""The OTLP attributes Phoenix (OpenInference) and Langfuse (gen_ai / OpenInference) map are stable."""

from harness_factory.observability.span import Span, SpanKind
from harness_factory.observability.tracer import _annotate


class _Recorder:
    def __init__(self) -> None:
        self.attrs: dict = {}
        self.status = None

    def set_attribute(self, key, value) -> None:
        self.attrs[key] = value

    def set_status(self, status) -> None:
        self.status = status


def _span(kind: SpanKind, name: str, **kw) -> Span:
    return Span(trace_id="t", span_id="s", kind=kind, name=name, run_key="rk", start_ns=1, **kw)


def test_model_call_attributes():
    rec = _Recorder()
    _annotate(rec, _span(SpanKind.model_call, "gpt-oss-20b", tokens_in=12, tokens_out=3))
    assert rec.attrs == {
        "openinference.span.kind": "LLM", "hf.run_key": "rk", "hf.kind": "model_call",
        "llm.model_name": "gpt-oss-20b", "gen_ai.request.model": "gpt-oss-20b",
        "llm.token_count.prompt": 12, "llm.token_count.completion": 3,
        "gen_ai.usage.input_tokens": 12, "gen_ai.usage.output_tokens": 3,
    }


def test_span_kinds_and_tool_name():
    kinds = {SpanKind.run: "AGENT", SpanKind.tool_call: "TOOL", SpanKind.retrieval: "RETRIEVER",
             SpanKind.grade: "EVALUATOR", SpanKind.plan: "CHAIN"}
    for kind, expected in kinds.items():
        rec = _Recorder()
        _annotate(rec, _span(kind, "x"))
        assert rec.attrs["openinference.span.kind"] == expected
    rec = _Recorder()
    _annotate(rec, _span(SpanKind.tool_call, "sql.query"))
    assert rec.attrs["tool.name"] == "sql.query"
    assert "gen_ai.usage.input_tokens" not in rec.attrs
