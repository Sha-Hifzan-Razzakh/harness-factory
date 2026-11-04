"""Runner transfer check (RUN-05, ADR 0016): does a recipe's score carry over from the native runtime to a
framework runner?

1. The two recipes must be the same harness except for `runtime` (a matrix recipe `base: <native> + runtime: ...`).
2. Pair runs that differ only in the runner: same task version and task hash, seed, canonical model, provider and
   served model id, same tool environment (`tools_hash`), clean (no fault profile) and scored.
3. Take the paired difference of E0 (stored `composite`, runner minus native) with `results.report.paired_delta` and
   judge it with the provider-calibration rule (`results.calibration.judge`): `inconclusive` below `min_pairs`,
   `equivalent` when the 95% interval lies within ±margin, `different` when it excludes 0, else `inconclusive`.
4. Freeze the result (content-addressed) in the results store. The record names the framework versions the runner
   recipe was hashed with, and reports, for information, the median ratios of tokens and of model calls (what the
   framework's own prompts, formatting and retries cost) next to the verdict.

A verdict is about one recipe on one model and provider: a framework upgrade changes the runner recipe's hash, so
its runs no longer pair with the old verdict's and the check has to be run again.
"""

from __future__ import annotations

import statistics
from datetime import UTC, datetime

from pydantic import BaseModel, Field

from harness_factory.hashing import content_hash
from harness_factory.providers.catalog import ProviderCatalog
from harness_factory.recipes.model import HarnessRecipe
from harness_factory.results.calibration import DEFAULT_MARGIN, DEFAULT_MIN_PAIRS, Verdict, judge
from harness_factory.results.cost import is_clean
from harness_factory.results.report import paired_delta
from harness_factory.results.store import UNSCORED
from harness_factory.runners.identity import framework_versions

_NOT_HARNESS = ("id", "version", "base", "runtime")         # what may differ between the two recipes


class Transfer(BaseModel):
    native_recipe: str
    runner_recipe: str
    runtime: str
    native_hash: str
    runner_hash: str
    framework: dict[str, str | None]                         # package -> version the runner recipe was hashed with
    canonical_model: str
    provider: str
    served: str                                              # provider model id the pairs were served by
    metric: str = "composite"                                # E0
    margin: float
    min_pairs: int
    n: int
    mean: float | None = None                                # runner minus native
    ci: tuple[float, float] | None = None
    wins: int = 0                                            # pairs where the runner scored higher
    losses: int = 0
    ties: int = 0
    token_ratio: float | None = None                         # median tokens(runner) / tokens(native)
    call_ratio: float | None = None                          # median model calls(runner) / model calls(native)
    verdict: Verdict
    reason: str
    note: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @property
    def id(self) -> str:
        return content_hash({k: v for k, v in self.model_dump(mode="json").items() if k not in ("created_at", "note")},
                            12)


def counterpart_issues(native: HarnessRecipe, runner: HarnessRecipe) -> list[str]:
    """Why these two recipes are not the same harness on two runtimes (empty when they are)."""
    out = []
    if native.runner is not None:
        out.append(f"{native.id} is not native (runtime {native.runtime})")
    if runner.runner is None:
        out.append(f"{runner.id} has no framework runtime")
    a = native.model_dump(mode="json", exclude=set(_NOT_HARNESS))
    b = runner.model_dump(mode="json", exclude=set(_NOT_HARNESS))
    diff = sorted(k for k in a.keys() | b.keys() if a.get(k) != b.get(k))
    if diff:
        out.append(f"{native.id} and {runner.id} also differ in {', '.join(diff)}")
    return out


def _pair_key(r: dict) -> tuple:
    return (r["task_version"], str(r["run_key"]).split("|")[1], r["seed"], r.get("tools_hash"))


def pairs(rows: list[dict], native_hash: str, runner_hash: str, canonical_model: str, provider: str,
          served: str) -> list[tuple[dict, dict]]:
    """(native run, runner run) for every key both have a usable run for."""
    usable: dict[str, dict[tuple, dict]] = {native_hash: {}, runner_hash: {}}
    for r in rows:
        h = r["recipe_hash"]
        if (h in usable and r["canonical_model"] == canonical_model and r.get("provider") == provider
                and r.get("served_id") == served and is_clean(r) and r["status"] not in UNSCORED
                and r.get("tools_hash")):
            usable[h][_pair_key(r)] = r                      # rows come oldest first: the newest wins
    a, b = usable[native_hash], usable[runner_hash]
    return [(a[k], b[k]) for k in sorted(a.keys() & b.keys(), key=str)]


def _median_ratio(ps: list[tuple[dict, dict]], value) -> float | None:
    ratios = [value(rb) / value(ra) for ra, rb in ps if value(ra) > 0]
    return round(statistics.median(ratios), 4) if ratios else None


def transfer(rows: list[dict], providers: ProviderCatalog, native: HarnessRecipe, runner: HarnessRecipe,
             canonical_model: str, provider: str, margin: float = DEFAULT_MARGIN,
             min_pairs: int = DEFAULT_MIN_PAIRS, note: str = "") -> Transfer:
    issues = counterpart_issues(native, runner)
    if issues:
        raise ValueError("; ".join(issues))
    served = providers.served(provider, canonical_model).provider_model_id
    native_hash, runner_hash = native.content_hash(), runner.content_hash()
    ps = pairs(rows, native_hash, runner_hash, canonical_model, provider, served)
    d = paired_delta([(float(ra["composite"]), float(rb["composite"])) for ra, rb in ps])
    verdict, reason = judge(d, margin, min_pairs)
    return Transfer(native_recipe=native.id, runner_recipe=runner.id, runtime=str(runner.runner),
                    native_hash=native_hash, runner_hash=runner_hash, framework=framework_versions(str(runner.runner)),
                    canonical_model=canonical_model, provider=provider, served=served, margin=margin,
                    min_pairs=min_pairs, n=len(ps),
                    token_ratio=_median_ratio(ps, lambda r: r["tokens_in"] + r["tokens_out"]),
                    call_ratio=_median_ratio(ps, lambda r: r["model_calls"]),
                    verdict=verdict, reason=reason, note=note,
                    **({"mean": d["mean"], "ci": d["ci"], "wins": d["wins"], "losses": d["losses"],
                        "ties": d["ties"]} if ps else {}))
