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
| 0010 | `…JZLSKyEhjaA4sxi9R` | Cross-provider equivalence calibration (in #35) |
| 0011 | `…JZLSKyEhjaA4sxi9R` | Allocation planner (in #36) |
| 0016 | `…JZLSKyEhjaA4sxi9R` | Runner axis (in #53) |
| 0017 | `…JZLSKyEhjaA4sxi9R` | Retrieval on LlamaIndex (in #84, was #57) |

Handing a reserved PR to another session: `handoff.md` (protocol, starter prompt, one work package per PR).
