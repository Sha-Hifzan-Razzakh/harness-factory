# Harness Factory: working rules

Builds agent harnesses from recipes, runs them on versioned tasks, grades the environment after the run,
and stores an effectiveness score per (task class × recipe × model). Read README.md for the run walkthrough.

## Commands

```bash
uv sync --extra dev                 # install from uv.lock
uv run pytest -q -n auto              # full suite in parallel, as CI runs it (spawns MCP servers)
uv run ruff check src tests
uv run hfx validate                 # recipes vs tools.yaml, prompts, tiers
uv run hfx reference                # every task's reference.py passes its own oracle
uv run hfx smoke --models scripted/replay --strict   # offline P0 gate, same as CI
HF_SANDBOX=docker uv run hfx smoke --models scripted/replay --strict   # same, inside containers
docker build -t harness-factory:local . && docker compose run --rm factory smoke --models scripted/replay --strict
```

The CLI is `hfx`, not `hf`: huggingface_hub (pulled in by LiteLLM) installs its own `hf`.

## Where things go

| Change | Location | Also do |
|---|---|---|
| New task | `catalog/tasks/<id>/task.yaml` + `reference.py` | `hfx reference <id>` passes; add a guard check (`weight: 0, critical: true`) for "don't touch anything else"; the reference fits V3 caps (≤3 reads before the first write, ≤5 calls after); each trap gets a wrong-answer script in `tests/test_task_traps.py`; a do-nothing run must score 0 (the same file checks every task: a check that passes on the untouched seed is a guard); editing a seed changes the hash of every task using it |
| New tool | MCP server in `src/harness_factory/env/mcp/impl/`, declare in `catalog/tools.yaml` | side effect + risk are mandatory; raise `ToolError`, never `ValueError` (MCP 2.x hides other exception messages from the model) |
| New shape (V1, V3...) | `src/harness_factory/shapes/<v>.py` with `@ShapeRegistry.register(Variant.X)` | import it in `shapes/builtin.py`; never in `shapes/__init__.py` (import cycle) |
| New recipe | `catalog/recipes/**.yaml`, model slot left empty | `hfx validate` passes; V3 recipes declare `workflow:` stages with prompts under `prompts/stage/`; a matrix recipe is `base: <id>` plus the one dimension it changes (`catalog/recipes/matrix/`) |
| New provider/model | `catalog/providers.yaml` | verify model ids and limits against the provider's live docs; limits that apply per model use `scope: model` (quota bucket `provider|model`); prices are not hashed, model ids are (`env_hash`) |
| Container / compose | `Dockerfile` (factory), `compose.yaml` (services `factory`, `factory-docker`), sandbox image `src/harness_factory/env/sandbox.Dockerfile` | the factory image never contains `.env` (`.dockerignore`); the socket is mounted only in `factory-docker`; `./var` keeps its absolute path in the container; editing `sandbox.Dockerfile` changes every sandbox image tag |
| Inspect AI | `src/harness_factory/integrations/inspect_task.py` (optional extra `inspect`) | outer shell only; never move tasks or grading into Inspect (`docs/adr/0001-inspect-ai.md`) |
| New fault profile | `catalog/faults/<id>.yaml` (rules: tools, kind, rate, nth, max_per_run) | faults fire only in `ToolExecutor`; never tell the model a failure was injected; `none` is reserved for clean runs |
| New recipe dimension | the field in `recipes/model.py` (optional, in `_HASH_OMIT_WHEN_NONE`), behaviour in `shapes/common.py` | every shape gets it the same way; an offline scripted test that it changes behaviour; a recovery or retry is never free (rejected replies count as invalid calls in Q); add the matrix recipe to the CI matrix gate |
| New grader | `src/harness_factory/grading/`, register in `default_pipeline()` | grade the environment or the trace, not the model's claims |

## Rules that protect the measurements

- **Run identity.** `RunKey` and the content hashes decide what counts as the same experiment. The recipe hash
  excludes the model on purpose (the model is a campaign axis). Changing what goes into a hash invalidates
  comparisons with older results, so call it out in the PR. A new optional recipe field goes in
  `_HASH_OMIT_WHEN_NONE` (`recipes/model.py`) so existing recipe hashes do not change. Campaign axes (model,
  seed) are nulled before hashing (`_SLOT_AXES`). Prompt template sources are part of the recipe hash;
  tool specs and the provider model id are in `RunKey.env_hash`. After changing an MCP server's tools, run
  `hfx tools freeze` (CI runs `--check`).
- **Scoring.** Changes to `ScoreWeights.composite`, oracle semantics or run status logic change every number in
  the results store. Call them out in the PR and update the requirements doc. The store's `composite` column is
  E₀ (before cost). The final E is computed at aggregation against a cost baseline (`results/cost.py`); never
  bake C into a stored run, because it is relative to other runs. Robustness R is the same: computed per cell
  at aggregation (faulted ÷ clean mean outcome). Faulted runs feed only R, never a cell's E mean or the cost
  medians, and an injected tool failure is never counted against Q.
- **No free credit.** A run that does nothing must score 0. Preservation checks are zero-weight guards, and
  trajectory counts as Q·O.
- **How runs end** (`shapes/common.finish`, `runner._finalize_status`):
  - A harness stop (budget, stage cap, context overflow, malformed output) is graded on the environment with
    outcome × `OVERRUN_OUTCOME_FACTOR` and status at most PARTIAL.
  - Infrastructure is INCOMPLETE; setup and crashes are ERROR. Neither is averaged into cell means.
  - Provider exceptions are classified in exactly one place, `substrate/gateway.classify_provider_error`. Never
    catch provider errors elsewhere.
  - A 400 is the model's fault only when the provider says the output was malformed.
- **Isolation.** Every run gets a fresh sandbox. Nothing may read or write outside `HF_WORKSPACE` / `HF_DB`.
  The Docker isolation contract is `DockerSandbox.run_args()`; weakening any flag must fail
  `tests/test_docker_sandbox.py`. A new MCP server module goes in `env/mcp/impl/` so the sandbox image picks
  it up (`SERVER_FILES`), and may import only `mcp` and the standard library.
- **Secrets.** Only in `.env` / environment. Never in recipes, prompts, task files, traces or test fixtures.
- **Budget semantics.** `max_steps` counts model calls and gates only the next model call; the tool calls a step
  asked for still run. Tool, token and wall-time limits gate everything.
- **Shapes differ only in control flow.** Model turns, tool batches and the stop-to-status mapping live in
  `shapes/common.py`; a new shape reuses them so comparisons stay fair.
- **Concurrency.** Campaigns run many `run_one` calls on one event loop. Per-run state lives in the run
  (tracer, budget, backend, sandbox); only the Factory's QuotaLedger and ResultsStore are shared. Never add
  module-level mutable state that a run writes to. Model calls go through `ModelGateway`, which takes a ledger
  slot first, so new code must not call providers directly.
- **Cleanup on cancel.** A failed or cancelled `__aenter__` gets no `__aexit__`: async context managers that
  open processes (MCP clients, the session pool) close what they opened in `__aenter__` itself. Teardown runs
  shielded (`provisioner._teardown`), and a cancelled campaign waits for in-flight runs to finish cleaning up.
  Anything that creates resources must not do so before a step that can fail (e.g. `DockerSandbox` resolves
  its image before creating directories).
- **Tests run in parallel** (pytest-xdist, one process per worker). A test may not depend on another test's state
  or on a fixed path outside `tmp_path` (each test gets its own sandboxes directory), and a check for leftovers
  looks only at what the test itself created, never at every hf container or directory. Tests about
  aggregation, identity or campaigns use the `p0_tasks` fixture, not every task, so they don't slow down as the
  catalog grows.
- **Offline first.** Every feature must be testable with the `scripted` provider. Real-provider tests mock
  `litellm.acompletion` (see `tests/test_gateway_litellm.py`).

## Git

Branch from `main` (`feat/…`, `fix/…`, `ci/…`), one concern per PR, CI green before merge.
