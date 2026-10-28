"""QuotaLedger (B6): live request and token use per provider, per minute and per day, shared by every worker.

Before a model call, a worker `acquire()`s a request slot: if the provider's per-minute request or token
window is full it waits for the window to roll; if a per-day limit is spent it fails fast with
QUOTA_EXHAUSTED (the run ends INCOMPLETE; waiting a day is never the right answer inside a run).
After the call, `record_tokens()` adds what the call actually used.

Windows: per-minute windows start at first use; per-day windows align to UTC midnight (how providers typically
reset, and stable across restarts). A provider that resets differently is still caught: its own daily 429
marks it spent (`mark_exhausted`).

Persistence: with `state_path`, usage is saved after every change (atomic write) and loaded on start, so a
campaign resumed after a restart knows what today has already spent. One process at a time; sharing across
machines (Redis) is the next step once campaigns run on more than one host.
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path

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
    jitter_s: float = 1.0          # spread wake-ups so paced workers don't all burst at the window edge
    _minute: dict[str, _Window] = field(default_factory=dict)
    _day: dict[str, _Window] = field(default_factory=dict)
    _totals: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    waits: dict[str, float] = field(default_factory=lambda: defaultdict(float))   # seconds spent pacing
    _spent_until: dict[str, float] = field(default_factory=dict)                   # provider said: out for today
    state_path: Path | None = None                                                  # persist usage across restarts

    def __post_init__(self) -> None:
        if self.state_path is not None and Path(self.state_path).is_file():
            self._load()

    def _roll(self, store: dict[str, _Window], provider: str, length: float) -> _Window:
        now = self.clock()
        start = now - (now % DAY) if length == DAY else now          # day windows: UTC midnight
        w = store.get(provider)
        if w is None or (length == DAY and w.start != start) or (length != DAY and now - w.start >= length):
            w = _Window(start=start)
            store[provider] = w
        return w

    # ---- persistence -------------------------------------------------------------------------------------
    def _load(self) -> None:
        try:
            data = json.loads(Path(self.state_path).read_text())
        except (OSError, ValueError):
            return                                                    # unreadable: start clean, never crash
        for provider, st in data.get("providers", {}).items():
            if "day" in st:
                self._day[provider] = _Window(**st["day"])
            if "minute" in st:
                self._minute[provider] = _Window(**st["minute"])
            if st.get("spent_until"):
                self._spent_until[provider] = float(st["spent_until"])

    def save(self) -> None:
        if self.state_path is None:
            return
        providers = set(self._day) | set(self._minute) | set(self._spent_until)
        data = {"version": 1, "providers": {p: {
            **({"day": asdict(self._day[p])} if p in self._day else {}),
            **({"minute": asdict(self._minute[p])} if p in self._minute else {}),
            **({"spent_until": self._spent_until[p]} if p in self._spent_until else {}),
        } for p in sorted(providers)}}
        path = Path(self.state_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=1))
        os.replace(tmp, path)                                         # atomic: a crash never leaves half a file

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

    async def acquire(self, provider: str, tokens: int = 0) -> float:
        """Reserve one request slot, waiting for the minute window if needed. No await between check and charge,
        so concurrent workers on one event loop can never over-book a window. `tokens` is an estimate reserved in
        the token windows up front (corrected by record_tokens), so a burst of workers can't overshoot tpm.
        Returns the seconds this caller waited (the shared `waits` total mixes all workers)."""
        waited = 0.0
        while True:
            if self.exhausted(provider):
                raise HarnessError(ErrorKind.QUOTA_EXHAUSTED, f"{provider}: daily quota spent")
            left = self.remaining(provider)
            tpm_short = left["tpm"] is not None and left["tpm"] < min(tokens, self._tpm(provider))
            minute_full = (left["rpm"] is not None and left["rpm"] <= 0) or tpm_short or \
                (left["tpm"] is not None and left["tpm"] <= 0)
            if not minute_full:
                for w in (self._minute[provider], self._day[provider]):
                    w.requests += 1
                    w.tokens += tokens
                self._totals[provider] += 1
                self.save()
                return waited
            wait = max(0.05, self._minute[provider].start + MINUTE - self.clock())
            if self.jitter_s:
                wait += random.uniform(0, self.jitter_s)
            self.waits[provider] += wait
            waited += wait
            await self.sleep(wait)

    def mark_exhausted(self, provider: str) -> None:
        """The provider itself said today's quota is spent: stop every worker calling it until the day rolls."""
        lim = self.catalog.get(provider).limits
        d = self._roll(self._day, provider, DAY)
        d.requests = max(d.requests, lim.rpd if lim.rpd is not None else d.requests)
        self._spent_until[provider] = d.start + DAY                   # next UTC midnight
        self.save()

    def record_tokens(self, provider: str, tokens: int, reserved: int = 0) -> None:
        """Book actual usage, releasing what acquire() reserved for this call."""
        for store, length in ((self._minute, MINUTE), (self._day, DAY)):
            w = self._roll(store, provider, length)
            w.tokens = max(0, w.tokens + tokens - reserved)
        self.save()

    def _tpm(self, provider: str) -> int:
        lim = self.catalog.get(provider).limits.tpm
        return lim if lim is not None else 1 << 62

    def charge(self, provider: str, requests: int = 1, tokens: int = 0) -> None:
        """Synchronous bookkeeping (no pacing), for callers outside the gateway."""
        for store, length in ((self._minute, MINUTE), (self._day, DAY)):
            w = self._roll(store, provider, length)
            w.requests += requests
            w.tokens += tokens
        self._totals[provider] += requests
        self.save()
