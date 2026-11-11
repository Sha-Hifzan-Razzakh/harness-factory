"""Model caps (#121): what a model can do (max output, reasoning, supported parameters, input modalities) and
where a router should serve it from (endpoint). Every new field is optional: absent means unknown, as before."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from harness_factory import settings
from harness_factory.providers.catalog import EndpointPrefs, ModelCaps, ReasoningCaps, load_provider_catalog

FULL = {"canonical": "m", "provider_model_id": "x/m", "price_in": 0.1, "price_out": 0.2,
        "caps": {"context_len": 131072, "max_output_tokens": 32768,
                 "reasoning": {"mode": "optional", "efforts": ["low", "medium", "high"]},
                 "supported_params": ["tools", "tool_choice", "reasoning", "max_tokens"],
                 "input_modalities": ["text", "image"]},
        "endpoint": {"order": ["deepinfra", "together"], "quantizations": ["fp8", "bf16"],
                     "require_parameters": True, "allow_fallbacks": False}}


def test_the_catalog_loads_without_the_new_fields():
    cat = load_provider_catalog(settings.PROVIDERS_FILE)
    m = cat.served("groq", "gpt-oss-120b")
    assert (m.caps.max_output_tokens, m.caps.reasoning, m.caps.supported_params, m.caps.input_modalities,
            m.endpoint) == (None, None, None, None, None)


def test_the_catalog_loads_with_every_field(tmp_path):
    p = tmp_path / "providers.yaml"
    p.write_text(yaml.safe_dump({"providers": [{"id": "r", "kind": "litellm", "litellm_prefix": "openrouter",
                                                "models": [FULL]}]}))
    m = load_provider_catalog(p).served("r", "m")
    assert m.caps.max_output_tokens == 32768 and m.caps.reasoning.efforts == ["low", "medium", "high"]
    assert m.caps.supported_params[0] == "tools" and m.caps.input_modalities == ["text", "image"]
    assert m.endpoint.order == ["deepinfra", "together"] and m.endpoint.require_parameters is True


@pytest.mark.parametrize("caps, error", [
    ({"context_len": 8000, "max_output_tokens": 9000}, "exceeds context_len"),
    ({"max_output_tokens": 0}, "greater than 0"),
    ({"context_len": 0}, "greater than 0"),
    ({"reasoning": {"mode": "none", "efforts": ["low"]}}, "takes no efforts"),
    ({"reasoning": {"mode": "optional", "efforts": ["low", "low"]}}, "listed twice"),
    ({"reasoning": {"mode": "optional", "efforts": ["max"]}}, "Input should be"),
    ({"reasoning": {"mode": "sometimes"}}, "Input should be"),
    ({"reasoning": {"mode": "always", "budget": 4000}}, "Extra inputs"),
    ({"input_modalities": ["image"]}, "must include text"),
    ({"input_modalities": ["text", "smell"]}, "Input should be"),
    ({"max_output_token": 4096}, "Extra inputs"),                        # a typo is refused, not ignored
])
def test_caps_validation(caps, error):
    with pytest.raises(ValidationError, match=error):
        ModelCaps(**caps)


@pytest.mark.parametrize("endpoint, error", [
    ({"order": ["a", "a"]}, "listed once"),
    ({"quantizations": [""]}, "non-empty"),
    ({"sort": "price"}, "Extra inputs"),
])
def test_endpoint_validation(endpoint, error):
    with pytest.raises(ValidationError, match=error):
        EndpointPrefs(**endpoint)


def test_reasoning_always_without_efforts_is_fine():
    assert ReasoningCaps(mode="always").efforts == []


# ------------------------------------------------------------------ authoring keeps them

@pytest.fixture
def svc(tmp_path):
    from harness_factory.authoring.service import AuthoringService
    root = tmp_path / "repo"
    root.mkdir()
    for name in ("catalog", "prompts"):
        shutil.copytree(settings.ROOT / name, root / name)
    return AuthoringService(root, results_url=f"sqlite:///{tmp_path / 'results.db'}", author="tester"), root


def test_authoring_writes_and_reads_back_every_field(svc):
    service, root = svc
    path = root / "catalog/providers.yaml"
    comments = [line for line in path.read_text().splitlines() if line.strip().startswith("#")]
    model = {"provider": "openrouter", **FULL, "canonical": "caps-test", "provider_model_id": "x/caps-test"}
    r = service.create("model", model, note="all caps")
    assert r.status == "applied", r.issues
    text = path.read_text()
    assert [line for line in text.splitlines() if line.strip().startswith("#")] == comments    # comments kept
    entry = load_provider_catalog(path).served("openrouter", "caps-test")
    assert entry.caps.model_dump() == ModelCaps(**FULL["caps"]).model_dump()
    assert entry.endpoint == EndpointPrefs(**FULL["endpoint"])

    # an update keeps what it doesn't change; a removed field disappears from the line
    r = service.update("model", {**model, "caps": {**FULL["caps"], "max_output_tokens": None}}, note="unknown max")
    assert r.status == "applied" and "max_output_tokens" not in Path(path).read_text().split("caps-test")[1][:400]
    assert load_provider_catalog(path).served("openrouter", "caps-test").caps.max_output_tokens is None

    # the history keeps each version's fields: restoring v1 brings max_output_tokens back
    assert service.restore("model", "openrouter/caps-test", 1, note="back").status == "applied"
    assert load_provider_catalog(path).served("openrouter", "caps-test").caps.max_output_tokens == 32768


def test_authoring_refuses_what_the_catalog_refuses(svc):
    service, _ = svc
    bad = {"provider": "openrouter", "canonical": "bad", "provider_model_id": "x/bad",
           "caps": {"context_len": 1000, "max_output_tokens": 2000}}
    from harness_factory.authoring.service import AuthoringError
    with pytest.raises(AuthoringError, match="exceeds context_len"):
        service.create("model", bad)


def test_a_compact_line_leaves_defaults_out():
    from harness_factory.authoring.providers_edit import compact
    line = compact({"canonical": "m", "provider_model_id": "x/m", "price_in": 0, "price_out": 0,
                    "caps": ModelCaps().model_dump(), "endpoint": EndpointPrefs(order=["a"]).model_dump()})
    assert line == {"canonical": "m", "provider_model_id": "x/m", "endpoint": {"order": ["a"]}}


def test_adding_the_fields_left_every_existing_model_hash_alone():
    """The authoring snapshot hashes each model; unset new fields stay out of it, so no model looks changed."""
    from harness_factory.authoring.snapshot import _model_identity
    from harness_factory.providers.catalog import ServedModel
    m = ServedModel(canonical="m", provider_model_id="x/m", price_in=1.0)
    assert set(_model_identity(m)) == {"canonical", "provider_model_id", "price_in", "price_out", "free", "caps",
                                       "limits"}
    assert set(_model_identity(m)["caps"]) == {"tool_calling", "structured_output", "context_len"}
    full = ServedModel(**FULL)
    assert _model_identity(full)["endpoint"]["order"] == ["deepinfra", "together"]
