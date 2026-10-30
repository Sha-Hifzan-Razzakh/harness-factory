# Handing a reserved PR to another session

Any Claude session can build any reserved PR. This file says what the new session needs and how the PR changes
hands, then gives one work package per reserved PR, with checkboxes to complete. Read `sessions.md` (the ledger)
first: its rules still apply.

## 1. How a PR changes hands

A session never pushes to another session's branch (ledger rule 3). So the handover is written down before the
new session starts:

- [ ] **The user picks the package** below and starts a new session on this repo.
- [ ] **The transfer is recorded** by the user, the old owner or the new session at the user's request:
  - the ledger row's *Session* column names the new session (a PR that edits only that row);
  - the PR gets the new session's `session:<name>` label in place of the old one;
  - the PR body gains one line: `Owner: <new session URL> (handed over from <old session URL>, <date>)`.
- [ ] **From then on** the branch belongs to the new session. It may rebase it, drop the placeholder commit and
  force-push. The old session no longer touches it.
- [ ] **When done**, the new session marks the PR ready and updates its ledger row's *Status*.

## 2. What the new session needs to know

Most of this is already in the repo; the starter prompt points to it.

| What | Where |
|---|---|
| Working rules, where things go, rules that protect the measurements | `CLAUDE.md` |
| How the factory works end to end | `README.md` |
| Parallel-session rules, who owns what, claimed ADR numbers | `docs/coordination/sessions.md` |
| This package's scope, files, acceptance criteria | §4 below, and the PR's own description |
| Design decisions to keep | `docs/adr/` (read the ones the package names) |
| Requirement IDs (RUN-, FWK-, SUB-, …) | `docs/requirements/requirements-v1.md` (a snapshot; the source of truth is the Requirements v1 doc, https://claude.ai/artifact/1pWpX5ui9KmP751n8KvaTz) |
| Test-matrix and runner design notes | `docs/design/test-matrix.md` (PR #37 until it merges) |
| Open review findings that may touch the same code | `docs/reviews/2026-10-30-code-and-depth-review.md` (PR #38) |

**Before starting parallel sessions,** merge the docs PRs they read (#67 this file and the requirements snapshot,
#37 the test-matrix notes, #38 the review), or tell each session to read them from their branches
(`git show origin/<branch>:<path>`). Every session also needs this GitHub repo attached with push access.

What the repo can't tell it, so the prompt says it:

- **Git identity and attribution.** Commit as the repo owner (`git -c user.name=… -c user.email=…`, values from the
  user). Commit messages and PR bodies end with the attribution lines that session's own instructions give.
- **Secrets.** Only in `.env` or the environment. Never in code, tests, traces or PR text.
- **Offline first.** The build and every test must pass with the `scripted` provider. Real API keys aren't needed
  and must not be assumed.

## 3. Starter prompt for a new session

Paste this into the new session, with the package number filled in:

```text
Repo: Sha-Hifzan-Razzakh/harness-factory. You are taking over reserved PR #<N> (branch <branch>).

1. Read CLAUDE.md, docs/coordination/sessions.md, docs/coordination/handoff.md (§4, package #<N>) and the
   requirement IDs it names in docs/requirements/requirements-v1.md. (Not on main yet? Read them from branch
   docs/handoff-packages with git show.)
2. Record the handover (handoff.md §1): ledger row, label session:<your name>, the Owner line in the PR body.
3. Check out the PR's branch, drop the "[skip ci] Claim:" placeholder commit, rebase on main.
4. Build the package to its acceptance criteria. One concern only; append-only edits to shared files.
5. Run: uv run ruff check src tests; uv run pytest -q -n auto; uv run hfx validate; uv run hfx reference;
   uv run hfx smoke --models scripted/replay --strict (plus any gate the package names).
6. Write the PR body (what, hash/scoring impact, tests, docs), mark it ready, update the ledger Status.
Commit as <name> <email>. Never push to a branch another session owns.
```

## 4. Work packages

Every package lists:
- **Goal**: what changes for a user of the factory.
- **Scope**: what to build, and what not to.
- **Requirements**: the requirement IDs it closes.
- **Files**: where the work goes; *shared* files take append-only edits.
- **Impact**: on hashes and scores. Say it again in the PR body.
- **ADR**: the number to use, if the package claims one.
- **Depends on**: what must merge first.
- **Done when**: the acceptance checks.

The general gates in §3 step 5 apply to every package as well.

### #36 Allocation planner (`feat/allocation-planner`)

- **Goal:** for a campaign, decide which provider serves each cell, from quotas, projections and calibration.
- **Scope:** a planner that assigns (model, cell) to a provider from `catalog/providers.yaml` limits, the
  projection (MOD-05) and calibration verdicts (ADR 0010). It may split runs across providers only when they are
  calibrated `equivalent`. It writes the allocation with the campaign and logs every decision. Not in scope:
  re-planning mid-campaign (MOD-07).
- **Requirements:** MOD-04, MOD-06 (use of calibration).
- **Files:** `src/harness_factory/runner/` (new module), CLI in its own module; shared `cli/main.py` (one line),
  `README.md` (new section).
- **Impact:** none on hashes; the RunKey keeps its provider.
- **ADR:** 0011 (claimed).
- **Depends on:** #35.
- **Done when:**
  - [ ] offline test: two scripted providers, with and without an `equivalent` verdict, give the expected split;
  - [ ] a plan over quota refuses with the reason, as the projection does;
  - [ ] ADR 0011 written.

### #53 Runner axis: LangGraph prebuilt and LlamaIndex agent (`feat/runner-axis`)

- **Goal:** the same recipe runs under a framework's own agent, so the factory measures what you would deploy.
- **Scope:**
  - an optional `runtime` recipe field: `native` is the default and is left out of the hash;
  - a runner interface;
  - two runners: LangGraph's ready-made agent, which gets its tools through langchain-mcp-adapters, and a
    LlamaIndex `FunctionAgent` or `AgentWorkflow`, which gets them through LlamaIndex's MCP tool spec;
  - only the model client (into `ModelGateway`) and the tool callables (into `ToolExecutor`) are wrapped;
  - framework prompts and defaults stay as they are;
  - the runner and its framework version go into the run's identity;
  - a transfer check reuses the ADR 0010 rule.

  Not in scope: other frameworks (#54–#56).
- **Requirements:** RUN-01 to RUN-09, FWK-01 to FWK-04.
- **Files:** `src/harness_factory/runners/` (new), `recipes/model.py` (new optional field, in
  `_HASH_OMIT_WHEN_NONE`), `runner/spec.py` or the identity code, `pyproject.toml` + `uv.lock` (optional extras
  `langgraph-prebuilt`, `llamaindex`); shared `CLAUDE.md` (new row), `README.md` (new section), `ci.yml` (a runner
  gate line).
- **Impact:** existing recipe and env hashes unchanged (test it); runner runs get new identities.
- **ADR:** 0016 (claimed).
- **Depends on:** #35.
- **Done when:**
  - [ ] scripted models drive both runners offline, all P0 tasks reach SUCCESS;
  - [ ] a test proves every model call of a runner run goes through `ModelGateway` (counted in the ledger) and
    every tool call through `ToolExecutor` (spans, permissions, faults);
  - [ ] a test proves the framework's own agent class is used, not a loop of ours;
  - [ ] `v2-base` hash unchanged; a framework version change changes the runner run's identity;
  - [ ] transfer check command and README section; ADR 0016.

### #54 Microsoft Agent Framework runner (`feat/runner-ms-agent-framework`)

- **Goal:** benchmark recipes on Microsoft Agent Framework (1.0 GA, the successor to Semantic Kernel and AutoGen).
- **Scope:** a runner on its own agent, with a custom chat client into `ModelGateway` and its built-in MCP support,
  wrapped so tool calls go through `ToolExecutor`. Check its extension points against its current docs first.
- **Requirements:** RUN-10, FWK-01 to FWK-04.
- **Files:** `src/harness_factory/runners/` (one module), optional extra in `pyproject.toml` + `uv.lock`.
- **Impact:** none outside runner runs.
- **ADR:** none (follows 0016).
- **Depends on:** #53.
- **Done when:**
  - [ ] the same offline checks as #53, for this runner;
  - [ ] a transfer check against `native` on the P0 tasks runs offline.

### #55 Semantic Kernel and AutoGen runners (`feat/runner-sk-autogen`)

- **Goal:** benchmark for teams already on Semantic Kernel or AutoGen, and for migrating to Agent Framework.
- **Scope:** two runners on each framework's own agent (SK v1.x agents; AutoGen AgentChat 0.4+, in maintenance
  mode), with each framework's chat-client and MCP seams. A paired comparison of each against #54 on the same
  recipe.
- **Requirements:** RUN-10, RUN-11.
- **Files:** `src/harness_factory/runners/` (two modules), optional extras.
- **Impact:** none outside runner runs.
- **ADR:** none.
- **Depends on:** #53, #54.
- **Done when:**
  - [ ] the #53 offline checks for both runners;
  - [ ] a documented migration comparison command (AutoGen vs Agent Framework, SK vs Agent Framework).

### #56 More runners: LangGraph supervisor, CrewAI, OpenAI Agents SDK, Pydantic AI (`feat/runner-more`)

- **Goal:** cover the other frameworks teams commonly deploy on.
- **Scope:** one runner per framework, each on its own agent; LangGraph supervisor as a V5-style runner. Check each
  framework's current docs for the model-client and MCP seams.
- **Requirements:** RUN-10.
- **Files:** `src/harness_factory/runners/` (one module each), optional extras.
- **Impact:** none outside runner runs.
- **ADR:** none.
- **Depends on:** #53.
- **Done when:**
  - [ ] the #53 offline checks for every runner added;
  - [ ] a framework whose seams don't allow routing through `ModelGateway` is left out and named in the PR.

### #57 Retrieval on LlamaIndex (`feat/retrieval-llamaindex`)

- **Goal:** the `retrieval` recipe field works, so retrieval strategies can be compared.
- **Scope:**
  - a recorded corpus;
  - a retrieval MCP server built with LlamaIndex: a Qdrant vector index (in-memory or local offline), a BM25
    retriever, and a reranker;
  - `retrieval.mode` none / bm25 / dense / hybrid, and `rerank`;
  - a matrix recipe and research tasks that need retrieval, written by an author who hasn't seen the recipes;
  - never hand-written retrieval code.

  Note the sandbox rule: MCP servers may import only `mcp` and the standard library. Follow ADR 0013 (#47) for
  non-stdlib server dependencies, or run retrieval in the factory like remote servers (ADR 0004).
- **Requirements:** KNW-01 to KNW-05 (part), FWK-01.
- **Files:** a new server or remote stand-in, `catalog/tools.yaml` (new server only, refreeze),
  `recipes/validate.py` (stop refusing `retrieval`), new matrix recipe, tasks.
- **Impact:** new server and tools only; existing env hashes unchanged.
- **ADR:** 0017 (claimed).
- **Depends on:** #47's ADR 0013 decision, if the server runs in the sandbox.
- **Done when:**
  - [ ] offline: deterministic embeddings (or a recorded index) so CI needs no model;
  - [ ] each mode returns the expected passages on a fixture corpus;
  - [ ] a matrix recipe passes the CI matrix gate; ADR 0017.

### #58 LiteLLM Router inside ModelGateway (`feat/gateway-litellm-router`)

- **Goal:** model calls use the planned LiteLLM Router, not one-off `acompletion` calls.
- **Scope:** `litellm.Router` with a deployment per provider and rpm/tpm limits, inside `ModelGateway`. The
  QuotaLedger stays authoritative for pacing. Error classification stays only in `classify_provider_error`.
- **Requirements:** MOD-10, FWK-01.
- **Files:** `substrate/gateway.py`, `tests/test_gateway_litellm.py`.
- **Impact:** none (provider model ids and env hashes unchanged).
- **ADR:** none.
- **Depends on:** nothing.
- **Done when:**
  - [ ] the mocked-LiteLLM tests pass through the Router;
  - [ ] a test shows retries and limits don't double-count with the QuotaLedger.

### #59 Tool-call emulation with instructor (`feat/tool-call-emulation`)

- **Goal:** models without native tool calling (`caps.tool_calling: emulated`) can run the tool-using shapes.
- **Scope:** instructor turns the model's reply into validated tool calls, through `ModelGateway`. A rejected
  reply counts as an invalid call (no free recovery). Agree the library with #51 (structured plans).
- **Requirements:** SUB-02, FWK-01.
- **Files:** `substrate/gateway.py` or a new module beside it, `providers/catalog.py` (read the cap).
- **Impact:** none for native models.
- **ADR:** none.
- **Depends on:** nothing.
- **Done when:**
  - [ ] a scripted "no native tools" model solves the P0 set through emulation;
  - [ ] malformed emulated calls count against Q.

### #60 LangGraph checkpointer (`feat/langgraph-checkpointer`)

- **Goal:** an interrupted run resumes from its last node instead of starting over.
- **Scope:** a LangGraph checkpointer on the V2–V5 graphs: SQLite offline, Postgres when configured. Resume keeps
  the same RunKey, budget used so far and trace.
- **Requirements:** SUB-09, FWK-01.
- **Files:** `shapes/` (compile with a checkpointer), `runner/`.
- **Impact:** none on scoring; a resumed run must score like an uninterrupted one.
- **ADR:** none.
- **Depends on:** nothing.
- **Done when:**
  - [ ] offline test: a run interrupted mid-graph resumes and ends with the same status and outcome as an
    uninterrupted run.

### #61 hfx bench and recipe-vs-recipe report (`feat/bench-file`)

- **Goal:** a benchmark described in one readable file runs as one command, and the report can compare any two
  recipes.
- **Scope:** `hfx bench <file>` reads a file shaped like `docs/design/benchmark.example.yaml`, runs it through the
  existing campaign path (projection, resume, faults) and stores the file with the campaign. The report pairs any
  two recipes on the same task, model, provider and seed.
- **Requirements:** ANL (part).
- **Files:** a new CLI module, `results/report.py`; shared `cli/main.py` (one line), `README.md` (new section).
- **Impact:** none.
- **ADR:** none.
- **Depends on:** #37.
- **Done when:**
  - [ ] the example file runs offline with scripted models;
  - [ ] the report shows a paired v2-base vs v3-staged table.

### #62 Selector on pooled cells (`feat/selector-pooled-cells`)

- **Goal:** the selector can use evidence from calibrated-equivalent providers.
- **Scope:** the selector optionally reads pooled cells (ADR 0010). It still uses only current cells, and
  held-out runs never inform a held-out pick.
- **Requirements:** SEL-01 to SEL-06, MOD-06.
- **Files:** `src/harness_factory/select/`.
- **Impact:** none unless pooling is switched on.
- **ADR:** none.
- **Depends on:** #35.
- **Done when:**
  - [ ] offline test: with an `equivalent` verdict, the selector's evidence count doubles and its pick is logged
    with the pool.

### #63 Analysis: shape × model by task class (`feat/analysis-shape-by-model`)

- **Goal:** the ANL-01 views: which shape wins per model and task class, and where shapes cross over by step depth.
- **Scope:** tables and charts on scipy (bootstrap) and polars; views on the results DB that Superset can read.
- **Requirements:** ANL-01, FWK-01.
- **Files:** `results/` (new module), the report.
- **Impact:** none on stored rows.
- **ADR:** none.
- **Depends on:** #41 (aggregation fixes).
- **Done when:**
  - [ ] offline: the views build from a scripted campaign;
  - [ ] the intervals match `paired_delta` within tolerance.

### #64 Storage: Alembic and Postgres in CI (`chore/storage-alembic`)

- **Goal:** schema changes are migrations, and the Postgres path is tested.
- **Scope:** Alembic replaces the additive in-code migration (keep old stores readable). CI runs the store tests on
  Postgres with testcontainers.
- **Requirements:** TRC-03, NFR-10.
- **Files:** `results/store.py`, `alembic/`; shared `ci.yml`, `pyproject.toml` + `uv.lock`.
- **Impact:** none.
- **ADR:** none.
- **Depends on:** coordinate with #41 and #48, which also touch the store.
- **Done when:**
  - [ ] a store made by today's code upgrades cleanly;
  - [ ] the store tests pass on SQLite and Postgres.

### #65 Langfuse in compose (`chore/langfuse-compose`)

- **Goal:** the collector's Langfuse export has a target, next to Phoenix.
- **Scope:** a Langfuse service in `compose.yaml`; OpenInference instrumentation where it can replace attributes
  we set by hand.
- **Requirements:** TRC-01, TRC-02.
- **Files:** shared `compose.yaml`, `.env.example`; `observability/`.
- **Impact:** none.
- **ADR:** none.
- **Depends on:** nothing.
- **Done when:**
  - [ ] `docker compose up` brings Langfuse up;
  - [ ] a scripted run's spans arrive in both Phoenix and Langfuse (documented check).

### #66 promptfoo prompt checks (`feat/promptfoo-smoke`)

- **Goal:** quick prompt × model checks for the prompt templates, outside campaigns.
- **Scope:** promptfoo config over `prompts/`, offline with a scripted or echo provider in CI.
- **Requirements:** SKP-03.
- **Files:** `promptfoo/` (new); shared `ci.yml` (one job).
- **Impact:** none (prompt templates unchanged).
- **ADR:** none.
- **Depends on:** nothing.
- **Done when:**
  - [ ] the promptfoo checks run offline in CI;
  - [ ] the README has a short section.

## 5. Ready for review, not open for handover

#34 (task batch 3), #35 (calibration) and #37 (test-matrix notes) are built and waiting for review. A reviewing
session may comment. Changes to them stay with `session:core` unless a handover is recorded as in §1.
