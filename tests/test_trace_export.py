"""Traces in MLflow too (#116): the collector forwards the same OTLP traces to MLflow, the MLflow run carries the
exported trace's id, and `hfx traces replay` loads stored JSONL runs into the backends with their original times."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
import yaml
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from harness_factory.observability import replay as rp
from harness_factory.observability import tracer as tracer_mod
from harness_factory.runner.runner import run_one

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def otel(monkeypatch):
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(tracer_mod.WithoutMcpSpans(SimpleSpanProcessor(exporter)))
    monkeypatch.setattr(tracer_mod, "_otel_tracer", provider.get_tracer("harness_factory"))
    yield exporter
    provider.shutdown()


def test_the_collector_forwards_traces_to_mlflow():
    col = yaml.safe_load((ROOT / "observability/collector.yaml").read_text())
    mlflow = col["exporters"]["otlphttp/mlflow"]
    assert mlflow["endpoint"] == "http://mlflow:5000"                       # MLflow >= 3.6: OTLP/HTTP /v1/traces
    assert mlflow["headers"]["x-mlflow-experiment-id"] == "${env:MLFLOW_EXPERIMENT_ID}"
    assert col["service"]["pipelines"]["traces"]["exporters"] == ["otlphttp/phoenix", "otlphttp/langfuse",
                                                                  "otlphttp/mlflow"]
    compose = yaml.safe_load((ROOT / "compose.yaml").read_text())
    env = compose["services"]["otel-collector"]["environment"]
    assert env["MLFLOW_EXPERIMENT_ID"] == "${HF_MLFLOW_EXPERIMENT_ID:-1}"
    assert "sqlite" in compose["services"]["mlflow"]["command"]              # ingestion needs a SQL store


class RecordingTracker:
    """Stands in for ExperimentTracker: records what each run's mirror was given."""
    enabled = True
    failures = 0

    def __init__(self):
        self.calls = []

    def log(self, rec, recipe, **kw):
        self.calls.append(kw)


def test_the_mlflow_run_carries_the_exported_trace_id(factory, otel):
    factory.tracker = RecordingTracker()
    asyncio.run(run_one(factory, factory.tasks.get("fs-01"), factory.recipes["v2-base"], "scripted/replay", "scripted"))
    (kw,) = factory.tracker.calls
    (root,) = [s for s in otel.get_finished_spans() if s.attributes.get("hf.kind") == "run"]
    assert kw == {"otel_trace_id": format(root.context.trace_id, "032x")}       # MLflow shows it as tr-<id>


def test_without_export_the_mirror_gets_no_trace_id(factory, monkeypatch):
    monkeypatch.setattr(tracer_mod, "_otel_tracer", None)
    factory.tracker = RecordingTracker()
    asyncio.run(run_one(factory, factory.tasks.get("fs-01"), factory.recipes["v2-base"], "scripted/replay", "scripted"))
    assert factory.tracker.calls == [{}]


def test_the_tracker_tags_the_run_with_the_trace_id():
    from harness_factory.mlops.tracker import ExperimentTracker

    class FakeMlflow:
        def __init__(self):
            self.tags = {}

        def start_run(self, run_name):
            from contextlib import nullcontext
            return nullcontext()

        def log_params(self, p):
            pass

        def log_metrics(self, m):
            pass

        def set_tags(self, t):
            self.tags.update(t)

        def log_artifact(self, *a, **k):
            pass

    class Rec:
        class run_key:
            task_version, canonical_model, provider, seed = "fs-01@1", "m", "p", 0

            @staticmethod
            def as_str():
                return "key"

        class scores:
            outcome, trajectory = 1.0, 1.0

        class cost:
            tokens_in = tokens_out = tool_calls = 0
            usd = wall_s = 0.0

        class status:
            value = "SUCCESS"

        composite, task_class, trace_path, transcript_path = 1.0, "c", None, None

    class Recipe:
        id = "v2-base"

        @staticmethod
        def to_mlflow_params():
            return {}

    t = ExperimentTracker()
    t.enabled, t._mlflow = True, FakeMlflow()
    t.log(Rec, Recipe, otel_trace_id="ab" * 16)
    assert t._mlflow.tags == {"status": "SUCCESS", "run_key": "key", "otel_trace_id": "ab" * 16}
    t._mlflow.tags.clear()
    t.log(Rec, Recipe)
    assert "otel_trace_id" not in t._mlflow.tags


def test_replay_recreates_a_stored_run_with_its_times_and_tree(factory, monkeypatch):
    monkeypatch.setattr(tracer_mod, "_otel_tracer", None)                       # a run made with OTel off
    rec = asyncio.run(run_one(factory, factory.tasks.get("fs-02"), factory.recipes["v2-base"], "scripted/replay",
                              "scripted"))
    stored = [json.loads(line) for line in Path(rec.trace_path).read_text().splitlines()]

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    runs, spans = rp.replay([Path(rec.trace_path).parent], provider.get_tracer("harness_factory"))
    assert (runs, spans) == (1, len(stored))
    out = exporter.get_finished_spans()
    assert len(out) == len(stored) and all(s.attributes["hf.replayed"] for s in out)
    assert len({s.context.trace_id for s in out}) == 1                         # one trace per run

    by_name_start = {(s.attributes["hf.kind"], s.start_time): s for s in out}
    by_id = {s.context.span_id: s for s in out}
    for d in stored:
        o = by_name_start[(d["kind"], d["start_ns"])]                         # original start time
        assert o.end_time == max(d["end_ns"], d["start_ns"])                   # and end time
        parent = next((p for p in stored if p["span_id"] == d["parent_id"]), None)
        if parent is None:
            assert o.parent is None and o.name == "v2-base · fs-02" and o.attributes["hf.trace_id"] == d["trace_id"]
        else:
            assert by_id[o.parent.span_id].start_time == parent["start_ns"]
    llm = [s for s in out if s.attributes["hf.kind"] == "model_call"]
    assert llm and all(s.attributes["openinference.span.kind"] == "LLM" and s.attributes["llm.token_count.prompt"]
                       for s in llm)


def test_replay_needs_somewhere_to_send(monkeypatch, tmp_path):
    monkeypatch.setattr(tracer_mod, "_otel_tracer", None)
    monkeypatch.setattr(tracer_mod.settings, "OTLP_ENDPOINT", None)
    with pytest.raises(RuntimeError, match="OTEL_EXPORTER_OTLP_ENDPOINT is not set"):
        rp.replay([tmp_path])
    from typer.testing import CliRunner

    from harness_factory.cli import main as cli
    out = CliRunner().invoke(cli.app, ["traces", "replay", str(tmp_path)])
    assert out.exit_code == 2 and "OTEL_EXPORTER_OTLP_ENDPOINT" in out.output


def test_mlflow_is_pinned_to_a_version_that_ingests_otlp():
    """MLflow takes OTLP traces from 3.6 on; `latest` (or a copied .env.example) could move below or past it."""
    import re
    compose = yaml.safe_load((ROOT / "compose.yaml").read_text())
    image = compose["services"]["mlflow"]["image"]
    m = re.fullmatch(r"ghcr\.io/mlflow/mlflow:\$\{MLFLOW_TAG:-v(\d+)\.(\d+)\.(\d+)\}", image)
    assert m and (int(m[1]), int(m[2])) >= (3, 6), image
    env = (ROOT / ".env.example").read_text()
    assert re.search(r"^MLFLOW_TAG=v" + re.escape(".".join(m.groups())) + r"\b", env, re.M)   # the same pin
