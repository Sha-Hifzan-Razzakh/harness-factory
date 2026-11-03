# ADR 0011: Allocation planner

Status: accepted · 2026-11-02 · Roadmap: P2 (allocation planner; builds on ADR 0010) · Requirements: MOD-04, MOD-06

## Context

A campaign has always run on one provider: `hfx smoke --provider groq`. With several providers serving the same
canonical model, someone has to decide which one serves each cell. That choice depends on quota (free tiers run out
by day, per account or per model), on price, and on whether the providers have been shown to behave alike. MOD-04
asks for that choice to be made before the campaign, one provider per cell, and never changed inside a cell. MOD-06
says a provider may share a model's runs only after equivalence calibration. ADR 0010 produced the verdicts; this
ADR uses them.

## Decision

1. **Plan first, then assign providers.** The campaign is planned without providers (tasks × recipes × models ×
   fault profiles × seeds, in the usual fixed order). Then the planner goes through the models in plan order and
   picks a *serving group* for each from the active providers that serve it (`--providers` narrows the list). The
   RunKey keeps its provider, so nothing about identity changes: the allocation only decides which provider's
   RunSpecs are planned.
2. **Serving groups.** A group is either a single provider or an *equivalent pool*: providers with a current
   `equivalent` verdict for that model (`results.calibration.pools`). "Current" means every pair inside the pool has
   its own `equivalent` record and every provider still serves the model id it was calibrated with. Only a pool may
   split a model's runs, and its runs aggregate into one pooled cell (`groq+openrouter`). So within a cell the
   provider never changes: the cell's provider is the pool. Without such a verdict, every cell of a model goes to
   one provider, not just every cell. This matters because the selector compares recipes for one model on one
   provider (`build_policy(model, provider)`), and cells split across unproven providers would mix evidence it
   can't compare. A `different` or `inconclusive` verdict, or one whose served model id has changed, never splits.
3. **Capacity comes from the ledger and the projection.** Each run is estimated per provider with the projection's
   `Estimator` (MOD-05: history first, then shape defaults), priced from the catalog when history has no cost. A
   provider's capacity is what is left today in its quota bucket (`QuotaLedger.remaining`; `scope: model` gives
   each model its own bucket), minus what earlier models in the same plan already took from that bucket. So on an
   account-scoped provider, the second model sees what the first one used. Days are counted the way the projection
   counts them: runs that fit today in plan order, then the rest at the daily limits. A bucket the ledger reports
   as spent today is excluded.
4. **Strategy.** `capacity` (the default) ranks groups by days needed, then spare runs left today, then more
   providers, then list-price dollars. `cost` ranks by dollars, then days, then spare runs, then more providers. The
   last tie-break is the label, so the same inputs always give the same allocation. A pool always has at least the
   spare capacity of its best member, so under `capacity` an equivalent pool wins.
5. **The split.** A pool divides a model's runs in proportion to each member's remaining capacity today, counted in
   runs and capped at the model's run count, using largest remainder. So two unlimited providers split evenly, and
   a member already spent today gets nothing. Runs are then interleaved in plan order, so every cell gets runs from
   every member and no member gets a block of whole tasks.
6. **Refusals mirror the preflight.** The planner refuses a model that no candidate serves, a model whose every
   candidate is spent today or needs more days than `--deadline-days`, and a plan whose list-price total is over
   `--budget-usd`. Each refusal gives the reason per candidate. A refused allocation is not saved and `hfx allocate
   plan` exits 2, as `hfx smoke` does.
7. **Decisions are logged; allocations are frozen.** Every decision is logged in `var/decisions/`, one file per
   model, refusals included. The layout is the selector's (`{"decision": ..., "context": ...}`) and each file
   records every candidate group with its capacity, runs, days, dollars, spare runs and exclusion reason, plus the
   verdict in force for every candidate pair. An allocation that refuses nothing is saved, content-addressed (its
   strategy and RunSpecs), in `var/allocations/<id>.json`. `hfx allocate run <id>` runs those frozen specs, so
   `--resume` finds the same RunKeys even after quota has moved. It repeats the projection preflight before
   starting.
8. **CLI in its own module.** `hfx allocate plan|run|show` lives in `cli/allocation.py` and is registered by an
   appended import and `add_typer` in `cli/main.py`. `hfx smoke` is unchanged, so allocation is opt-in.

## Not in scope

- Re-planning mid-campaign when a bucket runs out or a model is removed (MOD-07). A frozen allocation keeps its
  providers; runs past today's quota end INCOMPLETE and `--resume` finishes them after the reset.
- A calibration gate on single-provider service. MOD-06's "only after equivalence calibration" is applied to
  sharing a model; a model with no calibrated pair still runs on one provider, as every campaign has so far.
  Requiring a reference provider would block every single-provider campaign today, so it waits for the catalog to
  name reference providers.
- Emitting a LiteLLM router config (MOD-10).

## Consequences

- No hash or score changes. The RunKey, recipe hash, env hash, stored columns and composite are untouched. Runs
  from an allocation are ordinary runs on their provider.
- A model on two equivalent providers gets its runs spread over two quota buckets, which can be the difference
  between one day and several on free tiers. Its cell has twice the evidence when aggregated with
  `hfx calibration cells`.
- `hfx results --cells` and the selector still read per-provider cells. A pooled model's runs show there as two
  half-size cells until the selector reads pooled cells (the follow-up named in ADR 0010).
- Offline, two scripted providers in a test-local catalog give the expected splits: without a verdict, the provider
  with capacity (or, at equal capacity, the cheaper one) serves every run; with an `equivalent` verdict, runs split
  by remaining capacity and pool into one cell.
