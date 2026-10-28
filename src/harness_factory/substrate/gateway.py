"""ModelGateway (B3): one interface to every catalogued provider.

The provider is fixed per run by the cell assignment (MOD-04); the gateway never swaps models.
Backends:
  LiteLLMBackend   real providers (OpenRouter, Groq, Cerebras, ...) through LiteLLM
  ScriptedBackend  replays a fixed script of turns; used for offline runs and tests
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Any, Protocol

import anyio
from pydantic import BaseModel

from harness_factory.observability.span import SpanKind
from harness_factory.observability.tracer import Tracer
from harness_factory.providers.catalog import ProviderCatalog, ProviderEntry, ServedModel
from harness_factory.providers.quota import QuotaLedger
from harness_factory.recipes.model import ModelSlot
from harness_factory.substrate.errors import RETRYABLE_KINDS, ErrorKind, HarnessError


class Completion(BaseModel):
    message: dict[str, Any]       # OpenAI-style assistant message (content, tool_calls)
    tokens_in: int = 0
    tokens_out: int = 0
    usd: float = 0.0


class Backend(Protocol):
    async def complete(self, messages: list[dict], tools: list[dict], slot: ModelSlot,
                       provider: ProviderEntry, served: ServedModel) -> Completion: ...


_DAILY = re.compile(r"per[ -_]?day|\b[RT]PD\b|daily", re.IGNORECASE)
DAILY_RETRY_AFTER_S = 120.0       # a provider asking us to wait longer than this is out of quota, not throttling


def retry_after_s(e: BaseException) -> float | None:
    """Seconds the provider asked us to wait (Retry-After header), when it said."""
    headers = dict(getattr(e, "headers", None) or {})
    response = getattr(e, "response", None)
    if response is not None and getattr(response, "headers", None):
        headers = {**dict(response.headers), **headers}
    for k, v in headers.items():
        if k.lower() == "retry-after":
            try:
                return max(0.0, float(v))
            except (TypeError, ValueError):
                return None
    return None


def classify_provider_error(e: BaseException) -> HarnessError:
    """Map any provider/LiteLLM exception to a typed HarnessError. Order matters: subclasses first."""
    import litellm

    msg = str(e)[:500]
    if isinstance(e, litellm.ContextWindowExceededError):
        return HarnessError(ErrorKind.BUDGET, f"context window exceeded: {msg}")      # the harness overflowed it
    if isinstance(e, litellm.RateLimitError):
        wait = retry_after_s(e)
        if _DAILY.search(msg) or (wait is not None and wait > DAILY_RETRY_AFTER_S):
            return HarnessError(ErrorKind.QUOTA_EXHAUSTED, msg)
        err = HarnessError(ErrorKind.RATE_LIMITED, msg)
        err.retry_after = wait                                                         # type: ignore[attr-defined]
        return err
    if isinstance(e, (litellm.AuthenticationError, litellm.PermissionDeniedError, litellm.NotFoundError,
                      litellm.BadRequestError, litellm.UnprocessableEntityError)):
        return HarnessError(ErrorKind.CONFIG, msg)
    if isinstance(e, (litellm.Timeout, litellm.APIConnectionError, litellm.InternalServerError,
                      litellm.ServiceUnavailableError, litellm.BadGatewayError, litellm.APIError,
                      TimeoutError)):
        return HarnessError(ErrorKind.PROVIDER_ERROR, msg)
    if isinstance(e, HarnessError):
        return e
    return HarnessError(ErrorKind.PROVIDER_ERROR, f"{type(e).__name__}: {msg}")


def approx_tokens(obj: Any) -> int:
    return max(1, len(json.dumps(obj, default=str)) // 4)


class ScriptedBackend:
    """Turns are {"tool_calls": [{"name": ..., "arguments": {...}}]} or {"content": "..."}."""

    def __init__(self, turns: list[dict[str, Any]]) -> None:
        self.turns = list(turns)
        self.i = 0

    async def complete(self, messages, tools, slot, provider, served) -> Completion:
        turn = self.turns[self.i] if self.i < len(self.turns) else {"content": "done"}
        self.i += 1
        if "tool_calls" in turn:
            calls = [{"id": f"call_{self.i}_{k}", "type": "function",
                      "function": {"name": c["name"], "arguments": json.dumps(c["arguments"])}}
                     for k, c in enumerate(turn["tool_calls"])]
            msg = {"role": "assistant", "content": None, "tool_calls": calls}
        else:
            msg = {"role": "assistant", "content": turn["content"]}
        return Completion(message=msg, tokens_in=approx_tokens(messages), tokens_out=approx_tokens(msg))


class LiteLLMBackend:
    def __init__(self, timeout_s: float | None = None) -> None:
        self.timeout_s = timeout_s

    async def complete(self, messages, tools, slot, provider, served) -> Completion:
        import litellm

        timeout_s = self.timeout_s

        kwargs: dict[str, Any] = dict(
            model=f"{provider.litellm_prefix}/{served.provider_model_id}" if provider.litellm_prefix
            else served.provider_model_id,
            messages=messages, temperature=slot.temperature, max_tokens=slot.max_output_tokens, num_retries=0,
        )
        if tools:
            kwargs["tools"] = tools
        if provider.base_url:
            kwargs["api_base"] = provider.base_url
        if provider.auth_env:
            key = os.environ.get(provider.auth_env)
            if not key:
                raise HarnessError(ErrorKind.PROVISION, f"{provider.auth_env} is not set for provider {provider.id}")
            kwargs["api_key"] = key
        if slot.seed is not None:
            kwargs["seed"] = slot.seed
        kwargs["drop_params"] = True               # a provider that rejects `seed` etc. drops it rather than 400s
        if timeout_s:
            kwargs["timeout"] = timeout_s
        try:
            resp = await litellm.acompletion(**kwargs)
        except Exception as e:  # every provider failure is classified; none escapes untyped
            raise classify_provider_error(e) from e
        choice = resp.choices[0].message
        msg: dict[str, Any] = {"role": "assistant", "content": choice.content}
        if getattr(choice, "tool_calls", None):
            msg["tool_calls"] = [{"id": tc.id, "type": "function",
                                  "function": {"name": tc.function.name, "arguments": tc.function.arguments or "{}"}}
                                 for tc in choice.tool_calls]
        usage = getattr(resp, "usage", None)
        tin = getattr(usage, "prompt_tokens", 0) or 0
        tout = getattr(usage, "completion_tokens", 0) or 0
        return Completion(message=msg, tokens_in=tin, tokens_out=tout, usd=served.cost(tin, tout))


MAX_BACKOFF_S = 60.0


class ModelGateway:
    _sleep = staticmethod(asyncio.sleep)        # retry backoff; replaced in tests

    def __init__(self, catalog: ProviderCatalog, ledger: QuotaLedger, tracer: Tracer,
                 provider_id: str, backend: Backend | None = None, retries: int = 2,
                 backoff_s: float = 2.0, timeout_s: float | None = None) -> None:
        self.catalog = catalog
        self.ledger = ledger
        self.tracer = tracer
        self.provider = catalog.get(provider_id)
        self.timeout_s = timeout_s
        self.backend = backend or (LiteLLMBackend(timeout_s) if self.provider.kind == "litellm" else None)
        if self.backend is None:
            raise HarnessError(ErrorKind.PROVISION, f"provider {provider_id} needs an explicit backend")
        self.retries = retries
        self.backoff_s = backoff_s
        self.infra_wait_s = 0.0          # quota waits + retry backoff: excluded from the run's wall budget

    async def complete(self, slot: ModelSlot, messages: list[dict], tools: list[dict]) -> Completion:
        """One model call: take a quota slot, call with a hard timeout, classify failures, retry the transient
        ones (honouring Retry-After). Spans of failed attempts stay in the trace with status error."""
        if not slot.canonical_model:
            raise HarnessError(ErrorKind.PROVISION, f"model slot {slot.role} has no canonical model")
        served = self.catalog.served(self.provider.id, slot.canonical_model)
        attempt = 0
        while True:
            waited = await self.ledger.acquire(self.provider.id)  # paces to the provider's limits; may raise
            self.infra_wait_s += waited
            with self.tracer.span(SpanKind.model_call, slot.canonical_model, provider=self.provider.id,
                                  provider_model_id=served.provider_model_id, attempt=attempt) as span:
                if waited:
                    span.attributes["quota_wait_s"] = round(waited, 3)
                try:
                    if self.timeout_s:
                        # Backstop above the provider timeout: a hung backend can never hold a worker forever.
                        with anyio.fail_after(self.timeout_s + min(5.0, self.timeout_s)):
                            comp = await self.backend.complete(messages, tools, slot, self.provider, served)
                    else:
                        comp = await self.backend.complete(messages, tools, slot, self.provider, served)
                except Exception as raw:  # classified below; nothing escapes untyped
                    e = raw if isinstance(raw, HarnessError) else classify_provider_error(raw)
                    span.status, span.error = "error", e.kind.value
                    span.attributes["error_detail"] = e.message[:300]
                    if e.kind == ErrorKind.QUOTA_EXHAUSTED:
                        self.ledger.mark_exhausted(self.provider.id)  # other workers stop calling this provider
                    if e.kind not in RETRYABLE_KINDS or attempt >= self.retries:
                        if e is raw:
                            raise
                        raise e from raw
                    wait = getattr(e, "retry_after", None)
                    wait = min(MAX_BACKOFF_S, max(wait or 0.0, self.backoff_s * (2 ** attempt)))
                    span.attributes["retry_in_s"] = round(wait, 3)
                else:
                    span.tokens_in, span.tokens_out = comp.tokens_in, comp.tokens_out
                    span.attributes["usd"] = comp.usd
                    span.attributes["tool_calls"] = len(comp.message.get("tool_calls") or [])
                    if comp.message.get("content"):
                        span.attributes["content_preview"] = str(comp.message["content"])[:500]
                    self.ledger.record_tokens(self.provider.id, comp.tokens_in + comp.tokens_out)
                    return comp
            attempt += 1
            self.infra_wait_s += wait
            await self._sleep(wait)
