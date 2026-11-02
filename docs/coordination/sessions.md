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

Shared files the #29–#32 stack touches (append-only): `README.md` (new sections before *Real models*, three
*Layout* lines), `CLAUDE.md` (one row at the end of *Where things go*), `src/harness_factory/cli/main.py` (two
lines at the end), `.github/workflows/ci.yml` (`--extra api --extra ui` on the test install), `pyproject.toml` +
`uv.lock` (extras `api`, `ui`), `compose.yaml` (service `api`), `.env.example` (`HF_API_TOKEN`, `HF_API_PORT`).

## ADRs

| ADR | Session | Subject |
|---|---|---|
| 0008 | `…JZLSKyEhjaA4sxi9R` | V5 orchestrator (in #28) |
| 0009 | `…JtRe49bgfAAmdaB6W` | Authoring layer: one service behind CLI, API and UI |
