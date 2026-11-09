"""OpenRouter: the model shortlist and its limits (openrouter.ai/docs/api/reference/limits, checked 2026-11-09)."""

from __future__ import annotations

from harness_factory import settings
from harness_factory.providers.catalog import load_provider_catalog

SHORTLIST = {
    "deepseek-v4.1-flash": "deepseek/deepseek-v4.1-flash",
    "glm-5.3-flash": "z-ai/glm-5.3-flash",
    "qwen3.6-35b-a3b": "qwen/qwen3.6-35b-a3b",
    "claude-haiku-5.5": "anthropic/claude-haiku-5.5",
    "gpt-oss-120b": "openai/gpt-oss-120b",
    "nemotron-3-ultra:free": "nvidia/nemotron-3-ultra-550b-a55b:free",
}


def test_openrouter_serves_the_shortlist():
    cat = load_provider_catalog(settings.PROVIDERS_FILE)
    for canonical, model_id in SHORTLIST.items():
        m = cat.served("openrouter", canonical)
        assert m.provider_model_id == model_id
        assert m.free == model_id.endswith(":free")
        assert (m.price_in == 0 and m.price_out == 0) if m.free else m.price_out > 0


def test_openrouter_limits_free_and_paid_models_apart():
    """Paid variants: no platform cap, only the factory's 20 rpm pacing, one account bucket. The free variant:
    20 rpm and 1,000 a day (credits >= $10), in its own bucket."""
    cat = load_provider_catalog(settings.PROVIDERS_FILE)
    paid = cat.limits(cat.bucket("openrouter", "deepseek-v4.1-flash"))
    assert cat.bucket("openrouter", "deepseek-v4.1-flash") == "openrouter"
    assert paid.rpm == 20 and paid.rpd is None
    free_bucket = cat.bucket("openrouter", "nemotron-3-ultra:free")
    assert free_bucket != "openrouter"
    free = cat.limits(free_bucket)
    assert free.rpm == 20 and free.rpd == 1000
    assert sum(m.free for m in cat.get("openrouter").models) == 1    # a second free model needs a shared limit
