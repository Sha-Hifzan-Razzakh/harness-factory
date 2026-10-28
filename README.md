# Harness Factory

Builds agent harnesses from declarative recipes, runs them against versioned tasks, grades what changed in the
environment, and records an effectiveness score per (task class × recipe × model). Requirements: *Harness Factory —
Requirements v1*. Design: *Harness Factory UML*.

Status: **P0 done, P1 in progress.** Three harness shapes (V1 single call, V2 tool loop, V3 workflow graph) run
end to end over real MCP servers, and every run is graded, traced and stored.

## Quickstart

```bash
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"

hfx tasks                 # 5 seed tasks and their class keys
hfx validate              # every recipe against tools, prompts, tiers
hfx reference             # every task's reference solution passes its own oracle (TSK-04)
hfx smoke                 # P0 gate offline: 5 tasks x 3 scripted models
hfx smoke --recipe v1-base,v2-base,v3-staged --models scripted/replay   # compare shapes
hfx results --cells       # per-cell mean E, bootstrap CI, pass rate
pytest -q                 # 58 tests (9 Docker tests skip without a daemon)
```

## Real models

Put keys in `.env` (copy `.env.example`), then check the model ids and limits in `catalog/providers.yaml` against
each provider's current model list. The ids shipped are examples.

```bash
hfx run   --task fs-02 --model llama-3.3-70b --provider groq
hfx smoke --provider groq --models llama-3.3-70b,gpt-oss-120b,qwen3-32b
```

`smoke` runs a quota preflight first. If the worst case (tasks × models × seeds × max_steps) exceeds the
provider's daily request limit, it refuses to start. The quota ledger lives in memory per process; P2 moves it to
Redis.

## Sandboxes

| `HF_SANDBOX` | What runs where | Use for |
|---|---|---|
| `local` (default) | Fresh temp directory per run; MCP servers are local subprocesses confined to it by path checks | scripted runs, development |
| `docker` | Same directories, bind-mounted into a container; MCP servers run inside it via `docker exec -i` | real models |

The Docker sandbox's isolation contract lives in one place, `DockerSandbox.run_args()`:

- `--network none`: the only interface is `lo`;
- `--read-only` root filesystem: only `/work`, `/state` and a small `/tmp` are writable;
- `--cap-drop ALL` and `no-new-privileges`;
- pid, memory and CPU limits;
- runs as your user, so files the run writes stay yours.

Tests check every point of the contract against a live container. CI runs them with `HF_REQUIRE_DOCKER=1`, so
they fail rather than skip.

The image (`hf-sandbox:<content hash>`) builds itself on first use. It contains only Python, the `mcp` package
closure and the server modules. Dependencies are resolved on the host for the image's Python and CPU, pinned to
the versions in your environment (from `uv.lock`), and copied in. The Dockerfile has no `RUN` step, so the build
needs no network inside containers. Any base image that provides Python works; set `HF_SANDBOX_BASE_IMAGE` and
`HF_SANDBOX_PYTHON` to match it.

Graders read the host-side directories, so a run grades identically in either sandbox. The sandbox kind and
image tag are recorded in each run's snapshot.

## Observability and MLOps stack

```bash
docker compose up -d     # postgres, mlflow, phoenix, otel-collector
```

Then set the following in `.env`:

| Variable | Value | Effect |
|---|---|---|
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://localhost:4318` | Spans go to the collector, which fans out to Phoenix (`:6006`) and Langfuse |
| `MLFLOW_TRACKING_URI` | `http://localhost:5000` | Each run is logged as an MLflow run (params, metrics, trace artifact) |
| `HF_RESULTS_URL` | `postgresql+psycopg://hf:hf@localhost:5432/hf` | Results go to Postgres (`uv pip install -e ".[postgres,mlops]"`) |

Langfuse needs ClickHouse, Redis and object storage, so it isn't in this compose file. Run Langfuse's official
docker-compose on the same Docker network, or use Langfuse Cloud. Then set `LANGFUSE_OTLP_ENDPOINT` and
`LANGFUSE_AUTH` (base64 of `public_key:secret_key`).

Each run is **one trace**:

```
run:<run-key-short>
  model_call:<model>
  tool_call:sql.query
    MCP send tools/call query        <- emitted by the MCP SDK, nested automatically
  model_call:<model>
  ...
  grade:pipeline
```

Spans carry OpenInference attributes (Phoenix) and `gen_ai.*` attributes (Langfuse). With no OTLP endpoint set,
traces are still written to `var/traces/<trace_id>.jsonl`.

## Layout

```
catalog/                  data, versioned with the code
  providers.yaml          provider catalog: canonical model -> provider id, limits, prices
  tools.yaml              declared MCP servers and tools (side effect, risk); discovery drift fails provisioning
  recipes/base/           harness recipes; the model slot is empty and filled per cell
  seeds/base/             seed files and SQL shared by tasks
  tasks/<id>/             task.yaml (instruction, allowance, oracle) + reference.py (PLAN, FINAL)
prompts/                  Jinja2 prompt layers (system/, task/)
observability/            OTel collector config
src/harness_factory/
  tasks/                  Task, Oracle, registry, reference loader                  (B1)
  env/                    LocalSandbox, StateInspector, MCP servers (impl/)         (B1, B11)
  recipes/                HarnessRecipe, validator                                  (B2)
  substrate/              ModelGateway, ToolExecutor, BudgetGuard, prompts          (B3)
  shapes/                 Harness protocol, ShapeRegistry, V1 / V2 / V3 shapes      (B4)
  provision/              Provisioner, task class key                               (B5)
  providers/              ProviderCatalog, QuotaLedger                              (B6)
  runner/                 RunKey, CostLedger, run_one / run_reference / smoke       (B7)
  grading/ results/       graders, EffectivenessRecord, ResultsStore                (B8)
  mlops/                  MLflow tracker                                            (B9)
  mcp/                    MCPClient, MCPSessionPool, ToolCatalog                    (B11)
  observability/          Span, Tracer (JSONL + OTLP)
  cli/                    hfx
```

## Shapes

A shape is the control flow around the model. It is the main thing a recipe varies, besides tools, prompts and
config. All three shapes share one model-turn and one tool-batch implementation (`shapes/common.py`), so they
differ only in control flow.

| Shape | Recipe | Control flow | Good at | Expected to fail at |
|---|---|---|---|---|
| V1 single call | `v1-base` | One model response; its tool calls run; nothing follows | single-step writes, cheaply | anything that must read before acting or answering |
| V2 tool loop | `v2-base` | Model calls tools until it answers (LangGraph `call_model ⇄ run_tools`) | general tasks | over-spending on easy tasks |
| V3 workflow graph | `v3-staged` | Stages from the recipe, each a bounded tool loop with its own tools: inspect (read-only), act, verify (read-only); verify can reply `RETRY:` to go back to act once | tasks where acting before inspecting is costly | cost: more model calls per task |

V3 stage tools are enforced: a write attempted in a read-only stage is refused and recorded as a
`tool_not_allowed` violation. Stage transitions are `handoff` spans, so the trace shows the path through the
graph. A V3 recipe declares its graph under `workflow:` (see `catalog/recipes/base/v3_staged.yaml`).

Offline comparison (`scripted/replay`, 5 tasks):

| Recipe | Success | Model calls | Tokens |
|---|---|---|---|
| `v1-base` | 1/5 (fs-01 only) | 5 | ~1,000 |
| `v2-base` | 5/5 | 13 | ~3,500 |
| `v3-staged` | 5/5 | 23 | ~8,700 |

V2 and V3 tie on E for now, because efficiency C is not yet normalised (P2). Once it is, the cost difference
will show up in E, and the selector can prefer the cheaper shape per task class.

## How a run works

1. **Provision.** The recipe gets the cell's model and is validated (shape registered, prompts exist, tools resolve, tier ≥
   the risk of reachable tools, model supports tool calling). A fresh sandbox is seeded. One MCP stdio session is
   opened per server, and discovered tools are checked against `tools.yaml`.
2. **Allowed tools** are the recipe slice ∩ the task allowance. The model sees only those, named `server__tool`.
3. **Run** the recipe's shape. The budget is checked before every model and tool call; `max_steps` counts model
   calls, so it gates only the next model call, and the tool calls of the last allowed step still run. Every
   tool call goes through ToolExecutor: allowance (narrowed per V3 stage), argument check, permission by side
   effect, timeout, then MCP.
4. **Grade** the environment, not the model's claims:
   - `state_diff` scores the oracle's weighted checks against the after-state;
   - `trajectory` scores Q = 1 − (invalid + repeated calls) / calls.
5. **Score.** E = weighted mean over measured families − w_c·C, set to 0 on any critical violation. Two rules stop
   a run from scoring for doing nothing:
   - trajectory counts as Q·O, so a clean path to a wrong end earns nothing;
   - robustness is left out until faults are injected (P2), instead of defaulting to 1.
6. **Record.** The trace goes to JSONL/OTLP, the row to the results store, and the run to MLflow when enabled.

## Writing a task

Oracle checks, with weights summing to 1:

- `file_equals`, `file_contains` (with `negate`), `file_absent`, `sql_rows`, `output_regex`;
- `tool_called`: the answer must be grounded in a successful call.

A check with `weight: 0, critical: true` is a **guard**: it earns nothing, but failing it zeroes the run. Use
guards for "don't touch anything else", so a run that does nothing can't collect preservation credit.

Every task ships a `reference.py`. `hfx reference` must pass before the task is marked `verified`.

## Offline models

The `scripted` provider follows each task's reference plan through the real shape, MCP servers and graders. It
only calls a tool it is offered at that moment, and otherwise ends the turn. So the same script drives every
shape: V2 offers all tools, a V3 stage offers its subset, and V1 gets a single response. It makes one call per
response and never batches dependent steps, so on V1 only the first plan step runs.

| Model | Behaviour | Expected result |
|---|---|---|
| `scripted/replay` | Follows the plan | SUCCESS, E = 1.0 |
| `scripted/noisy` | Makes one malformed call first, then recovers | SUCCESS, E ≈ 0.9 (pays on Q) |
| `scripted/lazy` | Stops one step early | FAIL or PARTIAL |

These prove the factory measures differences before any API budget is spent.

## Not in P0

- **P1:** V4 planner and V5 orchestrator shapes.
- **P2:** fault injection and robustness R.
- **P2:** efficiency C normalised against the class median.
- **P2:** shared Redis quota ledger and the allocation planner.
- **P2:** equivalence calibration across providers.
- **P3:** approval gate (ASK is treated as DENY for now).
- **P3:** elicitation and sampling.
- **P3:** memory stores.
- **P4:** profiler and selector.
- **P0 leftover:** the Inspect AI spike.
