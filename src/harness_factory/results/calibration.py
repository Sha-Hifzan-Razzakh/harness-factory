"""Cross-provider equivalence calibration (P2).

The canonical model is the experiment variable and the provider is infrastructure, but two providers serving "the
same" model can differ: quantisation, context limits, sampling defaults, tool-call parsing. Until that is measured,
the provider is part of every cell (results.store.CellResult). Calibration measures it:

1. Pair runs that differ only in the provider: same task version and task hash, recipe hash, canonical model, seed
   and provider-neutral tool environment (`tools_hash`), clean (no fault profile), scored, each served by the model
   id the provider catalog names now.
2. Take the paired difference of E0 (stored `composite`, B minus A): mean and a bootstrap 95% interval
   (results.report.paired_delta, the same statistic the matrix report uses).
3. Verdict, in this order:
   - `inconclusive` with fewer than `min_pairs` pairs;
   - `equivalent` when the whole interval lies within ±margin;
   - `different` when the interval excludes 0;
   - `inconclusive` otherwise (more pairs would decide it).
4. Freeze the result (content-addressed, like a cost baseline) in the results store.

An `equivalent` verdict lets aggregation pool the two providers' runs into one cell (`pools`). It is a claim about
how the providers serve the model, so it is calibrated on one recipe and applied to all; it holds only while each
provider serves the model id it was calibrated with. Cost is never pooled: each run's C is against its own
provider's median before its cell is pooled. Pools are connected components of equivalent pairs, and a component
is pooled only when every pair inside it has its own `equivalent` record (no transitive guesses).
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

from harness_factory.hashing import content_hash
from harness_factory.providers.catalog import ProviderCatalog
from harness_factory.results.cost import is_clean
from harness_factory.results.report import paired_delta
from harness_factory.results.store import UNSCORED

Verdict = Literal["equivalent", "different", "inconclusive"]
DEFAULT_MARGIN = 0.1
DEFAULT_MIN_PAIRS = 20


class Equivalence(BaseModel):
    canonical_model: str
    providers: tuple[str, str]               # sorted; the delta is providers[1] minus providers[0]
    served: dict[str, str]                   # provider -> provider model id the pairs were served by
    recipe_hashes: list[str]                 # recipes the pairs came from
    metric: str = "composite"                # E0
    margin: float
    min_pairs: int
    n: int
    mean: float | None = None
    ci: tuple[float, float] | None = None
    wins: int = 0                            # pairs where providers[1] scored higher
    losses: int = 0
    ties: int = 0
    token_ratio: float | None = None         # median of tokens(B) / tokens(A) over pairs, for information
    verdict: Verdict
    reason: str
    note: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @property
    def id(self) -> str:
        return content_hash({k: v for k, v in self.model_dump(mode="json").items() if k not in ("created_at", "note")},
                            12)


def _pair_key(r: dict) -> tuple:
    parts = str(r["run_key"]).split("|")
    return (r["task_version"], parts[1], r["recipe_hash"], r["seed"], r.get("tools_hash"))


def pairs(rows: list[dict], canonical_model: str, providers: tuple[str, str], served: dict[str, str],
          recipe_hash: str | None = None) -> list[tuple[dict, dict]]:
    """(run on providers[0], run on providers[1]) for every key both have a usable run for."""
    a, b = providers
    usable: dict[str, dict[tuple, dict]] = {a: {}, b: {}}
    for r in rows:
        p = r.get("provider")
        if (p in usable and r["canonical_model"] == canonical_model and is_clean(r)
                and r["status"] not in UNSCORED and r.get("tools_hash") and r.get("served_id") == served[p]
                and (recipe_hash is None or r["recipe_hash"] == recipe_hash)):
            usable[p][_pair_key(r)] = r                      # rows come oldest first: the newest wins
    return [(usable[a][k], usable[b][k]) for k in sorted(usable[a].keys() & usable[b].keys(), key=str)]


def calibrate(rows: list[dict], providers_catalog: ProviderCatalog, canonical_model: str, providers: list[str],
              recipe_hash: str | None = None, margin: float = DEFAULT_MARGIN,
              min_pairs: int = DEFAULT_MIN_PAIRS, note: str = "") -> Equivalence:
    if len(set(providers)) != 2:
        raise ValueError("calibration compares exactly two different providers")
    a, b = sorted(providers)
    served = {p: providers_catalog.served(p, canonical_model).provider_model_id for p in (a, b)}
    ps = pairs(rows, canonical_model, (a, b), served, recipe_hash)
    d = paired_delta([(float(ra["composite"]), float(rb["composite"])) for ra, rb in ps])
    ratios = [(rb["tokens_in"] + rb["tokens_out"]) / (ra["tokens_in"] + ra["tokens_out"])
              for ra, rb in ps if ra["tokens_in"] + ra["tokens_out"] > 0]
    common = dict(canonical_model=canonical_model, providers=(a, b), served=served,
                  recipe_hashes=sorted({ra["recipe_hash"] for ra, _ in ps}), margin=margin, min_pairs=min_pairs,
                  n=len(ps), token_ratio=round(statistics.median(ratios), 4) if ratios else None, note=note)
    if len(ps) < min_pairs:
        return Equivalence(**common, verdict="inconclusive",
                           reason=f"{len(ps)} pairs; at least {min_pairs} are needed",
                           **({"mean": d["mean"], "ci": d["ci"], "wins": d["wins"], "losses": d["losses"],
                               "ties": d["ties"]} if ps else {}))
    lo, hi = d["ci"]
    if -margin <= lo and hi <= margin:
        verdict, reason = "equivalent", f"the 95% interval [{lo:+.3f}, {hi:+.3f}] is within ±{margin}"
    elif lo > 0 or hi < 0:
        verdict, reason = "different", f"the 95% interval [{lo:+.3f}, {hi:+.3f}] excludes 0"
    else:
        verdict, reason = "inconclusive", (f"the 95% interval [{lo:+.3f}, {hi:+.3f}] includes 0 but reaches past "
                                           f"±{margin}: more pairs would decide it")
    return Equivalence(**common, verdict=verdict, reason=reason, mean=d["mean"], ci=d["ci"], wins=d["wins"],
                       losses=d["losses"], ties=d["ties"])


def latest(records: list[dict]) -> dict[tuple[str, tuple[str, str]], Equivalence]:
    """The newest calibration per (model, provider pair)."""
    out: dict[tuple[str, tuple[str, str]], Equivalence] = {}
    for raw in records:                                      # oldest first
        eq = Equivalence.model_validate(raw)
        out[(eq.canonical_model, tuple(eq.providers))] = eq
    return out


def pools(records: list[dict], providers_catalog: ProviderCatalog) -> dict[tuple[str, str, str], str]:
    """(canonical model, provider, served id) -> pool label ("a+b"), for aggregate_rows.

    A pair counts only if its newest record is `equivalent` and both providers still serve the model id it was
    calibrated with. A connected component is pooled only when every pair inside it counts."""
    good: dict[str, set[tuple[str, str]]] = defaultdict(set)
    served_by: dict[tuple[str, str], str] = {}
    for (model, (a, b)), eq in latest(records).items():
        try:
            current = {p: providers_catalog.served(p, model).provider_model_id for p in (a, b)}
        except KeyError:
            continue                                         # a provider no longer serves the model
        if eq.verdict == "equivalent" and current == eq.served:
            good[model].add((a, b))
            served_by.update({(model, p): s for p, s in current.items()})
    out: dict[tuple[str, str, str], str] = {}
    for model, edges in good.items():
        parent: dict[str, str] = {}
        for a, b in edges:
            parent[_find(parent, a)] = _find(parent, b)
        comps: dict[str, list[str]] = defaultdict(list)
        for p in list(parent):
            comps[_find(parent, p)].append(p)
        for members in comps.values():
            members = sorted(members)
            if all((x, y) in edges for i, x in enumerate(members) for y in members[i + 1:]):
                label = "+".join(members)
                out.update({(model, p, served_by[(model, p)]): label for p in members})
    return out


def _find(parent: dict[str, str], x: str) -> str:
    parent.setdefault(x, x)
    while parent[x] != x:
        x = parent[x]
    return x
