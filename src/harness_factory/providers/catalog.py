"""Provider catalog (B6). The canonical model is the experiment variable; the provider is infrastructure."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class RateLimits(BaseModel):
    rpm: int | None = None
    rpd: int | None = None
    tpm: int | None = None
    tpd: int | None = None
    # account / key: one bucket for the whole provider. model: each model gets these limits on its own (Groq's
    # free tier works this way), so one model running out never stops another.
    scope: Literal["account", "key", "model"] = "account"


Effort = Literal["minimal", "low", "medium", "high"]


class ReasoningCaps(BaseModel):
    """How a model reasons: never, when asked (optional) or always; and the effort levels it accepts."""
    model_config = ConfigDict(extra="forbid")
    mode: Literal["none", "optional", "always"]
    efforts: list[Effort] = Field(default_factory=list)

    @model_validator(mode="after")
    def _efforts(self) -> ReasoningCaps:
        if self.mode == "none" and self.efforts:
            raise ValueError("reasoning mode 'none' takes no efforts")
        if len(set(self.efforts)) != len(self.efforts):
            raise ValueError(f"reasoning efforts listed twice: {self.efforts}")
        return self


class EndpointPrefs(BaseModel):
    """Which upstream endpoint serves a model on a router (OpenRouter's provider preferences). Recorded here;
    sent with each call by #124 (A5)."""
    model_config = ConfigDict(extra="forbid")
    order: list[str] = Field(default_factory=list)            # upstream provider slugs, most preferred first
    quantizations: list[str] = Field(default_factory=list)    # allowed, e.g. ["fp8", "bf16"]; empty = any
    require_parameters: bool = False                          # only endpoints that support every request parameter
    allow_fallbacks: bool = True                              # others when the ordered ones are down

    @field_validator("order", "quantizations")
    @classmethod
    def _unique(cls, v: list[str]) -> list[str]:
        if len(set(v)) != len(v) or any(not x.strip() for x in v):
            raise ValueError(f"{v}: entries must be non-empty and listed once")
        return v


class ModelCaps(BaseModel):
    """What a model can do. The optional fields are unknown when absent (today's behaviour: nothing is checked
    against them); A3's recipe x model check (#122) uses them when they are set."""
    model_config = ConfigDict(extra="forbid")
    tool_calling: Literal["native", "emulated", "none"] = "native"
    structured_output: bool = True
    context_len: int = Field(32_768, gt=0)
    max_output_tokens: int | None = Field(None, gt=0)         # the most the model returns in one reply
    reasoning: ReasoningCaps | None = None
    supported_params: list[str] | None = None                 # request parameters, as the provider lists them
    input_modalities: list[Literal["text", "image", "audio", "video", "file"]] | None = None

    @model_validator(mode="after")
    def _fits(self) -> ModelCaps:
        if self.max_output_tokens is not None and self.max_output_tokens > self.context_len:
            raise ValueError(f"max_output_tokens {self.max_output_tokens} exceeds context_len {self.context_len}")
        if self.input_modalities is not None and "text" not in self.input_modalities:
            raise ValueError("input_modalities must include text")
        return self


class ServedModel(BaseModel):
    canonical: str
    provider_model_id: str
    price_in: float = 0.0          # $ per million input tokens (reference price for free tiers)
    price_out: float = 0.0
    free: bool = False
    caps: ModelCaps = Field(default_factory=ModelCaps)
    limits: RateLimits | None = None   # this model's own limits, when they differ from the provider's
    endpoint: EndpointPrefs | None = None   # router endpoint preferences (OpenRouter); in env_hash when set (#124)

    def cost(self, tokens_in: int, tokens_out: int) -> float:
        return (tokens_in * self.price_in + tokens_out * self.price_out) / 1e6

    def env_model_id(self) -> str:
        """The model as a run's env_hash sees it: the provider model id, plus the pinned endpoint when one is set
        (#124: a pinned endpoint changes what serves the model, so its runs start new cells). Unpinned models keep
        their plain id, so every existing env hash stays."""
        if self.endpoint is None:
            return self.provider_model_id
        from harness_factory.hashing import content_hash
        return f"{self.provider_model_id}+endpoint:{content_hash(self.endpoint.model_dump(mode='json'), 12)}"

    def endpoint_request(self) -> dict | None:
        """OpenRouter's `provider` preferences for this model's calls, or None when nothing is pinned."""
        if self.endpoint is None:
            return None
        ep = self.endpoint
        return {**({"order": ep.order} if ep.order else {}),
                **({"quantizations": ep.quantizations} if ep.quantizations else {}),
                "require_parameters": ep.require_parameters, "allow_fallbacks": ep.allow_fallbacks}


class ProviderEntry(BaseModel):
    id: str
    kind: Literal["litellm", "scripted"] = "litellm"
    litellm_prefix: str | None = None      # e.g. "openrouter", "groq"
    base_url: str | None = None
    auth_env: str | None = None
    data_terms: str = "unknown"
    limits: RateLimits = Field(default_factory=RateLimits)
    models: list[ServedModel] = Field(default_factory=list)
    status: Literal["active", "paused"] = "active"


class ProviderCatalog(BaseModel):
    providers: list[ProviderEntry]

    def get(self, provider_id: str) -> ProviderEntry:
        for p in self.providers:
            if p.id == provider_id:
                return p
        raise KeyError(f"unknown provider {provider_id!r}")

    def served(self, provider_id: str, canonical: str) -> ServedModel:
        for m in self.get(provider_id).models:
            if m.canonical == canonical:
                return m
        raise KeyError(f"provider {provider_id!r} does not serve {canonical!r}")

    def bucket(self, provider_id: str, canonical: str | None = None) -> str:
        """The quota bucket a call is counted in: the provider, or provider|model when limits are per model."""
        p = self.get(provider_id)
        if canonical is not None and (p.limits.scope == "model" or self.served(provider_id, canonical).limits):
            return f"{provider_id}|{canonical}"
        return provider_id

    def limits(self, bucket: str) -> RateLimits:
        provider_id, _, canonical = bucket.partition("|")
        if canonical:
            return self.served(provider_id, canonical).limits or self.get(provider_id).limits
        return self.get(provider_id).limits

    def providers_for(self, canonical: str) -> list[str]:
        return [p.id for p in self.providers if p.status == "active" and any(m.canonical == canonical for m in p.models)]

    def canonical_models(self) -> list[str]:
        return sorted({m.canonical for p in self.providers for m in p.models})


def load_provider_catalog(path: Path) -> ProviderCatalog:
    return ProviderCatalog.model_validate(yaml.safe_load(path.read_text()))
