"""QuotaLedger (B6): live request and token use per bucket, per minute and per day, shared by every worker.

A bucket is what a provider's limits apply to: the whole provider (scope account/key), or one model on it
(scope model, e.g. Groq's free tier), named "provider|model". `ProviderCatalog.bucket()` picks it; every method
here that says `provider` takes a bucket.

Before a model call, a worker `acquire()`s a request slot: if the provider's per-minute request or token
window is full it waits for the window to roll; if a per-day limit is spent it fails fast with
QUOTA_EXHAUSTED (the run ends INCOMPLETE; waiting a day is never the right answer inside a run).
After the call, `record_tokens()` adds what the call actually used.

Windows: per-minute windows start at first use; per-day windows align to UTC midnight (how providers typically
reset, and stable across restarts). A provider that resets differently is still caught: its own daily 429
marks it spent (`mark_exhausted`).

Persistence: with `state_path`, usage is saved after every change (atomic write) and loaded on start, so a
campaign resumed after a restart knows what today has already spent. Several `hfx` processes on one machine can
share the file: every check-and-change re-reads it and writes it back under a lock (`state_path` + ".lock",
fcntl on POSIX, msvcrt on Windows), so concurrent commands add up instead of overwriting each other. Sharing
across machines (Redis) is the next step once campaigns run on more than one host.
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path

from harness_factory.providers.catalog import ProviderCatalog
from harness_factory.substrate.errors import ErrorKind, HarnessError

MINUTE = 60.0
DAY = 86_400.0
LOCK_TIMEOUT_S = 30.0          # the lock is held for a read and a write; waiting this long means something is wrong

if os.name == "nt":
    import msvcrt

    def _try_lock(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)          # raises OSError when another process holds it

    def _unlock(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def _try_lock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)   # raises OSError (BlockingIOError) when held

    def _unlock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)


@contextmanager
def file_lock(path: Path, timeout_s: float = LOCK_TIMEOUT_S) -> Iterator[None]:
    """An exclusive lock between processes on one machine. The OS releases it if the holder dies, so there is no
    stale lock to clean up. Held only for short synchronous sections, so a brief blocking wait is fine."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        deadline = time.monotonic() + timeout_s
        while True:
            try:
                _try_lock(fd)
                break
            except OSError:
                if time.monotonic() > deadline:
                    raise TimeoutError(f"quota ledger lock {path} held for more than {timeout_s:.0f}s") from None
                time.sleep(0.005)
        try:
            yield
        finally:
            _unlock(fd)
    finally:
        os.close(fd)


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

    @contextmanager
    def _synced(self) -> Iterator[None]:
        """A check-and-change against the shared file: lock, re-read what other processes booked, change, write
        back. Without a state file it is just the in-memory change."""
        if self.state_path is None:
            yield
            return
        path = Path(self.state_path)
        with file_lock(path.with_name(path.name + ".lock")):
            if path.is_file():
                self._load()
            yield
            self._write()

    def save(self) -> None:
        """Write the current usage (merged with what other processes booked since the last change)."""
        with self._synced():
            pass

    def _write(self) -> None:
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
        lim = self.catalog.limits(provider)
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
            with self._synced():
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
                    return waited
            wait = max(0.05, self._minute[provider].start + MINUTE - self.clock())
            if self.jitter_s:
                wait += random.uniform(0, self.jitter_s)
            self.waits[provider] += wait
            waited += wait
            await self.sleep(wait)

    def mark_exhausted(self, provider: str) -> None:
        """The provider itself said today's quota is spent: stop every worker calling it until the day rolls."""
        lim = self.catalog.limits(provider)
        with self._synced():
            d = self._roll(self._day, provider, DAY)
            d.requests = max(d.requests, lim.rpd if lim.rpd is not None else d.requests)
            self._spent_until[provider] = d.start + DAY               # next UTC midnight

    def record_tokens(self, provider: str, tokens: int, reserved: int = 0) -> None:
        """Book actual usage, releasing what acquire() reserved for this call."""
        with self._synced():
            for store, length in ((self._minute, MINUTE), (self._day, DAY)):
                w = self._roll(store, provider, length)
                w.tokens = max(0, w.tokens + tokens - reserved)

    def _tpm(self, provider: str) -> int:
        lim = self.catalog.limits(provider).tpm
        return lim if lim is not None else 1 << 62

    def charge(self, provider: str, requests: int = 1, tokens: int = 0) -> None:
        """Synchronous bookkeeping (no pacing), for callers outside the gateway."""
        with self._synced():
            for store, length in ((self._minute, MINUTE), (self._day, DAY)):
                w = self._roll(store, provider, length)
                w.requests += requests
                w.tokens += tokens
            self._totals[provider] += requests
