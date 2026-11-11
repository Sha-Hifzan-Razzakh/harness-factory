"""OpenRouter: the model shortlist and its limits (openrouter.ai/docs/api_reference/limits, checked 2026-11-11)."""

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
    """Paid variants: no platform cap, so no pacing, one account bucket. The free variant: 20 rpm and 1,000 a day
    (credits >= $10), in its own bucket."""
    cat = load_provider_catalog(settings.PROVIDERS_FILE)
    paid = cat.limits(cat.bucket("openrouter", "deepseek-v4.1-flash"))
    assert cat.bucket("openrouter", "deepseek-v4.1-flash") == "openrouter"
    assert (paid.rpm, paid.rpd, paid.tpm, paid.tpd) == (None, None, None, None)
    free_bucket = cat.bucket("openrouter", "nemotron-3-ultra:free")
    assert free_bucket != "openrouter"
    free = cat.limits(free_bucket)
    assert free.rpm == 20 and free.rpd == 1000
    assert sum(m.free for m in cat.get("openrouter").models) == 1    # a second free model needs a shared limit


async def test_paid_models_are_not_paced_but_the_free_model_is():
    """The quota ledger lets 50 paid calls through in one minute without a wait (they used to wait 45-52 s each
    at 20/min); the free model's 21st call in the minute waits for the next window."""
    from harness_factory.providers.quota import QuotaLedger
    cat = load_provider_catalog(settings.PROVIDERS_FILE)
    now = [1_000_000.0]
    slept: list[float] = []

    async def sleep(s):
        slept.append(s)
        now[0] += s

    ledger = QuotaLedger(cat, clock=lambda: now[0], sleep=sleep, jitter_s=0)
    for canonical in ("gpt-oss-120b", "deepseek-v4.1-flash"):
        bucket = cat.bucket("openrouter", canonical)
        assert [await ledger.acquire(bucket) for _ in range(25)] == [0.0] * 25
    assert slept == []
    free = cat.bucket("openrouter", "nemotron-3-ultra:free")
    assert [await ledger.acquire(free) for _ in range(20)] == [0.0] * 20
    assert await ledger.acquire(free) > 0 and slept


def test_the_models_without_caps_have_their_real_context():
    """llama-3.3-70b, gpt-oss-120b and qwen3-32b list 131K on openrouter.ai (checked 2026-11-11); without caps they
    defaulted to 32,768 and the harness refused prompts a third of their real size."""
    cat = load_provider_catalog(settings.PROVIDERS_FILE)
    for canonical in ("llama-3.3-70b", "gpt-oss-120b", "qwen3-32b"):
        assert cat.served("openrouter", canonical).caps.context_len == 131_072
