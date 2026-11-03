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
| `session_01BgTbLJZLSKyEhjaA4sxi9R` | `session:core` | Shapes, measurement core, task batch 3, P2 (calibration, allocation), runners, framework fidelity |
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
| #49 | `feat/rubric-judge` | `…KhAFMPUEPRU3mK2t2` | Rubric judge with calibration | ready for review (CI green), stacked on #48 | scoring for tasks using it |
| #50 | `feat/context-management` | `…KhAFMPUEPRU3mK2t2` | Token accounting, output spill, compaction | ready for review (CI green) | new optional recipe field |
| #51 | `feat/shape-depth` | `…KhAFMPUEPRU3mK2t2` | Structured plans, parallel tool calls, concurrent V5 workers | ready for review, stacked on #50 | new optional recipe fields |
| #52 | `feat/task-batch-4` | `…KhAFMPUEPRU3mK2t2` | Long, data-heavy tasks by an independent author | draft, reserved | new tasks only |
| #69 | `fix/grounded-check` | `…KhAFMPUEPRU3mK2t2` | Grounded check requires touching the task's data (R-01 follow-up) | draft, reserved (wave 5) | yes (scoring; tasks whose check gains a field) |
| #70 | `feat/fault-kinds` | `…KhAFMPUEPRU3mK2t2` | Fault kinds: latency and malformed results (ENV-03) | draft, reserved (wave 5) | new fault profiles only |
| #71 | `feat/perturbations` | `…KhAFMPUEPRU3mK2t2` | Perturbation generator: variants by type (TSK-04) | draft, reserved (wave 5) | new tasks only |
| #72 | `feat/env-web-corpus` | `…KhAFMPUEPRU3mK2t2` | Web search and fetch over a recorded, versioned corpus (ENV-01, KNW-01; ADR 0018) | draft, reserved (wave 5) | new server; corpus id in env_hash |
| #73 | `feat/env-outbox-calendar` | `…KhAFMPUEPRU3mK2t2` | Email/notification outbox and calendar servers (ENV-01) | draft, reserved (wave 5) | new servers only |
| #74 | `feat/mcp-conformance` | `…KhAFMPUEPRU3mK2t2` | MCP conformance suite in CI and server health before runs (MCP-11, ENV-06) | draft, reserved (wave 5) | no |
| #75 | `feat/approval-gate` | `…KhAFMPUEPRU3mK2t2` | Approval gate and scripted approver; POLICY_BLOCKED (GOV-01, GOV-03; ADR 0019) | draft, reserved (wave 6) | run status logic (recipes using ASK) |
| #76 | `feat/clarification` | `…KhAFMPUEPRU3mK2t2` | Clarification: NEEDS_INPUT in headless runs, scripted user, elicitation (SHP-06, MCP-06) | draft, reserved (wave 6) | run status logic; new optional recipe field |
| #77 | `feat/guardrails` | `…KhAFMPUEPRU3mK2t2` | Guardrails, data tagging and trace redaction (GOV-02, GOV-05, GOV-06) | draft, reserved (wave 6) | new optional recipe field |
| #78 | `feat/memory` | `…KhAFMPUEPRU3mK2t2` | Memory stores, write policy and memory profiles (MEM-01..05; ADR 0020) | draft, reserved (wave 6) | new optional recipe field |
| #79 | `feat/task-profiler` | `…KhAFMPUEPRU3mK2t2` | Task profiler: 10-dimension profiles, rules + LLM profiler, agreement (PRF-01, 03-05) | draft, reserved (wave 6) | no (profiles are labels) |
| #80 | `feat/recipe-generator` | `…KhAFMPUEPRU3mK2t2` | Recipe generator and logged hard-rule pruning (RCP-04, RUN-02 campaign) | draft, reserved (wave 6) | new recipes only |
| #81 | `feat/self-metrics` | `…KhAFMPUEPRU3mK2t2` | Self-metrics and compressed traces with retention (NFR-08, NFR-09) | draft, reserved (wave 6) | no |
=======
| #34 | `feat/task-batch-3` | `…JZLSKyEhjaA4sxi9R` | Task batch 3, written by an independent author | closed by GitHub after the branch was re-signed; continued in #82 | yes (new tasks and seeds) |
| #35 | `feat/provider-calibration` | `…JZLSKyEhjaA4sxi9R` | Cross-provider equivalence calibration (P2) | ready for review | no (two nullable run columns, one table) |
| #36 | `feat/allocation-planner` | `…JZLSKyEhjaA4sxi9R` | Allocation planner (P2) | ready for review (stacked on #35; CI green) | no |
| #37 | `docs/test-matrix-notes` | `…JZLSKyEhjaA4sxi9R` | Test-matrix design notes, benchmark example | ready for review | no |
| #53 | `feat/runner-axis` | `…JZLSKyEhjaA4sxi9R` | Runner axis: LangGraph prebuilt, LlamaIndex agent | ready for review (stacked on #35; CI green) | no (new optional recipe field) |
| #54 | `feat/runner-ms-agent-framework` | `…JZLSKyEhjaA4sxi9R` | Microsoft Agent Framework runner | ready for review (stacked on #53; CI green) | no |
| #55 | `feat/runner-sk-autogen` | `…JZLSKyEhjaA4sxi9R` | Semantic Kernel and AutoGen runners | closed by GitHub after the branch was re-signed; continued in #83 | no |
| #56 | `feat/runner-more` | `…JZLSKyEhjaA4sxi9R` | LangGraph supervisor, Pydantic AI, OpenAI Agents SDK runners (CrewAI left out: pins mcp<2) | ready for review (stacked on #53; CI green) | no |
| #57 | `feat/retrieval-llamaindex` | `…JZLSKyEhjaA4sxi9R` | Retrieval dimension on LlamaIndex | closed by GitHub after the branch was re-signed; continued in #84 | no (new server only) |
| #58 | `feat/gateway-litellm-router` | `…JZLSKyEhjaA4sxi9R` | LiteLLM Router inside ModelGateway | ready for review (CI green) | no |
| #59 | `feat/tool-call-emulation` | `…JZLSKyEhjaA4sxi9R` | Tool-call emulation with instructor | ready for review (CI green) | no |
| #60 | `feat/langgraph-checkpointer` | `…JZLSKyEhjaA4sxi9R` | LangGraph checkpointer: pause and resume | ready for review (CI green) | no |
| #61 | `feat/bench-file` | `…JZLSKyEhjaA4sxi9R` | `hfx bench` and recipe-vs-recipe report | ready for review (stacked on #37; CI green) | no |
| #62 | `feat/selector-pooled-cells` | `…JZLSKyEhjaA4sxi9R` | Selector on calibrated pooled cells | ready for review (stacked on #35; CI green) | no |
| #63 | `feat/analysis-shape-by-model` | `…JZLSKyEhjaA4sxi9R` | Analysis: shape × model by task class | closed by GitHub after the branch was re-signed; continued in #85 | no |
| #64 | `chore/storage-alembic` | `…JZLSKyEhjaA4sxi9R` | Alembic migrations, Postgres in CI | ready for review (CI green) | no |
| #65 | `chore/langfuse-compose` | `…JZLSKyEhjaA4sxi9R` | Langfuse in compose | ready for review (CI green) | no |
| #66 | `feat/promptfoo-smoke` | `…JZLSKyEhjaA4sxi9R` | promptfoo prompt checks | closed by GitHub after the branch was re-signed; continued in #86 | no |
| #67 | `docs/handoff-packages` | `…JZLSKyEhjaA4sxi9R` | Handover guide, work packages, requirements snapshot | ready for review | no |
| #82 | `feat/task-batch-3` | `…JZLSKyEhjaA4sxi9R` | Task batch 3 (replaces #34) | ready for review | yes (new tasks and seeds) |
| #83 | `feat/runner-sk-autogen` | `…JZLSKyEhjaA4sxi9R` | AutoGen runner, migration check vs Agent Framework (Semantic Kernel left out: pins pydantic<2.14, mcp<2) (replaces #55) | ready for review (stacked on #54; CI green) | no |
| #84 | `feat/retrieval-llamaindex` | `…JZLSKyEhjaA4sxi9R` | Retrieval dimension on LlamaIndex (replaces #57) | ready for review (CI green) | no (unset recipes and existing env hashes unchanged) |
| #85 | `feat/analysis-shape-by-model` | `…JZLSKyEhjaA4sxi9R` | Analysis: shape × model by task class, crossover by depth (replaces #63) | ready for review (CI green) | no |
| #86 | `feat/promptfoo-smoke` | `…JZLSKyEhjaA4sxi9R` | promptfoo prompt checks (replaces #66) | ready for review (CI green) | no |


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
| 0018 | `…KhAFMPUEPRU3mK2t2` | Recorded corpora and their versioning (in #72) |
| 0019 | `…KhAFMPUEPRU3mK2t2` | Approval gate and scripted approver (in #75) |
| 0020 | `…KhAFMPUEPRU3mK2t2` | Memory stores and the write policy (in #78) |
=======
| 0010 | `…JZLSKyEhjaA4sxi9R` | Cross-provider equivalence calibration (in #35) |
| 0011 | `…JZLSKyEhjaA4sxi9R` | Allocation planner (in #36) |
| 0016 | `…JZLSKyEhjaA4sxi9R` | Runner axis (in #53) |
| 0017 | `…JZLSKyEhjaA4sxi9R` | Retrieval on LlamaIndex (in #84, was #57) |

Handing a reserved PR to another session: `handoff.md` (protocol, starter prompt, one work package per PR).

