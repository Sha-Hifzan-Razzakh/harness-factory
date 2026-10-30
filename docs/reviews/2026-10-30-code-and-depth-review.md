# Review of `main`: correctness, completeness and functional depth

**Date:** 2026-10-30 · **Reviewed:** `main` at `b9979f5` (after #28) · **Session:** `session:review`
(https://claude.ai/code/session_01BgTbLKhAFMPUEPRU3mK2t2)

**Against:** *Harness Factory — Requirements v1* (Claude Doc, rev 88 as read on 2026-10-30), `CLAUDE.md` and
`README.md`.

Read this before picking up any PR in the work plan (§4). Each finding has an ID. A PR names the IDs it closes,
and its description says how each one was checked.

**Baseline.** Everything on `main` passes:

- 328 tests pass and 7 are skipped (Docker is not available here; CI forces those tests on).
- `ruff`, `hfx validate`, `hfx tools freeze --check` and `hfx reference` all pass, with 23/23 references.

**How the findings were checked:**

- **Confirmed:** reproduced with a small script against the scripted provider.
- **Code:** checked by reading the code path end to end.
- **Plausible:** read but not reproduced. These are marked as such.

---

## Summary

1. **The measurement machinery is solid, but some scores it stores are wrong.** Run identity, resume, the
   results store, scoring, the quota ledger, the selector and the sandbox are well built. The tests assert real
   behaviour, and no vacuous test was found. Eleven defects still change stored numbers (§1.1). Fix them before
   any real-model campaign.
2. **What it measures is still too small.** The tool surface is thin, the tasks are tiny, and the graders and
   faults are basic (§2). Today's catalog can't separate the harness shapes:
   - offline, V2 to V5 all reach E₀ = 1.0;
   - in Phase 1, `gpt-oss-120b` passed 19/20 tasks on plain V2.
3. **Effort went ahead of the plan.** V4, V5, delegation, remote MCP, agent tools, test tenants and the selector
   (P4/P5 items) are built. Several P0–P2 items are still missing (§1.2).

---

## 1. Code review

### 1.1 Defects that change stored scores (fix first)

| ID | Where | Defect | Check |
|---|---|---|---|
| R-01 | `catalog/tasks/sql-01`, `sql-03`, `sql-04`, `sql-08` | **A hedged answer gets full credit.** The answer check is a bare `output_regex` search, so "It is 2, 3 or 4." matches `(?<![\d.])3(?![\d.])`. The `grounded` check passes after any query, even `SELECT 1`. All four hedges scored SUCCESS with E₀ 1.0. `ops-03` and `res-01` already block this with negative lookaheads. The trap tests try only a single wrong answer, so they miss it. | Confirmed |
| R-02 | `shapes/delegate.py:191` | **An infrastructure failure in a child becomes a scored run.** A helper or V5 worker that ends INCOMPLETE (a provider 5xx or rate limit after retries) comes back as "Helper failed: …", typed as a tool error. The parent carries on and is graded: V5 replanned and ended SUCCESS at 0.917, and `v2-delegate` ended SUCCESS. The same outage in V2 is INCOMPLETE. This breaks "infrastructure is INCOMPLETE". | Confirmed |
| R-03 | `runner/runner.py:102`, `shapes/v4.py:142` | **V4's step cap is not an overrun.** `OVERRUN_KINDS` has `stage_cap` but not `step_cap`, although ADR 0007 and the README say a V4 step cap ends "as a V3 stage cap is". The same behaviour (write, then read until the cap) gives V2 and V3 PARTIAL at 0.5, and V4 SUCCESS at 1.0. | Confirmed |
| R-04 | `results/store.py:144` (`_e0_with_r`) | **Robustness R gives free credit.** Once a cell has R, every clean run's E₀ is recomputed with `w_r·R`, and R is not multiplied by O the way Q is. A FAIL run with outcome 0 goes from E₀ 0 to **0.20** (0.075 after cost). This breaks "No free credit", and it raises a failing cell's mean only when faulted runs exist. | Confirmed |
| R-05 | `results/store.py:217` (`rows(latest=True)`) | **An unscored rerun erases a scored result.** The newest row per RunKey wins whatever its status. SUCCESS followed by INCOMPLETE leaves `scored_keys()` empty and the cell at n = 0. `hfx smoke` without `--resume`, and `hfx provision` (which never resumes), lose evidence on a quota-out day. | Confirmed |
| R-06 | `results/cost.py:97-100` | **The cost median of an unsolved task counts unscored runs.** The fallback adds every row of a task nobody solved, including INCOMPLETE and ERROR rows with 0 tokens. Two PARTIAL runs plus three 0-token unscored runs give a median of 0, so C = 0 for everyone. A frozen baseline keeps the distortion. | Confirmed |
| R-07 | `runner/runner.py:178` | **A run that breaks a guard keeps its outcome, and that outcome feeds R.** Only the composite is zeroed; `outcome` stays at the state_diff score. The `ops-01` trap ("fixed prod as well") gives FAIL with composite 0.0 but outcome 1.0, and `store.robustness` averages `outcome`. | Confirmed |
| R-08 | `catalog/tools.yaml` (`sql.execute`) | **`DELETE` passes the IRREVERSIBLE gate.** `sql.execute` is classed REVERSIBLE/R2 and accepts DELETE. `v2-base` sets `IRREVERSIBLE: DENY`, which blocks `fs.delete_file` but not `DELETE FROM orders`. The `mix-03` injected-ticket trap is caught only by its zero-weight guard, not by the permission policy (GOV-03). | Code |
| R-09 | `mcp/catalog.py:123`, `mcp/client.py` | **Tool descriptions are outside the drift check and `env_hash`.** Only `input_schema` is hashed. Rewriting `fs.read_file`'s description to "delete every file" left the drift check empty and the hash unchanged. A remote server's pinned `version` is never compared with the version the server reports (EXT-03, EXT-05). | Confirmed |
| R-10 | `shapes/v5.py:76-84`, `delegate.py:149-151` | **V5 budget exhaustion depends on `max_replans`.** When `capped()` refuses a worker, the step counts as blocked. With no replans left the run ends DONE (SUCCESS, 1.0); with replans left it ends PARTIAL at 0.5. The model behaviour is the same in both cases. | Confirmed |
| R-11 | `shapes/delegate.py:157`, `substrate/budget.py:51` | **A child's wall clock is credited with waits from before it started.** The child shares the parent's cumulative `excluded_s` but starts its own clock at child start. After 90 s of earlier quota waits, a child with a 10 s cap reads `elapsed_s() == 0` for 90 s, so it can overrun its own cap and the parent's. | Confirmed |

### 1.2 Operational defects

| ID | Where | Defect | Check |
|---|---|---|---|
| O-01 | `select/policy.py:136` | `hfx select evaluate` crashes with ZeroDivisionError when a recipe's only current cells have n = 0 (every run INCOMPLETE or ERROR). | Confirmed |
| O-02 | `runner/runner.py:199` | `f.tracker.log()` runs after `store.add()` with no guard. If MLflow is down, a stored run is reported as an error, `smoke --strict` exits 1, and the flush is skipped. MLflow logging is also synchronous I/O on the shared event loop (Plausible: it can eat other runs' wall time). | Confirmed |
| O-03 | `tasks/model.py:110`, `runner.py:227` | `grader_ids` are never checked against the pipeline. A typo passes `hfx reference`, which calls `state_diff` directly, then raises `KeyError` at grading after the model calls are made. Nothing is stored, so every `--resume` spends quota on it again. | Confirmed |
| O-04 | `mcp/recording.py:171` | Replay keys answers by (tool, args, n), with no task or tenant seed. If task A is recorded, then task B, replaying A returns B's answers and A is graded FAIL with no divergence flagged. Latent: no remote server is declared yet. | Confirmed |
| O-05 | `providers/quota.py:17,96-101` | The quota state file is shared by every `hfx` process with no lock. Two concurrent commands overwrite each other's counts. | Plausible |
| O-06 | `substrate/gateway.py` (`complete`) | `except Exception` doesn't catch `CancelledError`. On Ctrl-C a quota reservation is never released and is persisted, over-counting until the window rolls. | Plausible |

### 1.3 Low-severity defects

| ID | Where | Defect | Check |
|---|---|---|---|
| L-01 | `cli/main.py:393` | `if __name__ == "__main__": app()` sits before `select`, `provision` and `report` are registered, so `python -m harness_factory.cli.main` doesn't have them. The `hfx` entry point is fine. | Confirmed |
| L-02 | `env/mcp/impl/fs_server.py` | `write_file(".")`, a write under a path whose parent is a file, and reading a non-UTF-8 file raise OSError or UnicodeDecodeError instead of `ToolError`. The model only sees "Error executing tool …". | Confirmed |
| L-03 | `env/mcp/impl/sql_server.py:58` | `";" in stmt` refuses a valid one-statement `UPDATE … SET email='a;b@x.ae'`. sqlite3 already refuses multiple statements on its own. | Confirmed |
| L-04 | `grading/state.py` (`_same`) | `remote_equals` uses `json.loads(expected) == actual`, so `"1"` matches `true` and `"0"` matches `false`. | Confirmed |
| L-05 | `provision/provisioner.py:167`, `recipes/model.py:167` | A seed in a recipe's model slot is accepted, nulled before hashing, then wins over the campaign seed. The RunKey's seed then differs from the one sent. No catalog recipe does this today. | Confirmed |
| L-06 | `recipes/model.py:221` | `prompt_refs()` includes `planning.step_prompt` for V5, which never renders it, and `planning.step_max_steps` is hashed for V5 too. Editing `prompts/plan/step.j2` invalidates every V5 result with no change in behaviour. | Confirmed |
| L-07 | `recipes/model.py` (`config.recover_malformed`) | `0` and `None` behave the same but hash differently. | Plausible |
| L-08 | `cli/main.py:520-524`, `:477` | `hfx provision` prints run errors but exits 0. `select evaluate` throws away the campaign result, so errored runs silently drop out of the comparison. | Code |
| L-09 | `tests/conftest.py:11` | Only `SANDBOXES_DIR` is redirected. Traces from tests go to the repo's `var/traces` (1,400+ files), which breaks the "only `tmp_path`" rule. | Plausible |
| L-10 | `catalog/faults/garbled_reads.yaml` | The profile covers fs and sql reads but not `search.*`. Under `v2-search`, R partly measures which tool a recipe used rather than how robust it is. | Plausible |
| L-11 | `results/store.py:127`, `select/selector.py:132` | Cell keys check the recipe and env hashes but not `task_hash`. Rows from an edited task with the same `task_version` keep feeding E, the selector and the cost medians; `stale_tasks` only warns. | Code |
| L-12 | `results/report.py:103`, `integrations/inspect_task.py:96` | `hfx report` §2 shows E without R, while `results --cells` and report §4 use E with R. The Inspect scorer averages INCOMPLETE and ERROR samples as 0. | Code |

### 1.4 Completeness against Requirements v1

The phase of each item is from the requirements doc's phase table.

**Missing although its phase is P0–P2:**

| Area | Requirement | Gap |
|---|---|---|
| Substrate | SUB-02 | `caps.tool_calling: emulated` is declared but does nothing |
| Substrate | SUB-06 | No context accounting: `context_len` is never read |
| Substrate | SUB-07 | Tool output is cut at 16k characters, with no spill file |
| Substrate | SUB-08 | No structured or validated final output (`recover_malformed` repairs tool calls only) |
| Tasks | TSK-04 | No perturbation generator: 0 of the 120+ perturbed variants |
| Environment | ENV-01 | 4 of 8 MCP servers, 11 of ≥ 40 tools |
| Environment | ENV-03 | No latency kind and no malformed-result kind; a "timeout" fault returns at once |
| Knowledge | KNW-01 | No recorded corpora, DVC (MLO-04) or index |
| Recipes | RCP-04 | No recipe generator; the matrix recipes are hand-written |
| Recipes | RCP-05 | Context length and JSON mode are never validated |
| Shapes | SHP-06 | `headless: false` is refused, and there is no clarification path |
| Campaigns | RUN-02 | No pruning: `plan()` is the full cross product |
| Profiler | PRF-01 | The task profile has only depth and risk; side effects are derived |
| Graders | GRD-01 | No "tests pass" grader, no JSON-schema check, no LLM judge |
| Graders | GRD-04 | No grader versions; sandboxes are torn down, so a stored run can't be re-graded |
| Models | MOD-04 | No allocation planner (claimed by #36) |
| Models | MOD-06, 07 | No calibration (claimed by #35) and no re-planning |
| Models | MOD-10 | No LiteLLM Router config |
| Analysis | ANL-01 | No shape × model by task class, and no crossover by step depth |
| Self-observability | NFR-09 | No queue-depth, failure, retry or saturation metrics |

**Stack differences from the "Frameworks and stack" section:**
- **Agent framework:** no Postgres checkpointer on the LangGraph shapes.
- **Tracing:**
  - Langfuse is in the collector config but not in compose.
  - OpenInference attributes are set by hand rather than through the instrumentation.
  - Traces are uncompressed JSONL with no retention.
- **Storage:** results default to SQLite; the Postgres path is never run in CI, and there is no Alembic.
- **Model access:** no `instructor`, and no LiteLLM Router. OpenRouter is configured with paid model ids (no
  `:free`) under free-tier limits.

**Built ahead of the plan:**

| Item | Planned phase | Requirement |
|---|---|---|
| V4 planner | P2 | SHP-02 |
| V5 orchestrator | P4 | SHP-05 |
| Delegation | P3 | EXT-09, 11, 12 |
| Remote MCP | P5 | EXT-05 |
| Agent tools | P5 | EXT-10 |
| Test tenants | P5 | EXT-07 |
| Record/replay | P5 | EXT-08 |
| Selector | P4 | SEL-01..06 |

**Not started, and correctly later-phase:**
- Memory (MEM-01..07) and retrieval (KNW-02..05).
- Governance: ASK is treated as DENY, and there is no approval gate, guardrails or redaction.
- The API (API-01..04).
- The improvement loop (IMP-01..15).

### 1.5 Code quality and CI

**What holds up:**
- The CLAUDE.md rules are mostly honoured. No run writes module-level state; every broad `except` is justified
  and commented; path confinement in fs, search and skills is symlink-safe; `sql.query` is read-only; fault
  messages never say "injected".
- Adding an MCP server needs no core edits. This was tried: a module, a `tools.yaml` entry and a matrix recipe
  passed freeze, validate and smoke.

**Gaps:**
- **New graders aren't pluggable.** A new grader still needs an edit to `default_pipeline()`, and its score never
  reaches E. Only `state_diff` and `trajectory` feed `FamilyScores` (`runner.py:176-182`), and status is
  hard-wired to `state_diff`.
- **New shapes need core edits:** the `Variant` enum, `shapes/builtin.py` and the rules in `validate.py`.
- **Untested modules:** `mlops/tracker.py`, the OTLP export path, and Postgres in the store. The graders are
  tested only through full runs.
- **Never run in CI:**
  - the `postgres` and `mlops` extras;
  - a harness run on any held-out task;
  - `hfx select evaluate`;
  - the provider-capability rules: `cli/main.py:145` calls `validate_recipe` without `providers`.

---

## 2. Functional depth: rich enough, or simple implementations?

The parts that run and measure experiments are rich. The parts that decide **what can be measured** are thin. So
far the factory can only pose simple scenarios.

| Part | Depth | What it handles | What it can't handle yet |
|---|---|---|---|
| Run identity, resume, store | Solid | Content-hashed experiments; skip/retry/new on resume; clean Ctrl-C; parallel campaigns on one loop | Postgres in practice; the R-05 rerun bug |
| Scoring and reports | Solid design | O, Q·O, C against frozen baselines, R, critical zeroing, bootstrap 95% intervals, paired matrix deltas | Bugs R-04, R-06 and R-07; hard-coded per-class weights with no version stored per row; one pooled R across fault profiles; no seed-to-seed spread |
| Gateway and quota | Solid | Retries with exponential backoff and Retry-After; error classification in one place; per-model buckets; persisted minute and day windows | Fallback to another provider; x-ratelimit headers; a month window; a lock across processes |
| Selector | Solid for its size | Hard rules, ranking with intervals, both recipes on a close call, cold start, logged decisions, held-out evaluation | Only 3 held-out tasks, so verdicts are directional |
| Sandbox | Solid | Docker per run with no network, read-only and dropped capabilities; fresh state every run; a canary test | The default `local` sandbox doesn't block the network |
| Harness shapes V1–V5 | **Basic** | Shared model turns and tool batches; budgets; recover-after-rejection; skills upfront or on demand; delegation (see the list below) | Parallel tool calls or workers; context compaction (history grows until it overflows, output cut at 16k characters); structured plans with dependencies; ask-user; approvals; memory |
| Tools and environment | **Thin** | 4 servers, 11 tools (see the list below); remote MCP and tenant plumbing | Running code or tests; git; HTTP or business APIs; documents (docx/xlsx/pdf); email; calendar; another database engine. No real remote server is declared. |
| Tasks | **Thin** | 23 tasks over 3 seeds (31 over 4 with #34), each with traps and guards (sizes below) | Long-horizon work (15–30 steps); data larger than the context; ambiguity that should make the agent ask; distractor-heavy worlds |
| Graders | **Basic** | 10 check kinds: file equals, contains, regex, absent, JSON valid, SQL rows, output regex, tool called, remote equals | Tests passing; number tolerance; SQL rows in any order (`sql_rows` is an exact, ordered list comparison); rubric or LLM judging; open-ended deliverables |
| Faults | **Basic** | 5 kinds (error, timeout, rate limit, empty, garbled), each an instant canned message, per tool, by rate or nth call | Real latency; a server crash mid-run; a write that half-succeeds; bad data inside a "successful" result |

**Shapes, in detail:**
- **V3:** stages run in a straight line, with at most one jump back to retry.
- **V4:** the plan is free text, parsed by a regex for numbered lines. Steps run one after another, with one
  replan by default.
- **V5:** workers run one after another, never in parallel.

**Tools, in detail:**
- `fs` can list, read and write a whole file, and delete. There is no edit, append, move or mkdir.
- `sql` is SQLite query and execute.
- `search` has find and grep.
- `skills` has load.

**Task sizes, in detail:**
- Reference solutions use a median of **2 tool calls** (8 at most).
- Instructions are a median of 24 words (51 at most).
- Seeds: `base` has 2 files (92 bytes) and 2 tables; `shop` has 5 files and 6 tables (about 130 rows);
  `service` has 15 files (13 KB).

### 2.1 Why the thin parts matter

- **There's a ceiling.** In Phase 1, `gpt-oss-120b` on `v2-base` passed 19/20. Offline, V2 to V5 tie at
  E₀ = 1.0 and only C separates them.
- **Planning has nothing to work on.** A 2-call task gives a planner or orchestrator nothing to plan, so on this
  catalog V4, V5 and delegation can only lose on cost, never win on outcome.
- **The campaigns would measure the ceiling, not the harnesses.** P2's exit criterion, "at least one significant
  crossover by step depth", can't be met while nearly every task is a 1–3 step task.

### 2.2 Scenarios the factory can't pose today

1. **Long tasks:** 15–30 steps, where a plan, replanning or splitting work changes the outcome.
2. **Large inputs:** inputs bigger than the context, where search, summarising or truncation decides the result.
3. **Code work:** run the tests, read the failure, fix it, run them again.
4. **Business systems:** reconcile records across services (CRM, invoicing, ticketing).
5. **Ambiguity:** an instruction where the right move is to ask instead of guessing.
6. **Approvals:** a risky action that needs sign-off.
7. **Real tool failures:** slow tools, partial writes and a server dying mid-run.
8. **Open-ended deliverables:** a summary or report graded against a rubric.

---

## 3. Order of work

1. **Wave 1: scoring integrity** (R-, O- and L- findings). This blocks every real-model campaign.
2. **Wave 2: environment depth.** New tools and graders, so harder tasks can be written.
3. **Wave 3: harness depth.** Context management, structured plans, parallel tools and workers, and a rubric
   judge.
4. **Wave 4: task batch 4.** Long, data-heavy tasks written by an author who hasn't seen the recipes or skills,
   on the tools from wave 2.

Each wave can run 4–5 PRs in parallel. Within a wave, the PRs touch mostly separate files. The shared hotspots
are listed per PR, and the second PR to merge rebases and re-runs CI.

## 4. Work plan: reserved PRs

Every PR below is reserved as a draft with an empty `[skip ci]` commit, labelled `session:review`. The placeholder
commit is dropped before the PR is marked ready. A PR that changes a hash or scoring says so in "Notes for review"
and updates the requirements doc where `CLAUDE.md` asks.

| PR | Branch | Wave | Closes | Scope | Hash / scoring impact | Shared hotspots | ADR |
|---|---|---|---|---|---|---|---|
| #PR_ANSWER | `fix/answer-checks` | 1 | R-01 | Anchor or exclude hedges in the `sql-01/03/04/08` answer checks (as `ops-03` and `res-01` do). Add a hedged-answer trap script per task, plus a catalog-wide test that a reply listing two candidates fails every `output_regex` answer check. | **Task hashes** of 4 tasks (bump `version`) | `tests/test_task_traps.py`, README task table | none |
| #PR_ENDINGS | `fix/run-endings` | 1 | R-02, R-03, R-10, R-11 | A child INCOMPLETE propagates as the parent's INCOMPLETE. Add `step_cap` to `OVERRUN_KINDS`. A V5 worker refused for budget ends the run as `budget_exceeded`, whatever `max_replans` is. A child's budget subtracts only the waits after it started. Add a scripted test per case across V2–V5. | **Scoring** (run status logic) | `runner/runner.py` (with #PR_AGG) | none |
| #PR_AGG | `fix/aggregation` | 1 | R-04, R-05, R-06, R-07, O-01, L-11, L-12 | R enters E₀ as `R·O`. `rows(latest)` prefers the newest *scored* row per key. The cost-median fallback uses scored rows only. Outcome is 0 when a critical guard fails. Guard `plan_evaluation` against n = 0. Cells check `task_hash`. Use one E definition in report §2. Inspect skips unscored samples. | **Scoring** (aggregation; stored rows unchanged, recomputed E changes) | `results/store.py`, `runner/runner.py` | none |
| #PR_IDENTITY | `fix/tool-identity` | 1 | R-08, R-09, O-04, L-05, L-06, L-07 | Split destructive SQL out of `sql.execute` (a new IRREVERSIBLE `sql.delete`, or refuse DELETE/DROP in `execute`). Hash tool descriptions in drift and `env_hash`. Compare the pinned remote `version` with the server's. Key replay by task, seed and tenant. Refuse a seed in a recipe slot. Hash only the prompts and fields a shape uses. Normalise `recover_malformed` 0/None. | **env_hash** of every run (descriptions) and **recipe hashes** of V5 recipes; call out as a one-time reset of comparisons | `catalog/tools.yaml` (refreeze), `recipes/model.py` | none |
| #PR_OPS | `fix/ops-hardening` | 1 | O-02, O-03, O-05, O-06, L-01, L-02, L-03, L-04, L-08, L-09, L-10 | MLflow failures are logged, not raised, and run off the loop. Validate `grader_ids` when a task loads. File lock on the quota ledger. Release the reservation on cancel. Fix the `__main__` placement. Raise `ToolError` for the fs edge cases. Allow `;` in literals. Make `remote_equals` strict on type. `provision` exits non-zero on error. Tests write traces under `tmp_path`. Add `search.*` to `garbled-reads`. | Fault profile `garbled-reads` changes (fault runs only) | `cli/main.py`, `tests/conftest.py` | none |
| #PR_FSX | `feat/fs-edit-tools` | 2 | ENV-01 (part) | A new `fsx` server: `edit` (exact replace), `append`, `move`, `mkdir`, `tree`, all with side-effect and risk classes. Use `granted_by` where READ-only. Add a matrix recipe `v2-fsx`. | New server only; existing env hashes unchanged | `catalog/tools.yaml`, `ci.yml` matrix gate line | none |
| #PR_CODE | `feat/env-code-git` | 2 | ENV-01 (part), GRD-01 (tests pass) | A `code` server (run pytest or a script inside the sandbox, with a time and output cap) and a `git` server (status, diff, log, commit, branch) over a seeded repo. A new `tests_pass` check kind. | New servers and a new check kind; existing hashes unchanged | `catalog/tools.yaml`, `env/sandbox.Dockerfile` (image tag changes), `grading/state.py` | **0012**: running code in the sandbox |
| #PR_BIZ | `feat/env-business-api` | 2 | ENV-01 (part), ENV-02 | A local mock business API (CRM, invoicing, ticketing) served as a stdio MCP server over a seeded state file, so it runs offline in CI. `remote_equals`-style checks on its state. Optionally a streamable-HTTP variant that exercises the tenant path end to end. | New server; existing hashes unchanged | `catalog/tools.yaml`, `tests/` fixtures | none |
| #PR_DOCS | `feat/env-docs` | 2 | ENV-01 (part) | A `docs` server: read and write docx, xlsx and pdf (read only), with checks on cells, paragraphs and pages. This needs libraries beyond `mcp` and the stdlib, against the current server rule, so the ADR decides how the sandbox image carries them. | New server, new check kinds; **sandbox image tag** changes | `env/sandbox.Dockerfile`, `SERVER_FILES`, `pyproject.toml`, `uv.lock` | **0013**: non-stdlib server dependencies |
| #PR_GRADERS | `feat/graders-depth` | 2 | GRD-01, GRD-04 | Check kinds `sql_rows_unordered`, `number_close` (tolerance), `json_schema` and `output_exact`. Grader versions recorded per grade. Keep the end-state snapshot so a stored run can be re-graded (`hfx regrade`). Graders become pluggable into `FamilyScores` (§1.5). | **Scoring** only for tasks that adopt the new kinds; grader versions are added to the stored grade | `grading/`, `tasks/model.py`, `runner/runner.py` | none |
| #PR_JUDGE | `feat/rubric-judge` | 3 | GRD-03 | A rubric grader for open-ended outputs through `ModelGateway`. Calibrate it on ≥ 100 hand-scored items, gated on kappa ≥ 0.7. The judge model and prompt go into the grade's identity. It is never used for a task's main outcome until calibrated. | **Scoring** for tasks that use it | `grading/`, `catalog/` | **0014**: LLM judge and calibration |
| #PR_CONTEXT | `feat/context-management` | 3 | SUB-06, SUB-07, RCP-05 (context part) | Token accounting per turn against `context_len`. Long tool output spills to a file the model can read in pages. A `config.compaction` recipe dimension (summarise old turns) with a matrix recipe and an offline scripted test. Validate context length against the provider catalog. | New optional recipe field (in `_HASH_OMIT_WHEN_NONE`); existing hashes unchanged | `shapes/common.py`, `substrate/tools.py`, `recipes/model.py` | **0015**: context management |
| #PR_SHAPES | `feat/shape-depth` | 3 | SUB-08, SHP-04 (structure) | Structured output (SUB-08) for V4 and V5 plans (steps with `depends_on`, tool hints, done-when), replacing regex parsing behind a recipe flag. `config.parallel_tool_calls`. V5 runs independent workers concurrently within the budget. A scripted test per shape. | New optional recipe fields; existing hashes unchanged | `shapes/v4.py`, `shapes/v5.py`, `shapes/common.py` | none |
| #PR_BATCH4 | `feat/task-batch-4` | 4 | Scale targets (tasks, depth mix, side effects, held-out) | 15–20 long, data-heavy tasks on the wave-2 tools (references of 8–20 calls, seeds 10–100× larger, distractor files and rows, some needing search or summarising), written by an author who hasn't opened recipes, prompts, skills or shapes. Raise held-out to ≥ 25%. | New tasks only | `tests/test_task_traps.py`, README task table | none |

**Dependencies:**

| PR | Depends on |
|---|---|
| #PR_ANSWER, #PR_ENDINGS, #PR_AGG, #PR_IDENTITY, #PR_OPS | nothing; start in parallel |
| #PR_FSX, #PR_CODE, #PR_BIZ, #PR_DOCS, #PR_GRADERS | wave 1 merged, so their runs are scored correctly |
| #PR_JUDGE | #PR_GRADERS (pluggable graders) |
| #PR_CONTEXT, #PR_SHAPES | wave 1; otherwise independent of each other |
| #PR_BATCH4 | #PR_FSX, #PR_CODE, #PR_BIZ, #PR_GRADERS (and #PR_DOCS for document tasks) |

**Not assigned yet,** because they belong to later phases or other sessions:
- Perturbation generator (TSK-04).
- Recorded corpora and DVC (KNW-01, MLO-04).
- Recipe generator and campaign pruning (RCP-04, RUN-02).
- Ask-user and approvals (SHP-06, GOV).
- Postgres checkpointer and Langfuse in compose.
- Allocation planner and calibration: #36 and #35, `session:core`.

**Ledger.** These rows belong in `docs/coordination/sessions.md`, which arrives with #33. Once #33 merges, this
session adds its session row, the PR rows above and ADRs 0012–0015 in its own PR, per ledger rule 3.
