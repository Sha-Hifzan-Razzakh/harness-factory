"""QuotaLedger (B6): live request and token use per provider, per minute and per day, shared by every worker.

Before a model call, a worker `acquire()`s a request slot: if the provider's per-minute request or token
window is full it waits for the window to roll; if a per-day limit is spent it fails fast with
QUOTA_EXHAUSTED (the run ends INCOMPLETE; waiting a day is never the right answer inside a run).
After the call, `record_tokens()` adds what the call actually used.

Fixed windows, in-process: every async worker in one campaign shares one ledger. Sharing across machines
(Redis) is the next step once campaigns run on more than one host.
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from harness_factory.providers.catalog import ProviderCatalog
from harness_factory.substrate.errors import ErrorKind, HarnessError

MINUTE = 60.0
DAY = 86_400.0


@dataclass
class _Window:
    start: float
    requests: int = 0
    tokens: int = 0


@dataclass
class QuotaLedger:
    catalog: ProviderCatalog
    clock: Callable[[], float] = time.time
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    _minute: dict[str, _Window] = field(default_factory=dict)
    _day: dict[str, _Window] = field(default_factory=dict)
    _totals: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    waits: dict[str, float] = field(default_factory=lambda: defaultdict(float))   # seconds spent pacing
    _spent_until: dict[str, float] = field(default_factory=dict)                   # provider said: out for today

    def _roll(self, store: dict[str, _Window], provider: str, length: float) -> _Window:
        now = self.clock()
        w = store.get(provider)
        if w is None or now - w.start >= length:
            w = _Window(start=now)
            store[provider] = w
        return w

    def remaining(self, provider: str) -> dict[str, int | None]:
        lim = self.catalog.get(provider).limits
        m = self._roll(self._minute, provider, MINUTE)
        d = self._roll(self._day, provider, DAY)
        return {
            "rpm": None if lim.rpm is None else lim.rpm - m.requests,
            "rpd": None if lim.rpd is None else lim.rpd - d.requests,
            "tpm": None if lim.tpm is None else lim.tpm - m.tokens,
            "tpd": None if lim.tpd is None else lim.tpd - d.tokens,
        }

    def exhausted(self, provider: str) -> bool:
        """True when a per-day limit is spent (per-minute limits only make callers wait)."""
        if self.clock() < self._spent_until.get(provider, 0.0):
            return True
        left = self.remaining(provider)
        return any(left[k] is not None and left[k] <= 0 for k in ("rpd", "tpd"))

    async def acquire(self, provider: str) -> float:
        """Reserve one request slot, waiting for the minute window if needed. No await between check and charge,
        so concurrent workers on one event loop can never over-book a window. Returns the seconds this caller
        waited (the shared `waits` total mixes all workers, so it cannot be attributed to one run)."""
        waited = 0.0
        while True:
            if self.exhausted(provider):
                raise HarnessError(ErrorKind.QUOTA_EXHAUSTED, f"{provider}: daily quota spent")
            left = self.remaining(provider)
            minute_full = any(left[k] is not None and left[k] <= 0 for k in ("rpm", "tpm"))
            if not minute_full:
                self._minute[provider].requests += 1
                self._day[provider].requests += 1
                self._totals[provider] += 1
                return waited
            wait = max(0.05, self._minute[provider].start + MINUTE - self.clock())
            self.waits[provider] += wait
            waited += wait
            await self.sleep(wait)

    def mark_exhausted(self, provider: str) -> None:
        """The provider itself said today's quota is spent: stop every worker calling it until the day rolls."""
        lim = self.catalog.get(provider).limits
        d = self._roll(self._day, provider, DAY)
        d.requests = max(d.requests, lim.rpd if lim.rpd is not None else d.requests)
        self._spent_until[provider] = d.start + DAY

    def record_tokens(self, provider: str, tokens: int) -> None:
        for store, length in ((self._minute, MINUTE), (self._day, DAY)):
            self._roll(store, provider, length).tokens += tokens

    def charge(self, provider: str, requests: int = 1, tokens: int = 0) -> None:
        """Synchronous bookkeeping (no pacing), for callers outside the gateway."""
        for store, length in ((self._minute, MINUTE), (self._day, DAY)):
            w = self._roll(store, provider, length)
            w.requests += requests
            w.tokens += tokens
        self._totals[provider] += requests
