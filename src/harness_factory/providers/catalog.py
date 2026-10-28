"""Provider catalog (B6). The canonical model is the experiment variable; the provider is infrastructure."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field


class RateLimits(BaseModel):
    rpm: int | None = None
    rpd: int | None = None
    tpm: int | None = None
    tpd: int | None = None
    scope: Literal["account", "key"] = "account"


class ModelCaps(BaseModel):
    tool_calling: Literal["native", "emulated", "none"] = "native"
    structured_output: bool = True
    context_len: int = 32_768


class ServedModel(BaseModel):
    canonical: str
    provider_model_id: str
    price_in: float = 0.0          # $ per million input tokens (reference price for free tiers)
    price_out: float = 0.0
    free: bool = False
    caps: ModelCaps = Field(default_factory=ModelCaps)

    def cost(self, tokens_in: int, tokens_out: int) -> float:
        return (tokens_in * self.price_in + tokens_out * self.price_out) / 1e6


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

    def providers_for(self, canonical: str) -> list[str]:
        return [p.id for p in self.providers if p.status == "active" and any(m.canonical == canonical for m in p.models)]

    def canonical_models(self) -> list[str]:
        return sorted({m.canonical for p in self.providers for m in p.models})


def load_provider_catalog(path: Path) -> ProviderCatalog:
    return ProviderCatalog.model_validate(yaml.safe_load(path.read_text()))
