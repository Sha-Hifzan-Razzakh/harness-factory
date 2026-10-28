"""QuotaLedger (B6): live request and token use per provider, per minute and per day.

P0 keeps it in memory; P2 moves it to Redis so parallel workers share one ledger.
"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field

from harness_factory.providers.catalog import ProviderCatalog


@dataclass
class _Window:
    start: float
    requests: int = 0
    tokens: int = 0


@dataclass
class QuotaLedger:
    catalog: ProviderCatalog
    clock: callable = time.time
    _minute: dict[str, _Window] = field(default_factory=dict)
    _day: dict[str, _Window] = field(default_factory=dict)
    _totals: dict[str, int] = field(default_factory=lambda: defaultdict(int))

    def _roll(self, store: dict[str, _Window], provider: str, length: float) -> _Window:
        now = self.clock()
        w = store.get(provider)
        if w is None or now - w.start >= length:
            w = _Window(start=now)
            store[provider] = w
        return w

    def charge(self, provider: str, requests: int = 1, tokens: int = 0) -> None:
        for store, length in ((self._minute, 60.0), (self._day, 86_400.0)):
            w = self._roll(store, provider, length)
            w.requests += requests
            w.tokens += tokens
        self._totals[provider] += requests

    def remaining(self, provider: str) -> dict[str, int | None]:
        lim = self.catalog.get(provider).limits
        m = self._roll(self._minute, provider, 60.0)
        d = self._roll(self._day, provider, 86_400.0)
        return {
            "rpm": None if lim.rpm is None else lim.rpm - m.requests,
            "rpd": None if lim.rpd is None else lim.rpd - d.requests,
            "tpm": None if lim.tpm is None else lim.tpm - m.tokens,
            "tpd": None if lim.tpd is None else lim.tpd - d.tokens,
        }

    def exhausted(self, provider: str) -> bool:
        return any(v is not None and v <= 0 for v in self.remaining(provider).values())
