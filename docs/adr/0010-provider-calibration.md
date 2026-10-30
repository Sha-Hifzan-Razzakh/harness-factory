# ADR 0010: Cross-provider equivalence calibration

Status: accepted · 2026-10-30 · Roadmap: P2 (cross-provider equivalence; the allocation planner builds on it)

## Context

The canonical model is the experiment variable and the provider is infrastructure. But two providers serving "the
same" model can still differ: quantisation, context limits, sampling defaults, tool-call parsing. So every cell has
been keyed by provider and environment hash. A model served by Groq and by OpenRouter gets two cells, each with half
the evidence, and the selector can't use one provider's runs to judge the other's. Pooling needs a measured reason,
not an assumption.

## Decision

1. **Pairs that differ only in the provider.** Two runs pair when they have the same task version and task hash,
   recipe hash, canonical model, seed and provider-neutral tool environment. Both must be clean (no fault profile)
   and scored (not INCOMPLETE/ERROR), and each must have been served by the model id its provider's catalog entry
   names now. Newer rows replace older ones for the same key.
2. **Two new run columns.** `tools_hash` is `env_hash` computed with the provider model id replaced by `*`.
   `served_id` is the provider model id. `RunKey.env_hash` mixes the two and can't be split, so both are stored
   next to it (nullable, added by the store's additive migration). The RunKey is unchanged, so no hash changes.
   Runs stored before this change don't pair or pool.
3. **The statistic.** It is the paired difference of E0 (the stored `composite`): its mean and a bootstrap 95%
   interval. This is `results.report.paired_delta`, the same statistic as the matrix report. The verdict is
   decided in this order:
   - `inconclusive` below `min_pairs` (default 20);
   - `equivalent` when the whole interval is within ±`margin` (default 0.1);
   - `different` when the interval excludes 0;
   - `inconclusive` otherwise.

   The median token ratio is recorded for information. Cost is not part of the verdict.
4. **Frozen like a baseline.** An `Equivalence` record is content-addressed and saved in a new `equivalences` table.
   For each model and provider pair, the newest record is the one in force.
5. **Pooling is opt-in and conservative.** `aggregate(pools=...)` and `hfx calibration cells` pool a run when its
   provider is in an `equivalent` pair and still serves the calibrated model id. The pooled cell is keyed by the
   pool ("groq+openrouter") and `tools:<tools_hash>`. Pools are connected components, pooled only when every pair
   inside has its own `equivalent` record; nothing is assumed transitively. C stays per provider: each run is scored
   against its own provider's median before its cell is pooled. `hfx results --cells` and the selector are
   unchanged.
6. **Calibrated once, applied to every recipe.** Equivalence is a claim about how two providers serve a model, so
   one recipe's pairs (default `v2-base`) stand for all recipes. The record lists the recipes it was measured on.
7. **CLI in its own module.** `hfx calibration run|list|cells` lives in `cli/calibration.py`, registered with one
   `add_typer` line (docs/coordination/sessions.md, rule 5). `run --run` first runs the missing pairs through the
   normal campaign path (resume skips runs the store already has).

## Consequences

- A model on two equivalent providers gets one cell with twice the evidence. Its runs are spread across two quota
  buckets, which is what the allocation planner will use.
- A provider that changes the model id it serves loses its verdict until it is calibrated again.
- The selector still reads per-provider cells. Letting it read pooled cells is a follow-up; it has to keep the rule
  that evidence comes only from current cells.
- Offline, two scripted providers running the reference plan come out `equivalent` and pool. One running the lazy
  script comes out `different`.
