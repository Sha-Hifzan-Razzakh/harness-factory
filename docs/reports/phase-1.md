# Phase 1 report: first real-provider campaign

**Date:** 2026-10-29 (times are local, UTC+4)
**Scope:** get the factory running end to end on a real provider (Groq), with the observability stack available
next to other Docker stacks on the same machine, and confirm results and traces are kept.
**Outcome:** done. `gpt-oss-120b` on Groq with `v2-base` passed 19 of 20 tasks (mean E0 0.949). All runs are
in the results store and mirrored to MLflow and Phoenix.

`var/` (results DB, traces) and `.env` are git-ignored, so the numbers in section 4 exist in the repo only in
this file.

---

## 1. Changes

### 1.1 Committed to the repo

| File | Change | Why |
|---|---|---|
| `compose.yaml` | Every published host port is now an env var with the old port as default: `HF_POSTGRES_PORT` (5432), `HF_MLFLOW_PORT` (5000), `HF_PHOENIX_PORT` (6006), `HF_OTLP_GRPC_PORT` (4317), `HF_OTLP_HTTP_PORT` (4318). Added a two-line header comment explaining them. | Another stack on the dev machine (`policy-intel`) already publishes 5000, 6006 and 4317, so `docker compose up -d` failed. |
| `.env.example` | New commented block documenting the five `HF_*_PORT` variables. | So the override is discoverable. |
| `docs/reports/phase-1.md` | This report. | Phase 1 record. |

Only the left-hand (host) side of each mapping changed. Containers still reach each other on the container
ports (`http://phoenix:6006`, `otel-collector:4318`, `mlflow:5000`), so `observability/collector.yaml` and
the compose-internal URLs are unchanged. With no variables set, behaviour is identical to before.

**Not affected:** run identity and scoring. No hashed input changed (recipes, prompts, tasks, `tools.yaml`,
`providers.yaml`), so all `RunKey`s and earlier results stay comparable.

### 1.2 Local only (not in the repo; each developer repeats these)

**`.env`** (git-ignored):

```dotenv
GROQ_API_KEY=<your key>

# Host ports: policy-intel already uses 5000, 6006, 4317
HF_MLFLOW_PORT=5001
HF_PHOENIX_PORT=6007
HF_OTLP_GRPC_PORT=4327
HF_OTLP_HTTP_PORT=4328

# Tracing and MLflow for hfx running on the host: must use the host ports above
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4328
MLFLOW_TRACKING_URI=http://localhost:5001
```

**Python environment:** `uv sync --extra dev --extra mlops`. Without the `mlops` extra, `ExperimentTracker`
quietly disables itself even when `MLFLOW_TRACKING_URI` is set (`src/harness_factory/mlops/tracker.py:16-19`).

**One-off backfill** (session scratchpad, not committed): runs from before tracing was enabled were copied from
`var/results.db` and `var/traces/*.jsonl` into MLflow (same params, metrics, tags and artifacts as
`ExperimentTracker.log`) and Phoenix (spans replayed with original timestamps and parent links through
`tracer._annotate`). Copies are tagged `hf.backfilled`. MCP SDK sub-spans are not in the JSONL, so backfilled
traces lack them.

---

## 2. Concerns raised and what we found

| # | Concern | Finding | Resolution |
|---|---|---|---|
| 1 | What can be run? | `hfx` commands: `tasks`, `validate`, `reference`, `run`, `smoke`, `results [--cells]`, `baseline save/list`, `tools freeze`. Catalog: 23 tasks (3 held out), 3 recipes, 3 fault profiles, providers `scripted`, `groq`, `openrouter`. | See section 5 for the command list. |
| 2 | Is dry run available? | Yes, on `smoke` only (`--dry-run`). It prints plan counts (done / retry / new) and stops before the budget preflight. `run` has no dry run. | No change. |
| 3 | Will the factory clash with the existing containers? | The `factory` / `factory-docker` services publish no ports and start nothing else: no conflict. The supporting services clashed on 5000 (MLflow), 6006 (Phoenix) and 4317 (OTLP gRPC) with `policy-intel`. 5432 and 4318 were free. | Host ports made configurable (1.1) and moved (1.2). |
| 4 | Keep MLflow, Phoenix, Postgres and the collector available | Running on 5001, 6007, 5432, 4327/4328 next to `policy-intel`. | Done. |
| 5 | See proposed edits before they are made | — | Working agreement: changes are shown file by file and approved first. |
| 6 | "What happened to these runs?" (first time) | The commands were `--dry-run`, which records nothing. | Ran for real (section 3). |
| 7 | Where are traces? Is a `--phoenix` flag needed? | There is no such flag. JSONL traces are always written to `var/traces/`. OTLP to the collector, and on to Phoenix, is on only when `OTEL_EXPORTER_OTLP_ENDPOINT` is set. | Set in `.env` (1.2). |
| 8 | Is data kept, and is there a checkpoint, if a run stops? | Yes. Each finished run is committed to the store in its own transaction (`runner.py:189`, `store.py:208`). Ctrl-C waits for in-flight runs to clean up and flushes traces (`runner.py:315-325`). `smoke --resume` skips scored runs and reruns INCOMPLETE/ERROR (`runner.py:254-266`). The quota ledger is saved atomically after every request (`quota.py:83-96`). There is no checkpoint inside a run: an interrupted run restarts from scratch, by design (fresh sandbox per run). | Verified by tests (section 3.4). |
| 9 | Is the run metadata saved? | All 40 rows have identity, scores, cost, grades, timestamps, trace and transcript paths; the files exist. | No change. |
| 10 | Get the earlier runs into MLflow and Phoenix | Possible from the stored rows and JSONL traces. | Backfilled (3.5). |

---

## 3. Runs

### 3.1 Dry runs

`hfx smoke --provider groq --models llama-3.3-70b --dry-run [--resume]`, three times:
`plan: 20 runs  done 0  retry 0  new 20`. The 20 runs are the 23 tasks minus the 3 held out
(`docs-02`, `ops-04`, `sql-07`), times 1 recipe (`v2-base`), times 1 seed.

### 3.2 Campaign A: `llama-3.3-70b` on Groq, all ERROR

- 18:33:54 to 18:34:06. 20 runs, 20 ERROR, 0 tokens, 0 tool calls.
- Every run failed on its first model call: `GroqException ... The model 'llama-3.3-70b-versatile' does not
  exist or you do not have access to it (model_not_found)`. The gateway classified it as `CONFIG`.
- The harness behaved correctly: sandbox and MCP servers came up, and ERROR rows are excluded from cell means.
- **Root cause:** `catalog/providers.yaml` is out of date for Groq. Models the key can use on 2026-10-29:

| Catalog canonical | Catalog provider id | On Groq now? |
|---|---|---|
| `llama-3.3-70b` | `llama-3.3-70b-versatile` | No |
| `gpt-oss-120b` | `openai/gpt-oss-120b` | Yes |
| `qwen3-32b` | `qwen/qwen3-32b` | No (Groq has `qwen/qwen3.8-27b`, a different model) |

Other chat models on the key: `openai/gpt-oss-20b`, `allam-2-7b` (4k context, too small).

### 3.3 Campaign B: `gpt-oss-120b` on Groq, the Phase 1 result

`hfx smoke --provider groq --models gpt-oss-120b`, recipe `v2-base`, seed 0, concurrency 4, sandbox `local`,
no faults. 18:38:57 to 18:55:53 (about 17 minutes).

| Metric | Value |
|---|---|
| Runs | 20 |
| SUCCESS / FAIL / ERROR / INCOMPLETE | 19 / 1 / 0 / 0 |
| Mean E0 | 0.949 |
| Tokens in / out | 82,860 / 13,233 |
| Cost (list price; free tier) | $0.022 |
| Env hash | `87cf105d96ea` |
| Recipe hash (`v2-base`) | `79abd03818943f65` |

**Per task:**

| Task | Class | Status | O | Q | E0 | Model calls | Tool calls | Tokens | Wall (s) |
|---|---|---|---|---|---|---|---|---|---|
| code-01 | UNKNOWN_PATH / REVERSIBLE / R1 | **FAIL** | 0.00 | 1.00 | 0.000 | 5 | 5 | 2,443 | 65 |
| docs-01 | KNOWN_PATH / REVERSIBLE / R1 | SUCCESS | 1.00 | 1.00 | 1.000 | 5 | 4 | 3,521 | 122 |
| fs-01 | SINGLE_STEP / REVERSIBLE / R1 | SUCCESS | 1.00 | 1.00 | 1.000 | 3 | 2 | 1,176 | 8 |
| fs-02 | KNOWN_PATH / REVERSIBLE / R1 | SUCCESS | 1.00 | 1.00 | 1.000 | 4 | 3 | 1,752 | 9 |
| mix-01 | KNOWN_PATH / REVERSIBLE / R1 | SUCCESS | 1.00 | 1.00 | 1.000 | 5 | 4 | 3,093 | 114 |
| mix-02 | KNOWN_PATH / REVERSIBLE / R1 | SUCCESS | 1.00 | 1.00 | 1.000 | 8 | 7 | 6,999 | 352 |
| mix-03 | UNKNOWN_PATH / REVERSIBLE / R2 | SUCCESS | 1.00 | 1.00 | 1.000 | 9 | 8 | 8,630 | 357 |
| mix-04 | KNOWN_PATH / REVERSIBLE / R2 | SUCCESS | 1.00 | 1.00 | 1.000 | 7 | 6 | 5,884 | 240 |
| mix-05 | UNKNOWN_PATH / REVERSIBLE / R1 | SUCCESS | 1.00 | 1.00 | 1.000 | 9 | 8 | 10,350 | 420 |
| ops-01 | UNKNOWN_PATH / REVERSIBLE / R1 | SUCCESS | 1.00 | 1.00 | 1.000 | 8 | 7 | 4,757 | 301 |
| ops-02 | KNOWN_PATH / REVERSIBLE / R1 | SUCCESS | 1.00 | 1.00 | 1.000 | 3 | 2 | 1,584 | 61 |
| ops-03 | UNKNOWN_PATH / READ / R0 | SUCCESS | 1.00 | 1.00 | 1.000 | 4 | 3 | 4,276 | 122 |
| res-01 | UNKNOWN_PATH / READ / R0 | SUCCESS | 1.00 | 1.00 | 1.000 | 8 | 7 | 4,242 | 240 |
| sql-01 | SINGLE_STEP / READ / R0 | SUCCESS | 1.00 | 1.00 | 1.000 | 4 | 3 | 1,612 | 121 |
| sql-02 | KNOWN_PATH / REVERSIBLE / R2 | SUCCESS | 1.00 | 1.00 | 1.000 | 5 | 4 | 2,914 | 119 |
| sql-03 | UNKNOWN_PATH / READ / R0 | SUCCESS | 1.00 | 1.00 | 1.000 | 8 | 7 | 6,890 | 361 |
| sql-04 | KNOWN_PATH / READ / R0 | SUCCESS | 1.00 | 1.00 | 1.000 | 5 | 4 | 3,178 | 179 |
| sql-05 | UNKNOWN_PATH / REVERSIBLE / R2 | SUCCESS | 1.00 | 1.00 | 1.000 | 9 | 8 | 9,589 | 361 |
| sql-06 | KNOWN_PATH / REVERSIBLE / R2 | SUCCESS | 1.00 | 0.89 | 0.972 | 10 | 9 | 8,799 | 300 |
| sql-08 | KNOWN_PATH / READ / R0 | SUCCESS | 1.00 | 1.00 | 1.000 | 6 | 5 | 4,404 | 121 |

**Per cell** (`hfx results --cells`, live baseline `3ab95ffdd430`):

| Task class | n | Success | E0 | C | E |
|---|---|---|---|---|---|
| KNOWN_PATH / READ / R0 | 2 | 100% | 1.000 | 0.50 | 0.875 |
| KNOWN_PATH / REVERSIBLE / R1 | 5 | 100% | 1.000 | 0.50 | 0.925 |
| KNOWN_PATH / REVERSIBLE / R2 | 3 | 100% | 0.991 | 0.50 | 0.916 |
| SINGLE_STEP / READ / R0 | 1 | 100% | 1.000 | 0.50 | 0.875 |
| SINGLE_STEP / REVERSIBLE / R1 | 1 | 100% | 1.000 | 0.50 | 0.925 |
| UNKNOWN_PATH / READ / R0 | 3 | 100% | 1.000 | 0.50 | 0.875 |
| UNKNOWN_PATH / REVERSIBLE / R1 | 3 | 67% | 0.667 | 0.67 | 0.617 |
| UNKNOWN_PATH / REVERSIBLE / R2 | 2 | 100% | 1.000 | 0.50 | 0.925 |

C is about 0.50 everywhere because the cost baseline holds only this model, compared with itself. R is empty
because no faulted runs were made. Neither means anything until a second model or recipe and a faulted run exist.

**Findings:**

1. **`code-01` failed because the model called a tool that does not exist.** It explored well (listed the root,
   read `pyproject.toml`, listed `service/`, read `service/__init__.py`, listed `docs/`), found both
   declarations of 2.3.1, then called `fs__search_file`. Only `fs__list_dir`, `fs__read_file` and
   `fs__write_file` were offered. Groq rejected the call (`tool_use_failed`), the gateway classified it as
   `MODEL_PARSE` (the model's fault), and the run ended with the workspace unchanged: both oracle checks failed,
   O = 0, Q = 1.0. This is a model weakness, not infrastructure. With a single seed we cannot tell whether it
   repeats.
2. **`sql-06`:** correct end state; 1 of 9 tool calls counted as wasted, so Q = 0.89 and E0 = 0.972.
3. **Wall time is mostly quota waiting.** Groq's free tier allows 6,000 tokens per minute; 96k tokens at that
   rate is about 16 minutes, which matches the campaign. Per-run wall time (8 s to 420 s) measures queueing,
   not model speed.
4. **Harder classes cost more.** UNKNOWN_PATH / REVERSIBLE tasks averaged about 8 model calls and 7,000 tokens,
   against about 3 calls for SINGLE_STEP.

Groq usage for the day after both campaigns: 146 requests, 96,093 tokens (ledger `var/quota_ledger.json`).

### 3.4 Test check: data retention and resume

`uv run pytest tests/test_resume.py` plus the cancellation and resume tests from `test_isolation.py`,
`test_mcp_servers.py`, `test_gateway_litellm.py` and `test_faults.py`: **12 passed, 1 failed.**

| Test | Result |
|---|---|
| `test_resume_skips_what_is_done` | pass |
| `test_quota_interrupted_runs_are_finished_tomorrow` | pass |
| `test_an_interrupted_campaign_resumes_to_the_same_result` | pass |
| `test_rerunning_without_resume_supersedes_rather_than_double_counts` | pass |
| `test_ledger_usage_survives_a_restart` | pass |
| `test_persistent_provider_failure_is_incomplete_recorded_and_unscored` | pass |
| `test_resume_tells_clean_and_faulted_runs_apart` | pass |
| `test_cancelling_during_sandbox_start_leaks_nothing` | pass |
| `test_cancelling_while_sessions_open_leaves_no_server_running` | **fail on Windows** |

The failure is in the test, not the product: `_child_servers()` in `tests/test_mcp_servers.py:61` shells out to
Linux `ps`, which does not exist on Windows. The behaviour it checks is unverified on Windows; it should pass
in Linux CI.

### 3.5 Backfill to MLflow and Phoenix

| Target | Result |
|---|---|
| MLflow (`http://localhost:5001`, experiment `harness-factory`) | 40 runs (both campaigns), each with recipe params, metrics (outcome, trajectory, composite, tokens, tool calls, usd, wall), tags (`status`, `run_key`, `hf.row_id`, `hf.backfilled`), and trace + transcript artifacts |
| Phoenix (`http://localhost:6007`, project `default`) | 332 spans with original timestamps and parent/child links |

The collector logged 41 export failures, all from its Langfuse exporter (no Langfuse is running). That does not
affect Phoenix.

---

## 4. Phase 1 baseline

| | |
|---|---|
| Model / provider | `gpt-oss-120b` / Groq (`openai/gpt-oss-120b`) |
| Recipe | `v2-base` (V2 tool loop, tier T2, `max_steps` 15) |
| Tasks | 20 (DEV + TEST, held-out excluded), 1 seed, no faults |
| Pass rate | 95% (19/20) |
| Mean E0 | 0.949 |
| Tokens | 96,093 |
| Only failure | `code-01`: invented tool name |

To freeze this as the cost reference for Phase 2: `uv run hfx baseline save --note "phase 1"`.

---

## 5. Commands used

```powershell
uv sync --extra dev --extra mlops
docker compose up -d                                              # postgres, mlflow, phoenix, otel-collector
uv run hfx smoke --provider groq --models gpt-oss-120b --dry-run
uv run hfx smoke --provider groq --models gpt-oss-120b            # add --resume after any interruption
uv run hfx results --limit 50
uv run hfx results --cells
```

---

## 6. Open items for Phase 2

**Fix before the next campaign**

1. **`.env` tracking endpoints.** On 2026-10-29 the local `.env` still had `OTEL_EXPORTER_OTLP_ENDPOINT=...:4318`
   and `MLFLOW_TRACKING_URI=...:5000`. With these, spans are dropped and runs are logged into **policy-intel's**
   MLflow. Set them as in 1.2.
2. **`catalog/providers.yaml`, Groq section.** Remove or replace `llama-3.3-70b` and `qwen3-32b`; optionally add
   `gpt-oss-20b` and `qwen3.8-27b` after checking prices and limits on Groq's docs. This changes `env_hash`, so
   new models start fresh, which is the intended behaviour.

**Measurement**

3. More seeds for confidence intervals: `--seeds 3 --resume` (seed 0 is skipped). About 35 minutes and 190k
   tokens on the free tier.
4. Shape comparison so C and E mean something: `--recipe v1-base,v2-base,v3-staged`.
5. Robustness: `--faults none,first-call-fails`.
6. A second model (e.g. `gpt-oss-20b` once in the catalog, or OpenRouter at 50 requests a day).
7. Held-out tasks (`docs-02`, `ops-04`, `sql-07`) for a final evaluation.

**Tooling**

8. `tests/test_mcp_servers.py::_child_servers` uses Linux `ps`; make it cross-platform (e.g. `psutil`) or mark
   it Linux-only.
9. `compose.yaml` mounts `${PWD}/var` at the same path inside the container. On Windows that resolves to a
   Windows path inside a Linux container and will probably fail; `docker compose run factory` is untested on
   Windows. Host `uv run hfx` works.
10. Optionally commit a backfill command (results store and JSONL to MLflow and Phoenix) so a run made with
    tracing off can be published later.
11. Langfuse: run its stack or remove the exporter from `observability/collector.yaml` to stop the export errors.
12. `--dry-run` does not show the worst-case request count that the budget preflight uses; worth adding.
