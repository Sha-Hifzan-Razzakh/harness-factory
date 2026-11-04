"""ModelGateway (B3): one interface to every catalogued provider.

The provider is fixed per run by the cell assignment (MOD-04); the gateway never swaps models.
Backends:
  LiteLLMBackend   real providers (OpenRouter, Groq, Cerebras, ...) through a LiteLLM Router
  ScriptedBackend  replays a fixed script of turns; used for offline runs and tests

Who decides what (MOD-10, FWK-01): ModelGateway owns pacing (QuotaLedger slots and token reservations), retries
(config.retries, backoff, Retry-After) and error classification (`classify_provider_error`). The LiteLLM Router only
routes: it holds one deployment per (provider, served model) with the catalogued rpm/tpm, and is configured never to
retry, fall back, cool a deployment down or refuse a call locally, so no attempt or limit is counted twice.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
import weakref
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


_MALFORMED = re.compile(r"tool_use_failed|failed to call a function|tool call validation|invalid tool call",
                        re.IGNORECASE)
_TOO_LARGE = re.compile(r"request too large|request_too_large|maximum context|context length|too many tokens",
                        re.IGNORECASE)


def classify_provider_error(e: BaseException, mid_run: bool = False) -> HarnessError:
    """Map any provider/LiteLLM exception to a typed HarnessError. Order matters: subclasses first.

    A 400/422 is the model's failure (MODEL_PARSE, scored) only when the provider says the model's output was
    malformed (e.g. Groq's tool_use_failed). Any other 400 stays CONFIG (ERROR, unscored), even mid-run, so a
    factory message-format bug surfaces instead of being blamed on the model; `mid_run` is recorded in the
    message to help tell the two apart. Oversized requests (413, "request too large") are context limits (BUDGET).
    """
    import litellm

    msg = str(e)[:500] or f"{type(e).__name__} (no message)"
    if isinstance(e, litellm.ContextWindowExceededError):
        return HarnessError(ErrorKind.BUDGET, f"context window exceeded: {msg}")      # the harness overflowed it
    if isinstance(e, litellm.RateLimitError):
        wait = retry_after_s(e)
        if _DAILY.search(msg) or (wait is not None and wait > DAILY_RETRY_AFTER_S):
            return HarnessError(ErrorKind.QUOTA_EXHAUSTED, msg)
        err = HarnessError(ErrorKind.RATE_LIMITED, msg)
        err.retry_after = wait                                                         # type: ignore[attr-defined]
        return err
    if isinstance(e, (litellm.AuthenticationError, litellm.PermissionDeniedError, litellm.NotFoundError)):
        return HarnessError(ErrorKind.CONFIG, msg)
    if getattr(e, "status_code", None) == 413 or (
            isinstance(e, (litellm.BadRequestError, litellm.UnprocessableEntityError)) and _TOO_LARGE.search(msg)):
        return HarnessError(ErrorKind.BUDGET, f"request too large: {msg}")    # context/plan limit, the run's size
    if isinstance(e, (litellm.BadRequestError, litellm.UnprocessableEntityError)):
        if _MALFORMED.search(msg):
            return HarnessError(ErrorKind.MODEL_PARSE, msg)                  # the model broke the request
        return HarnessError(ErrorKind.CONFIG, f"{'mid-run ' if mid_run else ''}{msg}")
    if isinstance(e, (litellm.Timeout, TimeoutError)):
        err = HarnessError(ErrorKind.PROVIDER_ERROR, f"timeout: {msg}")
        err.timeout = True                                                   # type: ignore[attr-defined]
        return err
    if isinstance(e, (litellm.APIConnectionError, litellm.InternalServerError, litellm.ServiceUnavailableError,
                      litellm.BadGatewayError, litellm.APIError)):
        return HarnessError(ErrorKind.PROVIDER_ERROR, msg)
    if isinstance(e, HarnessError):
        return e
    return HarnessError(ErrorKind.PROVIDER_ERROR, f"{type(e).__name__}: {msg}")


def approx_tokens(obj: Any) -> int:
    return max(1, len(json.dumps(obj, default=str)) // 4)


class ScriptedBackend:
    """Turns are {"tool_calls": [{"name": ..., "arguments": {...}}]}, {"content": "..."}, {"reject": "..."}: the
    provider refuses the reply as malformed (MODEL_PARSE), as Groq does with tool_use_failed, or {"fail": "<ErrorKind>"}:
    the call fails as already classified (e.g. PROVIDER_ERROR, which the gateway retries, or CONFIG, which it doesn't)."""

    def __init__(self, turns: list[dict[str, Any]]) -> None:
        self.turns = list(turns)
        self.i = 0

    async def complete(self, messages, tools, slot, provider, served) -> Completion:
        if getattr(slot, "role", None) == "compactor":                  # ADR 0015: answered outside the script
            from harness_factory.substrate.context import scripted_summary
            msg = {"role": "assistant", "content": scripted_summary(messages)}
            return Completion(message=msg, tokens_in=approx_tokens(messages), tokens_out=approx_tokens(msg))
        turn = self.turns[self.i] if self.i < len(self.turns) else {"content": "done"}
        self.i += 1
        if "reject" in turn:
            raise HarnessError(ErrorKind.MODEL_PARSE, turn["reject"])
        if "fail" in turn:
            raise HarnessError(ErrorKind(turn["fail"]), f"scripted {turn['fail']}")
        if "tool_calls" in turn:
            calls = [{"id": f"call_{self.i}_{k}", "type": "function",
                      "function": {"name": c["name"], "arguments": json.dumps(c["arguments"])}}
                     for k, c in enumerate(turn["tool_calls"])]
            msg = {"role": "assistant", "content": None, "tool_calls": calls}
        else:
            msg = {"role": "assistant", "content": turn["content"]}
        return Completion(message=msg, tokens_in=approx_tokens(messages), tokens_out=approx_tokens(msg))


# The Router's in-flight cap per deployment defaults to its rpm, and a call over it is refused locally as a 429 that
# never reached the provider. Pacing is the QuotaLedger's job, so the cap is set out of reach.
ROUTER_INFLIGHT_CAP = 1_000_000


def litellm_model(provider: ProviderEntry, served: ServedModel) -> str:
    return f"{provider.litellm_prefix}/{served.provider_model_id}" if provider.litellm_prefix \
        else served.provider_model_id


def deployment_id(provider_id: str, canonical: str) -> str:
    """The Router deployment the gateway pins a call to: the cell's provider is fixed (MOD-04), never load-balanced."""
    return f"{provider_id}|{canonical}"


def router_deployments(catalog: ProviderCatalog, keys: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """One Router deployment per (provider, served model). The model group is the canonical model, so a model served
    by two providers is one group with a deployment per provider. rpm/tpm are the limits of the call's quota bucket
    (the model's own, else the provider's); with scope account/key the Router sees them per deployment, which is why
    the ledger, not the Router, paces. `keys` ({provider id: API key}) go on that provider's deployments only: a key
    passed per request instead makes the Router derive a throwaway deployment (and register its pricing) every call."""
    keys = keys or {}
    out: list[dict[str, Any]] = []
    for p in catalog.providers:
        if p.kind != "litellm":
            continue
        for m in p.models:
            lim = m.limits or p.limits
            params: dict[str, Any] = {"model": litellm_model(p, m), "max_parallel_requests": ROUTER_INFLIGHT_CAP}
            if p.base_url:
                params["api_base"] = p.base_url
            if p.id in keys:
                params["api_key"] = keys[p.id]
            if lim.rpm is not None:
                params["rpm"] = lim.rpm
            if lim.tpm is not None:
                params["tpm"] = lim.tpm
            out.append({"model_name": m.canonical, "litellm_params": params,
                        "model_info": {"id": deployment_id(p.id, m.canonical)}})
    return out


def build_router(catalog: ProviderCatalog, keys: dict[str, str] | None = None) -> Any:
    """A Router that routes and nothing else: retries, fallbacks, cooldowns and local admission are all off, because
    the gateway already retries (one place), the ledger paces, and a cooled-down deployment would turn the gateway's
    own retry into a local "no deployments" error that never reached the provider."""
    import litellm

    # Keep provider error messages as the provider sent them: the Router otherwise appends its fallback notes,
    # which would end up in traces and in the text classify_provider_error reads. A process-wide LiteLLM setting,
    # always set to the same value (LiteLLM plans to make it the default).
    litellm.expose_router_debug_in_errors = False
    return litellm.Router(model_list=router_deployments(catalog, keys), routing_strategy="simple-shuffle",
                          num_retries=0, max_fallbacks=0, retry_after=0, disable_cooldowns=True,
                          enable_pre_call_checks=False)


class LiteLLMBackend:
    accepts_response_format = True       # ModelGateway passes JSON mode only to backends that say so

    def __init__(self, timeout_s: float | None = None, catalog: ProviderCatalog | None = None) -> None:
        self.timeout_s = timeout_s
        self.catalog = catalog
        self._router: Any = None

    @staticmethod
    def api_key(provider: ProviderEntry) -> str | None:
        if not provider.auth_env:
            return None
        key = os.environ.get(provider.auth_env)
        if not key:
            raise HarnessError(ErrorKind.PROVISION, f"{provider.auth_env} is not set for provider {provider.id}")
        return key

    def router_for(self, provider: ProviderEntry) -> Any:
        """This backend's Router, built on first use (no await in between, so concurrent calls share one), holding the
        provider's key. It lives as long as the backend, i.e. one run: no Router state is shared between runs. LiteLLM
        registers every Router's usage callbacks globally, so they are removed when the backend is collected."""
        if self._router is None:
            key = self.api_key(provider)
            catalog = self.catalog if self.catalog is not None else ProviderCatalog(providers=[provider])
            try:
                self._router = build_router(catalog, {provider.id: key} if key else None)
            except Exception as e:                  # our own setup, not a provider failure: never retried
                raise HarnessError(ErrorKind.PROVISION, f"LiteLLM Router for {provider.id}: {e}") from e
            weakref.finalize(self, self._router.discard)
        return self._router

    async def complete(self, messages, tools, slot, provider, served,
                       response_format: dict[str, Any] | None = None) -> Completion:
        timeout_s = self.timeout_s

        kwargs: dict[str, Any] = dict(
            model=deployment_id(provider.id, served.canonical),
            messages=messages, temperature=slot.temperature, max_tokens=slot.max_output_tokens, num_retries=0,
        )
        if tools:
            kwargs["tools"] = tools
        if response_format:
            kwargs["response_format"] = response_format
        self.api_key(provider)                      # checked every call, as before the Router: unset is PROVISION
        if slot.seed is not None:
            kwargs["seed"] = slot.seed
        kwargs["drop_params"] = True               # a provider that rejects `seed` etc. drops it rather than 400s
        if timeout_s:
            kwargs["timeout"] = timeout_s
        router = self.router_for(provider)
        # Provider exceptions propagate raw: ModelGateway classifies them, because only it knows whether the run
        # already had a successful call (which decides whether a 400 is setup or the model's doing).
        resp = await router.acompletion(**kwargs)
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
        self.backend = backend or (LiteLLMBackend(timeout_s, catalog) if self.provider.kind == "litellm" else None)
        if self.backend is None:
            raise HarnessError(ErrorKind.PROVISION, f"provider {provider_id} needs an explicit backend")
        self.retries = retries
        self.backoff_s = backoff_s
        self.infra_wait_s = 0.0          # quota waits, retry backoff, failed attempts: not the harness's time
        self.successful_calls = 0

    async def complete(self, slot: ModelSlot, messages: list[dict], tools: list[dict],
                       attributes: dict[str, Any] | None = None,
                       response_format: dict[str, Any] | None = None) -> Completion:
        """One model call: take a quota slot, call with a hard timeout, classify failures, retry the transient
        ones (honouring Retry-After). Spans of failed attempts stay in the trace with status error.
        attributes: extra attributes for the model_call span (the caller's token estimate, ADR 0015).

        response_format (e.g. {"type": "json_object"}) asks for JSON mode. It is sent only when the model's caps
        declare structured output and the backend takes it; otherwise the prompt alone asks for JSON (scripted
        backends read the prompt). The span records which (`response_format`: the type, or "prompt_only")."""
        if not slot.canonical_model:
            raise HarnessError(ErrorKind.PROVISION, f"model slot {slot.role} has no canonical model")
        served = self.catalog.served(self.provider.id, slot.canonical_model)
        extra: dict[str, Any] = {}
        if response_format and served.caps.structured_output and getattr(self.backend, "accepts_response_format", False):
            extra["response_format"] = response_format
        bucket = self.catalog.bucket(self.provider.id, slot.canonical_model)   # provider, or provider|model
        attempt = 0
        while True:
            reserve = approx_tokens(messages) + slot.max_output_tokens // 4   # rough: prompt + a typical reply
            waited = await self.ledger.acquire(bucket, reserve)    # paces to the bucket's limits; may raise
            self.infra_wait_s += waited
            with self.tracer.span(SpanKind.model_call, slot.canonical_model, provider=self.provider.id,
                                  provider_model_id=served.provider_model_id, attempt=attempt,
                                  **(attributes or {})) as span:
                if waited:
                    span.attributes["quota_wait_s"] = round(waited, 3)
                if response_format:
                    span.attributes["response_format"] = response_format.get("type") if extra else "prompt_only"
                t_call = time.monotonic()
                try:
                    if self.timeout_s:
                        # Backstop above the provider timeout: a hung backend can never hold a worker forever.
                        with anyio.fail_after(self.timeout_s + min(5.0, self.timeout_s)):
                            comp = await self.backend.complete(messages, tools, slot, self.provider, served, **extra)
                    else:
                        comp = await self.backend.complete(messages, tools, slot, self.provider, served, **extra)
                except asyncio.CancelledError:
                    # Ctrl-C or a cancelled campaign: the call never finished, so give back the tokens it reserved
                    # (otherwise the persisted ledger over-counts tpm/tpd until the window rolls). Not a provider
                    # error, so nothing to classify; the cancellation goes on.
                    span.status, span.error = "error", "cancelled"
                    self.ledger.record_tokens(bucket, 0, reserve)
                    raise
                except Exception as raw:  # classified below; nothing escapes untyped
                    e = raw if isinstance(raw, HarnessError) else classify_provider_error(
                        raw, mid_run=self.successful_calls > 0)
                    span.status, span.error = "error", e.kind.value
                    self.infra_wait_s += time.monotonic() - t_call             # a failed attempt is not a step
                    self.ledger.record_tokens(bucket, 0, reserve)               # release the reservation
                    span.attributes["error_detail"] = e.message[:300]
                    if e.kind == ErrorKind.QUOTA_EXHAUSTED:
                        self.ledger.mark_exhausted(bucket)       # other workers stop calling this bucket
                    limit = min(self.retries, 1) if getattr(e, "timeout", False) else self.retries
                    if e.kind not in RETRYABLE_KINDS or attempt >= limit:
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
                    self.ledger.record_tokens(bucket, comp.tokens_in + comp.tokens_out, reserve)
                    self.successful_calls += 1
                    return comp
            attempt += 1
            self.infra_wait_s += wait
            await self._sleep(wait)
