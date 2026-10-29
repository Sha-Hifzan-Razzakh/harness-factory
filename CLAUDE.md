# Harness Factory: working rules

Builds agent harnesses from recipes, runs them on versioned tasks, grades the environment after the run,
and stores an effectiveness score per (task class × recipe × model). Read README.md for the run walkthrough.

## Commands

```bash
uv sync --extra dev                 # install from uv.lock
uv run pytest -q                    # full suite (~35 s, spawns MCP servers as subprocesses)
uv run ruff check src tests
uv run hfx validate                 # recipes vs tools.yaml, prompts, tiers
uv run hfx reference                # every task's reference.py passes its own oracle
uv run hfx smoke --models scripted/replay --strict   # offline P0 gate, same as CI
```

The CLI is `hfx`, not `hf`: huggingface_hub (pulled in by LiteLLM) installs its own `hf`.

## Where things go

| Change | Location | Also do |
|---|---|---|
| New task | `catalog/tasks/<id>/task.yaml` + `reference.py` | `hfx reference <id>` passes; add a guard check (`weight: 0, critical: true`) for "don't touch anything else" |
| New tool | MCP server in `src/harness_factory/env/mcp/impl/`, declare in `catalog/tools.yaml` | side effect + risk are mandatory; raise `ToolError`, never `ValueError` (MCP 2.x hides other exception messages from the model) |
| New shape (V1, V3...) | `src/harness_factory/shapes/<v>.py` with `@ShapeRegistry.register(Variant.X)` | import it in `shapes/builtin.py`; never in `shapes/__init__.py` (import cycle) |
| New recipe | `catalog/recipes/**.yaml`, model slot left empty | `hfx validate` passes; V3 recipes declare `workflow:` stages with prompts under `prompts/stage/` |
| New provider/model | `catalog/providers.yaml` | verify model ids and limits against the provider's live docs |
| New grader | `src/harness_factory/grading/`, register in `default_pipeline()` | grade the environment or the trace, not the model's claims |

## Rules that protect the measurements

- **Run identity.** `RunKey` and the content hashes decide what counts as the same experiment. The recipe hash
  excludes the model on purpose (the model is a campaign axis). Changing what goes into a hash invalidates
  comparisons with older results, so call it out in the PR. A new optional recipe field goes in
  `_HASH_OMIT_WHEN_NONE` (`recipes/model.py`) so existing recipe hashes do not change.
- **Scoring.** Changes to `ScoreWeights.composite`, oracle semantics or run status logic change every number in
  the results store. Call them out in the PR and update the requirements doc.
- **No free credit.** A run that does nothing must score 0. Preservation checks are zero-weight guards, and
  trajectory counts as Q·O.
- **Isolation.** Every run gets a fresh sandbox. Nothing may read or write outside `HF_WORKSPACE` / `HF_DB`.
- **Secrets.** Only in `.env` / environment. Never in recipes, prompts, task files, traces or test fixtures.
- **Budget semantics.** `max_steps` counts model calls and gates only the next model call; the tool calls a step
  asked for still run. Tool, token and wall-time limits gate everything.
- **Shapes differ only in control flow.** Model turns, tool batches and the stop-to-status mapping live in
  `shapes/common.py`; a new shape reuses them so comparisons stay fair.
- **Offline first.** Every feature must be testable with the `scripted` provider. Real-provider tests mock
  `litellm.acompletion` (see `tests/test_gateway_litellm.py`).

## Git

Branch from `main` (`feat/…`, `fix/…`, `ci/…`), one concern per PR, CI green before merge.
