# ADR 0001: Inspect AI is an optional outer shell, not the engine

- **Status:** accepted (P0 spike, 2026-10-28)
- **Decides:** whether [Inspect AI](https://inspect.aisi.org.uk/) should run the factory's tasks and grading,
  or sit beside the factory.

## What was tried

An adapter, `src/harness_factory/integrations/inspect_task.py` (one file, about 150 lines). It defines an Inspect
`@task` whose samples are factory tasks. Its solver calls the factory's `run_one()`, and its scorer reports the
factory's own grades. Inspect owns the dataset, epochs, concurrency, the eval log and the viewer. The factory owns
everything else, unchanged.

```bash
uv sync --extra inspect
inspect eval src/harness_factory/integrations/inspect_task.py --model mockllm/model -T recipe=v3-staged --epochs 2
inspect view
```

## Evidence

| Check | Result |
|---|---|
| Dependency compatibility | `inspect-ai 0.3.285` and `mcp 2.3.0` resolve together; no conflict |
| Runs end to end | `v3-staged`, 5 tasks × 2 epochs: 10/10 samples, E₀ mean 1.00; Inspect's own model called 0 times |
| Grades preserved | Each sample's score is the factory's EffectivenessRecord (E₀, O, Q, status, failed checks, violations); nothing is re-graded. Cost-adjusted E needs a baseline across runs, so it stays in `hfx results --cells` |
| Transcripts | The full transcript (stage prompts, tool calls with arguments, tool results) appears in the Inspect log and viewer |
| Seeds | Inspect epoch *n* maps to factory seed *n − 1*; RunKeys and results-store rows are correct |
| Results store | Still authoritative: all 10 runs written with their RunKeys |
| Concurrency | Same workload, local sandbox: `hfx smoke` 14.1 s sequential; `inspect eval` 9.9 s including about 3–4 s Inspect startup |
| Footprint | About 113 direct dependencies, including boto3, s3fs, numpy and textual; package about 60 MB |

## Capability overlap

| Capability | Factory | Inspect | Decision |
|---|---|---|---|
| Task definition | Versioned task, env seed, weighted oracle, guard checks, reference solution | `Sample` with input and target | **Factory**: environment-state oracles are the core of the measurement |
| Grading | State diff over the sandbox, trajectory grader, composite E | Scorers over `TaskState` (sandbox access possible) | **Factory** |
| Run identity | `RunKey` with content hashes; the recipe hash excludes the model (campaign axis) | Eval log ids, task version | **Factory**: Inspect has no notion of a recipe hash or of the model as an axis |
| Harness variation | Recipes, shapes V1–V3 (the experiment variable) | Solvers, agents | **Factory** |
| Providers | LiteLLM + catalog + quota ledger + allocation planner (P2) | Built-in providers, incl. OpenRouter and Groq | **Factory**: no quota or allocation in Inspect |
| MCP | Session pool, frozen catalog, schema-drift check | `mcp_server_stdio/http/sandbox` | **Factory**: drift checking is required for comparable runs |
| Sandboxes | Local, Docker (no network, read-only, no caps) | Docker, plus Kubernetes and others via plugins | **Factory** for now; revisit if Kubernetes scale is needed |
| Concurrency, retries | Parallel campaigns with a shared, pacing quota ledger (added after this spike) | `max_samples`, retries, eval sets | **Factory** (the gap this spike found is closed) |
| Log viewing | JSONL, Phoenix, Langfuse, MLflow | `inspect view` | **Both**: Inspect's viewer is a good per-sample transcript browser |
| Benchmarks | 5 seed tasks | `inspect_evals` library | **Later**: importing agentic benchmarks as factory tasks needs environment-state oracles, so only some fit |

## Decision

The factory stays the engine. Inspect is supported as an **optional outer shell** through the `inspect` extra.
Use it for its viewer, epochs and concurrency. Tasks and graders are **not** ported to Inspect: that would give up
run identity, recipe-level comparison and provider allocation, which are the reasons the factory exists.

## Consequences

- One adapter file to maintain. CI runs it (an `inspect eval` step and `tests/test_inspect_adapter.py`).
- `inspect-ai` stays out of the core dependencies because of its footprint.
- Pitfalls found:
  - `inspect eval` needs a task path relative to the working directory.
  - Inspect's `--model` is unused; pass `mockllm/model`. The factory model comes from `-T model=… -T provider=…`.
  - Inspect runs samples concurrently, so the results store must accept concurrent writes. SQLite is fine at this
    scale; use Postgres for campaigns.
- The concurrency gap is now explicit and goes into P2 (parallel workers sharing the quota ledger).

## Revisit when

- Kubernetes-scale sandboxes are needed: Inspect's sandbox plugins may beat building our own.
- Importing `inspect_evals` agentic benchmarks becomes a goal.
