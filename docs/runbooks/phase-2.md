# Phase 2 runbook: recipe matrix and selector on real models

**Goal:** find out which recipe dimensions change results on real free models, and whether picking a recipe per
task class (the selector) beats the single best recipe on held-out tasks.

**Runs on:** your machine, against Groq's free tier: `gpt-oss-120b` and `gpt-oss-20b`, each with 200k tokens
per day. It spans several days; every stage is resumable.

## 0. Before the first run

```powershell
git pull                                   # main, with PRs #3-#20 merged
uv sync --extra dev --extra mlops
docker compose up -d                       # optional: MLflow, Phoenix, Postgres, collector
uv run hfx validate                        # 8 recipes ok
uv run hfx baseline save --note "phase 1"  # freeze Phase 1's cost medians before new runs arrive
```

Check the lines `smoke` prints first: `results`, `traces` and `mlflow` must point where you expect (Phase 1 sent
runs to policy-intel's MLflow once).

## 1. Stages

Every command carries `--resume`: it runs what isn't done yet and skips scored runs, so the same command is
right on the first day and after every daily reset (00:00 UTC, 04:00 in Dubai). Repeat it until the plan line
shows `new 0`. Add `--dry-run` to see the projection first: runs, tokens, how many fit today, days.

Stages are ordered so each one finishes a complete, paired comparison before the next starts.

| Stage | What it answers | Command |
|---|---|---|
| A | Does a weaker model drop below the ceiling? Do retry-after-rejection and search help? | `uv run hfx smoke --provider groq --models gpt-oss-120b,gpt-oss-20b --recipe v2-base,v2-recover,v2-search --resume` |
| B | Do the plan-check prompt, skills and the V3 shape help? | `uv run hfx smoke --provider groq --models gpt-oss-120b,gpt-oss-20b --recipe v2-plan-check,v2-skills-on-demand,v3-staged --resume` |
| C | Robustness: who keeps their result when tools fail? | `uv run hfx smoke --provider groq --models gpt-oss-120b,gpt-oss-20b --recipe v2-base,v2-recover --faults none,first-call-fails --resume` |
| D | Does the selector beat the best single recipe? | `uv run hfx select show --model <m> --provider groq`, then `uv run hfx select evaluate --model <m> --provider groq` for each model |

Stage C's clean runs (`none`) are stage A's: `--resume` skips them, so only the faulted runs are new.

## 2. Report

```powershell
uv run hfx report --out docs/reports/phase-2.md --title "Phase 2: recipe matrix on gpt-oss"
```

The report is generated from the store. Add your own notes under it, as in `phase-1.md`.

## 3. Reading the results

- **Ceiling.** If `gpt-oss-20b` also passes 90% or more of the tasks on `v2-base`, the tasks are too easy to
  separate recipes. Task batch 3 (harder, written by someone who hasn't seen the skills) comes before more
  dimensions.
- **Matrix verdicts (report §3).** "better" or "worse" means the paired 95% interval excludes 0. With 20 tasks
  and one seed, expect many "no clear difference": that is a finding too.
- **Skills.** Gains on this catalog are an upper bound (the skills' author wrote these tasks).
- **Selector (§4 and `select evaluate`).** v1's exit criterion is the selector beating the best global recipe
  on held-out tasks. With only 3 held-out tasks, treat one Phase 2 result as a direction, not a verdict.
- **Not scored (§6).** INCOMPLETE runs (quota, provider) are finished by `--resume`; ERROR runs are setup
  problems worth a look before resuming.
