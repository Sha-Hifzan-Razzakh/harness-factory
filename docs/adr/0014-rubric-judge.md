# ADR 0014: LLM judge and its calibration

Status: accepted · 2026-11-02 · PR: #49 (stacked on #48, pluggable graders) · Requirement: GRD-03

## Context

Every check the factory had compared the environment with something fixed: a file's text, SQL rows, a number
within a tolerance, a JSON shape. That leaves out open-ended deliverables (a summary, a handover note, a report),
where many texts are right and no regex tells a good one from a confident wrong one (the review of 2026-10-30,
§2 and §4, row #49). An LLM judge can grade those, but its scores are only as good as its agreement with people.
The requirements say a judge is trusted only after calibration on at least 100 hand-scored items with Cohen's
kappa of at least 0.7, and the composite must never depend on a judge nobody has measured.

#48 gave graders versions, score families, stored end states and `hfx regrade`. This ADR builds on those.

## Decision

1. **A check kind, not a new score family.** `kind: rubric` goes in the task's oracle next to deterministic
   checks: `rubric: {criteria: [{id, text, weight}], threshold}` (criteria weights sum to 1), and an optional
   `path`. The subject is the file at `path` in the workspace, or the final answer when there is no path. The
   check earns its weight when the judge's weighted criterion score reaches the threshold: pass/fail, because
   pass/fail is what calibration measures (kappa); the score itself is stored and reported (MAE). A rubric check
   is never critical and never weight 0 (a judge's opinion can't fail a run outright; guards stay deterministic),
   and an oracle with rubric checks must keep some deterministic weight (decision 4). `rubric` is in
   `_CHECK_HASH_OMIT_WHEN_NONE`, so no existing task hash changes. `StateDiffGrader` grades the kind; it keeps
   version 1 because no existing check's result changes (the precedent #48 set for its new kinds), and the rubric
   check's own identity is the judge's (decision 3).
2. **Judging is a step before the graders.** Graders are synchronous and read the environment; a judge needs
   model calls. `RubricJudge.judge_checks()` runs first (in `run_one`, `run_reference`, and
   `GradingPipeline.arun()` for `hfx regrade`) and puts one `RubricVerdict` per rubric check in
   `GradeContext.judgements`; `StateDiffGrader` only reads them. A missing or empty deliverable fails without a
   call. An unscored run (INCOMPLETE / ERROR) is not judged.
3. **Through `ModelGateway`, on its own tracer.** The judge calls its model like any harness does: a quota slot
   in the shared `QuotaLedger`, the per-call timeout, retries of transient errors, and
   `classify_provider_error` as the only place provider exceptions are classified (the judge catches only the
   `HarnessError` the gateway raises, plus the parser's own). Its spans go to a `Tracer` of its own, so they are
   never in the run's spans: not in its `CostLedger` (hence never in C), not in its budget, not in its trace's
   model calls, and its wall time is subtracted from the run's. Its usage is stored with each verdict and summed
   per run in a new nullable `runs.judge_usage` column (and `regrades.judge_usage`).
4. **Identity.** `judge_hash` = provider, canonical model, provider model id, the sources of the two prompt
   templates (`prompts/judge/system.j2`, `prompts/judge/rubric.j2`), temperature, max output tokens, the subject
   cap, and `JUDGE_PROTOCOL` (framing + reply parser). Timeout and retries are infrastructure and left out. The
   rubric has its own `rubric_hash` (and is part of the task hash). Both, with the calibration state, are stored in
   every verdict inside the stored grade. The judge's config is `catalog/judge/judge.yaml`;
   `HF_JUDGE_PROVIDER` / `HF_JUDGE_MODEL` override it for a shell, and `hfx judge status` shows the judge in
   effect.
5. **Uncalibrated means weight 0.** A judge is calibrated when the latest calibration record of its current hash
   that covers at least 100 items has kappa >= 0.7 (`grading/judge.status_from_records`). Records on fewer items
   are informational; a newer qualifying-size record that falls below 0.7 withdraws the trust an older one gave.
   An uncalibrated judge still grades and its verdicts are reported, but its checks carry weight 0 and the other
   checks' weights are scaled to sum to 1 again: the outcome is then exactly the deterministic checks' share, so
   the composite never depends on that judge. Without a judge (no `judge.yaml`, or a test factory) rubric checks
   are ungraded and weighted the same way.
6. **A judge failure is never the agent's 0.** Provider down, quota, a setup problem or an unparseable reply
   makes the verdict unavailable:
   - uncalibrated judge: the check is marked `ungraded` and carries weight 0 as it would anyway; the run is scored
     on its deterministic checks as usual. Making it INCOMPLETE would throw away a measurable run for a grade that
     doesn't count.
   - calibrated judge: the outcome needs the verdict and can't be computed, so the run becomes unscored:
     INCOMPLETE (provider, quota, unusable reply), or ERROR for a judge setup problem (CONFIG / PROVISION), exactly
     as CLAUDE.md treats infrastructure and setup. It is excluded from cell means and rerun by `--resume`. Marking
     only the check ungraded and rescaling would score that run on a different check set from its cell's other
     runs. The run's end state keeps how it stood before (`pre_judge`), so `hfx regrade` can judge it again.
7. **The judge sees the rubric and the subject, nothing else.** No expected answers, no other checks, not even
   the threshold or the task's instruction. The subject is data: the prompt says so, and it sits between two
   marker lines carrying a code derived from the subject (first 16 hex digits of its SHA-256). A subject can't
   contain its own closing marker, so it can't end its block and add instructions or criteria after it. The reply
   must be a JSON object scoring every criterion id once in [0, 1]; anything else is the judge's MODEL_PARSE.
8. **The agent never sees the rubric.** The rubric lives in the oracle, which never reaches the sandbox, and the
   prompt assembler now renders task and stage templates with the task minus its oracle (`agent_view`), so even a
   template that reaches for `task.oracle` finds nothing. Rendered prompts of existing recipes are unchanged (no
   template used the oracle).
9. **Calibration.** A calibration set is YAML: named rubrics and items (`subject`, `rubric`, `human_score` in
   [0, 1], optional `human_pass`). `hfx judge calibrate --set <file> [--model --provider]` runs the judge over it
   (paced by the ledger), reports Cohen's kappa on pass/fail and MAE on scores, lists disagreements, and stores a
   record (judge hash, set name and hash, n, failed, kappa, MAE, date) in the results store's new
   `judge_calibrations` table. Items the judge couldn't grade are counted as failed and left out of n. Kappa is
   undefined (stored as NULL, never trusted) when both raters gave one label to everything. Records live in the
   results store, next to the runs whose weights they decide, rather than in a catalog file (catalog writes go
   through the authoring service, ADR 0009, and a calibration is a measurement, not an authored item).
10. **Offline first.** A `scripted-judge` provider serves `scripted/judge` (`ScriptedJudgeBackend`): it reads
    the rendered prompt the way a model would, scores quoted phrases and keywords, and is deterministic. It is its
    own provider so no smoke over the `scripted` provider runs it as an agent. `catalog/judge/calibration/seed.yaml`
    is a 16-item format example, labelled as such; it can never calibrate anything. `docs-03` (DEV) is the first
    task with a rubric check, alongside deterministic checks, guards and two traps.

## Consequences

- Scoring: unchanged for every task without a rubric check (same weights, same arithmetic; the do-nothing,
  legacy-scoring and smoke gates are unchanged). For a task with one, the outcome is its deterministic share until
  a judge is calibrated; after that the rubric check carries its declared weight. Calibrating a judge (adding a
  record) therefore changes how new runs of such tasks are scored; `hfx regrade` shows what it would do to stored
  runs, whose own rows never change.
- Store: additive only (`runs.judge_usage`, `regrades.judge_usage`, `judge_calibrations`).
- `hfx regrade` is now async inside (`await regrade(...)`); the CLI is unchanged.
- Cells are keyed by recipe, model, provider and environment, not by judge. Runs of a rubric task graded by two
  different calibrated judges would share a cell. Until the judge is part of the cell key, change the judge
  between campaigns, not within one, and compare with `hfx regrade`. Every stored verdict says which judge gave it.
- Calibration is per judge, measured over whatever rubrics the set holds, not per rubric. A set should cover the
  kinds of rubric the tasks use; per-rubric calibration is a possible later tightening.
- An uncalibrated judge still costs its calls. That buys the verdicts needed to see how it does before trusting it.
- Not done here: the hand-scored set of >= 100 items (needs people, not code), a real judge model choice, and
  the judge in the cell key.
