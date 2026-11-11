"""Pinned OpenRouter endpoints (#124, A5): a model's `endpoint` preferences go with each OpenRouter call, the upstream
that answered is recorded on the span and the run, and a pinned endpoint is part of env_hash (new cells)."""

from __future__ import annotations

from types import SimpleNamespace

import litellm
import pytest

from harness_factory.observability.tracer import Tracer
from harness_factory.providers.catalog import EndpointPrefs
from harness_factory.providers.quota import QuotaLedger
from harness_factory.recipes.model import ModelSlot
from harness_factory.substrate.gateway import ModelGateway

PINNED = EndpointPrefs(order=["deepinfra", "together"], quantizations=["fp8"], require_parameters=True,
                       allow_fallbacks=False)


def resp(provider: str | None = "DeepInfra"):
    msg = SimpleNamespace(content="hi", tool_calls=None)
    return SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason="stop")], provider=provider,
                           usage=SimpleNamespace(prompt_tokens=10, completion_tokens=2))


@pytest.fixture
def calls(monkeypatch):
    seen = []

    async def fake(**kwargs):
        seen.append(kwargs)
        return resp()

    monkeypatch.setattr(litellm, "acompletion", fake)
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setenv("GROQ_API_KEY", "k")
    return seen


def catalog_with(factory, provider: str, canonical: str, endpoint: EndpointPrefs | None):
    cat = factory.providers.model_copy(deep=True)
    cat.served(provider, canonical).endpoint = endpoint
    return cat


async def test_the_preferences_go_with_each_openrouter_call(factory, calls):
    cat = catalog_with(factory, "openrouter", "gpt-oss-120b", PINNED)
    tracer = Tracer("t")
    gw = ModelGateway(cat, QuotaLedger(cat), tracer, "openrouter")
    comp = await gw.complete(ModelSlot(canonical_model="gpt-oss-120b"), [{"role": "user", "content": "x"}], [])
    assert calls[0]["extra_body"] == {"provider": {"order": ["deepinfra", "together"], "quantizations": ["fp8"],
                                                   "require_parameters": True, "allow_fallbacks": False}}
    assert comp.served_by == "DeepInfra"
    (span,) = [s for s in tracer.spans if s.kind.value == "model_call"]
    assert span.attributes["served_by"] == "DeepInfra"


async def test_unpinned_models_send_nothing(factory, calls):
    cat = factory.providers
    gw = ModelGateway(cat, QuotaLedger(cat), Tracer("t"), "openrouter")
    await gw.complete(ModelSlot(canonical_model="gpt-oss-120b"), [{"role": "user", "content": "x"}], [])
    assert "extra_body" not in calls[0]


async def test_other_providers_never_get_openrouter_preferences(factory, calls):
    cat = catalog_with(factory, "groq", "gpt-oss-120b", PINNED)
    gw = ModelGateway(cat, QuotaLedger(cat), Tracer("t"), "groq")
    await gw.complete(ModelSlot(canonical_model="gpt-oss-120b"), [{"role": "user", "content": "x"}], [])
    assert "extra_body" not in calls[0]


def test_only_defaults_left_out_of_the_request():
    from harness_factory.providers.catalog import ServedModel
    m = ServedModel(canonical="m", provider_model_id="x/m", endpoint=EndpointPrefs(order=["a"]))
    assert m.endpoint_request() == {"order": ["a"], "require_parameters": False, "allow_fallbacks": True}
    assert ServedModel(canonical="m", provider_model_id="x/m").endpoint_request() is None


def test_a_pinned_endpoint_is_part_of_env_hash(factory):
    """Decision (#124): a pinned endpoint changes what serves the model, so it enters env_hash when set. Unpinned
    models keep their env hash."""
    from harness_factory.provision.provisioner import run_key_for
    task, recipe = factory.tasks.get("fs-01"), factory.recipes["v2-base"]

    def key(cat):
        return run_key_for(task, recipe, "gpt-oss-120b", "openrouter", 0, declared=factory.declared,
                           providers=cat).env_hash
    plain = key(factory.providers)
    assert key(catalog_with(factory, "openrouter", "gpt-oss-120b", None)) == plain
    pinned = key(catalog_with(factory, "openrouter", "gpt-oss-120b", PINNED))
    assert pinned != plain
    other = key(catalog_with(factory, "openrouter", "gpt-oss-120b", EndpointPrefs(order=["together"])))
    assert other not in (plain, pinned)


def test_the_run_records_which_endpoints_answered(factory, monkeypatch):
    """served_by on the record and the store, next to served_id (comma-joined, sorted, when fallbacks mixed)."""
    import asyncio

    from harness_factory.observability.span import SpanKind
    from harness_factory.runner.runner import run_one
    from harness_factory.substrate.gateway import ScriptedBackend

    class Upstream(ScriptedBackend):
        async def complete(self, *a, **kw):
            comp = await super().complete(*a, **kw)
            return comp.model_copy(update={"served_by": "Together" if self.i % 2 else "DeepInfra"})

    script = [{"tool_calls": [{"name": "fs__read_file", "arguments": {"path": "notes/README.txt"}}]},
              {"tool_calls": [{"name": "fs__list_dir", "arguments": {"path": "notes"}}]}, {"content": "done"}]
    rec = asyncio.run(run_one(factory, factory.tasks.get("fs-01"), factory.recipes["v2-base"], "scripted/replay",
                              "scripted", backend=Upstream(script)))
    assert rec.served_by == "DeepInfra,Together"
    (row,) = [r for r in factory.store.rows() if r["run_key"] == rec.run_key.as_str()]
    assert row["served_by"] == "DeepInfra,Together"
    import json
    from pathlib import Path
    spans = [json.loads(line) for line in Path(rec.trace_path).read_text().splitlines()]
    assert {s["attributes"].get("served_by") for s in spans if s["kind"] == SpanKind.model_call.value} == \
        {"DeepInfra", "Together"}


def test_authoring_asks_to_confirm_an_endpoint_change(tmp_path):
    """Pinning changes env_hash, so authoring treats it like a provider model id change: confirm first."""
    import shutil

    from harness_factory import settings
    from harness_factory.authoring.service import AuthoringService
    root = tmp_path / "repo"
    root.mkdir()
    for name in ("catalog", "prompts"):
        shutil.copytree(settings.ROOT / name, root / name)
    svc = AuthoringService(root, results_url=f"sqlite:///{tmp_path / 'r.db'}", author="t")
    model = {"provider": "openrouter", "canonical": "gpt-oss-120b", "provider_model_id": "openai/gpt-oss-120b",
             "price_in": 0.10, "price_out": 0.50, "caps": {"context_len": 131072},
             "endpoint": {"order": ["deepinfra"]}}
    r = svc.update("model", model)
    assert r.status == "needs_confirm" and r.impact.models_changed == ["openrouter/gpt-oss-120b"]
    assert svc.update("model", model, confirm=True).status == "applied"
