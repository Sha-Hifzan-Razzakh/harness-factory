# Parallel sessions: PR and ADR ledger

Several Claude sessions can work on this repo at the same time. This file says which session owns which PR,
branch and ADR number, so they don't collide. Read it before opening a PR or writing an ADR.

## Rules

1. **Claim before you build.** GitHub assigns PR numbers in order (issues share the same sequence) and they
   can't be reserved any other way. To claim numbers, push a branch with an empty commit and open it as a
   **draft** PR straight away, then add a row below. Drop the empty commit before marking the PR ready.
2. **Label every PR** `session:<name>` (create the label once per session) and put the session URL in the PR body.
3. **Edit only your own rows.** Ledger changes go in through your own PR. Never push to, rebase, retitle or
   close another session's branch or PR.
4. **ADR numbers are claimed here,** in the table below, before the file is written.
5. **Shared files** (`README.md`, `CLAUDE.md`, `src/harness_factory/cli/main.py`, `.github/workflows/ci.yml`,
   `pyproject.toml`, `uv.lock`, `compose.yaml`): add new sections or rows; don't reword or reorder existing text.
   New CLI commands go in their own module, registered with one line in `cli/main.py`.
6. **Hash impact is declared.** If a PR changes anything that goes into a hash (recipes, prompts, skills,
   tasks, seeds, `tools.yaml`, `recipes/model.py`), say so in the "Hash impact" column.
7. **Merge order is the user's call.** Whichever PR merges second rebases onto `main` and re-runs CI.

## Sessions

| Session | Label | Scope |
|---|---|---|
| `session_01BgTbLJZLSKyEhjaA4sxi9R` | (to be added by that session) | V5 orchestrator shape |
| `session_01BgTbLJtRe49bgfAAmdaB6W` | `session:authoring` | Authoring core, CLI, API, UI for tools, skills, prompts, recipes, faults |
| `session_01BgTbLKhAFMPUEPRU3mK2t2` | `session:review` | Review of `main` (#38) and its work plan: scoring fixes, environment, graders, harness depth, task batch 4 |

Session URLs: `https://claude.ai/code/<session id>`.

## PRs

| PR | Branch | Session | Scope | Status | Hash impact |
|---|---|---|---|---|---|
| #28 | `feat/v5-orchestrator` | `…JZLSKyEhjaA4sxi9R` | V5 orchestrator shape | merged | yes (new recipe field, base recipe `v5-orchestrator`) |
| #29 | `feat/authoring-core` | `…JtRe49bgfAAmdaB6W` | Authoring service + `hfx new` / `hfx catalog` CLI (stack 1/4, base `main`) | ready for review (CI green) | no (writes catalog files; existing hashes unchanged) |
| #30 | `feat/api` | `…JtRe49bgfAAmdaB6W` | FastAPI catalog + results endpoints, `hfx serve`, compose service (stack 2/4, base #29) | ready for review (CI green) | no |
| #31 | `feat/api-campaigns` | `…JtRe49bgfAAmdaB6W` | Background campaigns over the API (start, progress, cancel, resume) (stack 3/4, base #30) | ready for review (CI green) | no |
| #32 | `feat/ui` | `…JtRe49bgfAAmdaB6W` | Streamlit UI for authoring, runs and results (stack 4/4, base #31) | ready for review (CI green) | no |
| #33 | `chore/session-ledger` | `…JtRe49bgfAAmdaB6W` | This ledger + CLAUDE.md pointer | open | no |
| #34 | `feat/task-batch-3` | `…JZLSKyEhjaA4sxi9R` (presumed; that session confirms its own row) | Task batch 3, written by an independent author | open | yes (new tasks and seeds) |
| #38 | `docs/review-2026-10-30` | `…KhAFMPUEPRU3mK2t2` | Review doc + this session's ledger rows | ready for review | no |
| #39 | `fix/answer-checks` | `…KhAFMPUEPRU3mK2t2` | Answer checks refuse hedged answers (R-01) | ready for review (CI green) | yes (6 task hashes: sql-01/03/04/08, ops-03, res-01 → v2) |
| #40 | `fix/run-endings` | `…KhAFMPUEPRU3mK2t2` | Run endings: child outage, V4 step cap, V5 budget stop, child wall clock (R-02, R-03, R-10, R-11) | ready for review (CI green) | no hash; run status logic changes |
| #41 | `fix/aggregation` | `…KhAFMPUEPRU3mK2t2` | Aggregation: R·O, scored rows win, cost median, guard outcome, task_hash in cells (R-04..R-07, O-01, L-11, L-12) | ready for review (CI green) | no hash; aggregation changes |
| #42 | `fix/tool-identity` | `…KhAFMPUEPRU3mK2t2` | Destructive-SQL escalation, description hashes, remote version pin, replay scope (R-08, R-09, O-04, L-05..L-07) | ready for review (CI green) | yes (every env_hash once; `v5-orchestrator` recipe hash) |
| #43 | `fix/ops-hardening` | `…KhAFMPUEPRU3mK2t2` | MLflow off the loop, grader-id checks, quota ledger lock, CLI and fs edge cases (O-02, O-03, O-05, O-06, L-01..L-04, L-08..L-10) | ready for review (CI green) | fault profile `garbled-reads` only |
| #44 | `feat/fs-edit-tools` | `…KhAFMPUEPRU3mK2t2` | `fsx` server: edit, append, move, mkdir, tree | ready for review (CI green), stacked on #42 | new server only |
| #45 | `feat/env-code-git` | `…KhAFMPUEPRU3mK2t2` | Code runner + git servers, `tests_pass` check | ready for review (CI green), stacked on #44 | new servers; sandbox image tag |
| #46 | `feat/env-business-api` | `…KhAFMPUEPRU3mK2t2` | Mock business API server (CRM, invoicing, ticketing) | ready for review (CI green), stacked on #45 | new server only |
| #47 | `feat/env-docs` | `…KhAFMPUEPRU3mK2t2` | Documents server (docx, xlsx, pdf) | ready for review (CI green), stacked on #46 | new server; sandbox image tag |
| #48 | `feat/graders-depth` | `…KhAFMPUEPRU3mK2t2` | Unordered rows, number tolerance, JSON schema, grader versions, re-grading | ready for review (CI green), stacked on #41 | scoring only for tasks adopting new kinds |
| #49 | `feat/rubric-judge` | `…KhAFMPUEPRU3mK2t2` | Rubric judge with calibration | draft, reserved | scoring for tasks using it |
| #50 | `feat/context-management` | `…KhAFMPUEPRU3mK2t2` | Token accounting, output spill, compaction | draft, reserved | new optional recipe field |
| #51 | `feat/shape-depth` | `…KhAFMPUEPRU3mK2t2` | Structured plans, parallel tool calls, concurrent V5 workers | draft, reserved | new optional recipe fields |
| #52 | `feat/task-batch-4` | `…KhAFMPUEPRU3mK2t2` | Long, data-heavy tasks by an independent author | draft, reserved | new tasks only |

Shared files the #29–#32 stack touches (append-only): `README.md` (new sections before *Real models*, three
*Layout* lines), `CLAUDE.md` (one row at the end of *Where things go*), `src/harness_factory/cli/main.py` (two
lines at the end), `.github/workflows/ci.yml` (`--extra api --extra ui` on the test install), `pyproject.toml` +
`uv.lock` (extras `api`, `ui`), `compose.yaml` (service `api`), `.env.example` (`HF_API_TOKEN`, `HF_API_PORT`).

## ADRs

| ADR | Session | Subject |
|---|---|---|
| 0008 | `…JZLSKyEhjaA4sxi9R` | V5 orchestrator (in #28) |
| 0009 | `…JtRe49bgfAAmdaB6W` | Authoring layer: one service behind CLI, API and UI |
| 0012 | `…KhAFMPUEPRU3mK2t2` | Running code in the sandbox (in #45) |
| 0013 | `…KhAFMPUEPRU3mK2t2` | Non-stdlib MCP server dependencies (in #47) |
| 0014 | `…KhAFMPUEPRU3mK2t2` | LLM judge and its calibration (in #49) |
| 0015 | `…KhAFMPUEPRU3mK2t2` | Context management (in #50) |
