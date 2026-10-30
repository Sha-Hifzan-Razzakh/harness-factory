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
from typing import Any, Protocol

from pydantic import BaseModel

from harness_factory.observability.span import SpanKind
from harness_factory.observability.tracer import Tracer
from harness_factory.providers.catalog import ProviderCatalog, ProviderEntry, ServedModel
from harness_factory.providers.quota import QuotaLedger
from harness_factory.recipes.model import ModelSlot
from harness_factory.substrate.errors import ErrorKind, HarnessError


class Completion(BaseModel):
    message: dict[str, Any]       # OpenAI-style assistant message (content, tool_calls)
    tokens_in: int = 0
    tokens_out: int = 0
    usd: float = 0.0


class Backend(Protocol):
    async def complete(self, messages: list[dict], tools: list[dict], slot: ModelSlot,
                       provider: ProviderEntry, served: ServedModel) -> Completion: ...


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
    async def complete(self, messages, tools, slot, provider, served) -> Completion:
        import litellm

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
        try:
            resp = await litellm.acompletion(**kwargs)
        except litellm.RateLimitError as e:
            raise HarnessError(ErrorKind.RATE_LIMITED, str(e)[:300]) from e
        except (litellm.APIConnectionError, litellm.ServiceUnavailableError, litellm.Timeout) as e:
            raise HarnessError(ErrorKind.MODEL_ERROR, str(e)[:300]) from e
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


class ModelGateway:
    def __init__(self, catalog: ProviderCatalog, ledger: QuotaLedger, tracer: Tracer,
                 provider_id: str, backend: Backend | None = None, retries: int = 2,
                 backoff_s: float = 2.0) -> None:
        self.catalog = catalog
        self.ledger = ledger
        self.tracer = tracer
        self.provider = catalog.get(provider_id)
        self.backend = backend or (LiteLLMBackend() if self.provider.kind == "litellm" else None)
        if self.backend is None:
            raise HarnessError(ErrorKind.PROVISION, f"provider {provider_id} needs an explicit backend")
        self.retries = retries
        self.backoff_s = backoff_s

    async def complete(self, slot: ModelSlot, messages: list[dict], tools: list[dict]) -> Completion:
        if not slot.canonical_model:
            raise HarnessError(ErrorKind.PROVISION, f"model slot {slot.role} has no canonical model")
        served = self.catalog.served(self.provider.id, slot.canonical_model)
        if self.ledger.exhausted(self.provider.id):
            raise HarnessError(ErrorKind.QUOTA_EXHAUSTED, f"{self.provider.id} quota exhausted")
        attempt = 0
        while True:
            with self.tracer.span(SpanKind.model_call, slot.canonical_model, provider=self.provider.id,
                                  provider_model_id=served.provider_model_id, attempt=attempt) as span:
                try:
                    comp = await self.backend.complete(messages, tools, slot, self.provider, served)
                except HarnessError as e:
                    span.status, span.error = "error", e.kind.value
                    if e.kind == ErrorKind.RATE_LIMITED and attempt < self.retries:
                        retry = True
                    else:
                        raise
                else:
                    retry = False
                    span.tokens_in, span.tokens_out = comp.tokens_in, comp.tokens_out
                    span.attributes["usd"] = comp.usd
                    span.attributes["tool_calls"] = len(comp.message.get("tool_calls") or [])
                    self.ledger.charge(self.provider.id, 1, comp.tokens_in + comp.tokens_out)
                    return comp
            if retry:
                attempt += 1
                await asyncio.sleep(self.backoff_s * attempt)
