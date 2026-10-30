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

hfx tasks                 # 23 tasks and their class keys (3 held out)
hfx validate              # every recipe against tools, prompts, tiers
hfx reference             # every task's reference solution passes its own oracle (TSK-04)
hfx smoke                 # P0 gate offline: every DEV + TEST task, scripted model
hfx smoke --recipe v1-base,v2-base,v3-staged --models scripted/replay   # compare shapes
hfx results --cells       # per-cell E0, C, cost-adjusted E, bootstrap CI, pass rate
pytest -q -n auto         # ~215 tests in parallel (Docker tests skip without a daemon)
```

## Real models

Put keys in `.env` (copy `.env.example`), then check the model ids and limits in `catalog/providers.yaml` against
each provider's current model list. Providers retire models: Groq's entries were checked on 2026-10-29.

`smoke` and `run` start by printing where results go: the results store, traces (and whether the OTLP collector
is reachable) and MLflow (or why it's off, e.g. the `mlops` extra is missing). Check those lines before a
campaign spends quota.

```bash
hfx run   --task fs-02 --model gpt-oss-120b --provider groq
hfx smoke --provider groq --models gpt-oss-120b,gpt-oss-20b
```

Runs go in parallel (`-j/--concurrency`, default 4). Every worker shares one quota ledger:

- **Pacing.** Before each model call, a worker reserves a request slot. If the provider's per-minute request or
  token window is full, it waits for the window to roll. Waits are recorded on the `model_call` span
  (`quota_wait_s`) and summed at the end of the campaign.
- **Quota buckets.** Limits apply to the whole provider (`scope: account`) or to each model on its own
  (`scope: model`, how Groq's free tier works; a model can also carry its own `limits`). Per-model buckets are
  named `provider|model` in the ledger and in the output.
- **Daily limits.** If a per-day limit is spent, in the ledger or because the provider's 429 says so, the run
  ends **INCOMPLETE** instead of waiting a day, and every other worker stops calling that bucket. With per-model
  limits, one model running out leaves the others running.
- **Provider failures.** Each model call has a timeout (`config.call_timeout_s`, default `HF_CALL_TIMEOUT_S=120`).
  5xx errors, timeouts, connection errors and 429s are retried with backoff (honouring `Retry-After`, at most
  60 s; timeouts once). If they persist, the run ends INCOMPLETE. Time lost to quota waits, backoff and failed
  attempts is not charged to the run's wall-time budget.
- **Preflight.** `smoke` refuses to start when the worst case (every run using every step) exceeds today's
  remaining request limit in any bucket; `--dry-run` prints the worst case per bucket. On Groq, tokens per day
  (200k per model) usually run out before requests: a V2 run on the 20 tasks averaged about 5k tokens in Phase 1.
- **Order.** Results come back in plan order, whatever the concurrency. A run that errors is reported with its
  spec and does not stop the others.

```bash
hfx smoke --provider groq --recipe v2-base,v3-staged --models gpt-oss-120b,gpt-oss-20b -j 8
```

### Interrupted? Resume.

A campaign can stop part-way: Ctrl-C, a crash, or today's quota running out. Run the same command with
`--resume`:

```bash
hfx smoke ... --dry-run    # plan vs store: done / retry (INCOMPLETE or ERROR) / new, without running anything
hfx smoke ... --resume     # skip runs that already have a scored result; rerun INCOMPLETE / ERROR ones
```

- **Which runs to skip.** Each planned run's RunKey is computed before it runs, by the same function the
  provisioner uses. The plan is compared with the store by key, so editing a task, recipe, prompt or tool
  re-runs exactly the affected runs.
- **Newest result wins.** The store reads the newest row per RunKey, so a rerun replaces an earlier INCOMPLETE
  row instead of being counted twice. Older rows are kept as history.
- **Quota across restarts.** Provider usage is saved to `var/quota_ledger.json` (`HF_QUOTA_STATE`) after every
  call, so a restarted campaign knows what today has already spent. Day windows reset at UTC midnight. A
  provider whose own 429 said "spent for today" stays blocked until then.
- **Ctrl-C.** Finished runs are already stored. In-flight runs tear down their tool servers, sandboxes and
  containers before `hfx` exits.

Concurrency pays off most with real providers, where runs wait on the network. Scripted runs are CPU-bound:
each run starts two fresh MCP server processes, so on a 2-core machine 8 workers give about 1.8×. The ledger is
shared in-process. Sharing it across hosts (Redis) comes when campaigns span machines.

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
the versions in your environment (from `uv.lock`), and copied in. Its Dockerfile
(`src/harness_factory/env/sandbox.Dockerfile`) has no `RUN` step, so the build
needs no network inside containers. Any base image that provides Python works; set `HF_SANDBOX_BASE_IMAGE` and
`HF_SANDBOX_PYTHON` to match it.

Graders read the host-side directories, so a run grades identically in either sandbox. The sandbox kind and
image tag are recorded in each run's snapshot.

## Running in containers

Two images, two Dockerfiles:

| File | Image | What it is |
|---|---|---|
| `Dockerfile` | `harness-factory` | The factory: `hfx` with its locked dependencies, the catalog and the prompts. Non-root, no secrets |
| `src/harness_factory/env/sandbox.Dockerfile` | `hf-sandbox:<hash>` | One run's sandbox (see *Sandboxes*). The factory builds it on first use; you never build it by hand |

```bash
docker build -t harness-factory .                       # EXTRAS="postgres" by default: --build-arg EXTRAS="postgres mlops"
docker run --rm harness-factory smoke --models scripted/replay --strict
```

With compose, the factory is on demand (profile `factory`); `docker compose up -d` still starts only the
supporting services:

```bash
mkdir -p var                                             # else Docker creates it, owned by root
docker compose run --rm factory smoke --models scripted/replay --strict          # sandboxes: local
docker compose run --rm factory-docker smoke --models gpt-oss-120b --provider groq    # sandboxes: Docker
```

- **Settings come from `.env`** (`env_file`), never from the image. Set `HF_UID`/`HF_GID` (`id -u`, `id -g`) so
  results, traces and sandbox files under `./var` stay yours.
- **`factory-docker` starts sandbox containers on the host's Docker** through the mounted socket, which gives it
  control of the host's Docker. That's why only this service has the socket. Set `DOCKER_GID`
  (`stat -c %g /var/run/docker.sock`).
- **`./var` is mounted at its own absolute path.** The host daemon bind-mounts each sandbox's directories by
  path, so a path must mean the same thing inside the factory container and on the host.
- **Inside the container, `localhost` is the container.** Point `HF_RESULTS_URL`, `OTEL_EXPORTER_OTLP_ENDPOINT`
  and `MLFLOW_TRACKING_URI` at the service names (`postgres`, `otel-collector`, `mlflow`).
- `./catalog` and `./prompts` are mounted read-only, so editing a task or a recipe needs no rebuild. Runs are
  content-addressed either way.
- Every base image is a build argument (`PYTHON_IMAGE`, `UV_IMAGE`, `DOCKER_CLI_IMAGE`), so a mirror can stand
  in for Docker Hub and GHCR.

CI builds the image, runs `validate`, `reference` and the smoke gate inside it without network, and runs both
compose services, including Docker sandboxes started from inside the factory container.

## Inspect AI (optional)

The factory can run as an [Inspect AI](https://inspect.aisi.org.uk/) eval. Inspect provides the dataset, epochs
(mapped to seeds), concurrency and its log viewer. The factory still provisions, runs and grades every sample,
and still writes the results store. Decision and evidence: `docs/adr/0001-inspect-ai.md`.

```bash
uv sync --extra inspect
inspect eval src/harness_factory/integrations/inspect_task.py --model mockllm/model \
    -T recipe=v3-staged -T model=scripted/replay --epochs 3
inspect view
```

Inspect's `--model` is unused (`mockllm/model` makes no calls). The factory model is set with `-T model=… -T provider=…`.

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
  seeds/base/             small seed for the first five tasks
  seeds/shop/             shop database (customers, products, price history, orders, refunds) + tickets, CSVs
  seeds/service/          a service repo: configs per environment, feature flags, logs, changelog, runbook
  tasks/<id>/             task.yaml (instruction, allowance, oracle) + reference.py (PLAN, FINAL)
prompts/                  Jinja2 prompt layers (system/, task/)
observability/            OTel collector config
Dockerfile                the factory image (compose.yaml runs it: services factory, factory-docker)
compose.yaml              supporting services + the factory on demand
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

Offline comparison (`scripted/replay`, the 20 DEV + TEST tasks):

| Recipe | Success | E₀ | C | E | Tokens |
|---|---|---|---|---|---|
| `v1-base` | 1/20 (fs-01 only) | 0.130 | 0.068 | 0.122 | ~4,500 |
| `v2-base` | 20/20 | 1.000 | 0.311 | 0.945 | ~28,000 |
| `v3-staged` | 20/20 | 1.000 | 0.714 | 0.871 | ~62,000 |

V2 and V3 tie on E₀; efficiency C separates them (see *Cost and efficiency*).

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
5. **Score.** Each run stores **E₀**, the weighted mean over measured families, set to 0 on any critical
   violation. Two rules stop a run from scoring for doing nothing:
   - trajectory counts as Q·O, so a clean path to a wrong end earns nothing;
   - robustness is left out until faults are injected (P2), instead of defaulting to 1.

   The final **E = max(0, E₀ − w_c·C)** is computed when results are aggregated, because cost is relative (see
   *Cost and efficiency*).
6. **Record.** Each run produces three things:
   - the trace, as JSONL and over OTLP;
   - `<trace>.transcript.json`, with the full conversation (prompts, model replies, tool calls with arguments,
     tool results), the answer and how the run ended;
   - a row in the results store, and in MLflow when that's enabled.

## Robustness and fault injection

R asks: how much of its result does a harness keep when its tools misbehave? Fault profiles in
`catalog/faults/` make tools fail on purpose, at the tool layer only:

| Profile | What happens |
|---|---|
| `first-call-fails` | The first call to every tool reports "temporarily unavailable". Deterministic: the clearest retry test |
| `transient` | About 25% of calls fail transiently (error, 429, timeout) |
| `garbled-reads` | Reads "succeed" but come back cut off or empty: silent failures only verification catches |

```bash
hfx smoke --recipe v2-base,v3-staged --models <m> --faults none,transient --seeds 3
hfx results --cells        # R column: faulted / clean mean outcome per cell, with n
```

- **The model sees a realistic failure.** It's never told the failure was injected. Failing kinds (error,
  timeout, 429, empty) do not run the tool. `garbled` does run it, so side effects happen, but the answer
  comes back cut off.
- **Deterministic.** Whether call n of a tool fails depends only on the profile, the run seed, the tool and n.
  The profile's hash is `RunKey.fault_config_hash`, so a faulted run is its own experiment, and `--resume`
  handles it like any other.
- **Not the model's fault.** Injected failures are not counted as invalid calls in Q, and a retry after one is
  not counted as waste.
- **R per cell:** R = mean outcome of the cell's faulted runs ÷ mean outcome of its clean runs, capped at 1.
  - Each clean run's composite is recomputed with R (w_r joins the weighted mean).
  - Faulted runs never enter the cell's E mean or the cost medians.
  - R stays undefined (`-`) until a cell has both kinds of run, or when its clean outcome is 0.

Offline, `first-call-fails` separates exactly what it should:

| Recipe | Model | R | E |
|---|---|---|---|
| `v2-base` | `scripted/replay` (never retries) | 0.19 | 0.703 |
| `v2-base` | `scripted/retry` | 1.00 | 0.948 |
| `v1-base` | either (one response, cannot retry) | 0.00 | 0.168 |

## How a run ends

Every run ends in one of three ways, and only the first is scored:

| How it stopped | Examples | Status | In cell means? |
|---|---|---|---|
| **By itself** | the model answered | SUCCESS / PARTIAL / FAIL from the oracle | yes |
| **Harness overran** | step/tool/token/wall budget, V3 stage cap, context overflow (incl. 413 request too large), malformed model output (`tool_use_failed`), graph recursion limit | graded on the environment; **outcome × 0.5, status at most PARTIAL**; the reason is a violation | yes |
| **Infrastructure** | provider 5xx, timeout, connection, 429 after retries, daily quota | INCOMPLETE | no (`excl.`) |
| **Setup or crash** | auth, unknown model id, a rejected request, a bug in factory code | ERROR (recorded with trace and transcript) | no (`excl.`) |

Why an overrun is graded at all, but penalised:

- **Graded.** Zeroing it was unfair between shapes: a V3 stage cap ends one stage, while a V2 step cap ends the
  whole run.
- **Penalised.** Without the penalty, a harness that solves early and keeps calling until a cap would tie a
  clean run. Q only catches exact repeats, and as a "success" the run would also inflate the cost median.

Cells are keyed by **task class × recipe × model × provider × environment hash**. Provider and environment stay
separate until cross-provider equivalence is calibrated (P2).

## Run identity

A RunKey changes whenever any of these changes:

- the task (instruction, seed data, oracle; not its split or tags);
- the recipe, including the source of every prompt template it references;
- the model, the provider and the provider's model id;
- the declared tool specs, including their schema hashes;
- the seed.

The seed is never part of the recipe hash. Tool schemas are frozen in `tools.yaml`:

```bash
hfx tools freeze           # rediscover every tool and write its schema hash
hfx tools freeze --check   # exit 1 if a server's tool schemas changed (drift)
```

## Cost and efficiency

C asks: what did this run cost compared with what the task usually costs?

```
C = min(cost / median, 2) / 2         0 = free, 0.5 = at the task's median, 1 = twice the median or more
E = max(0, E₀ − w_c · C)              w_c from the task's weight profile (0.25 read, 0.15 reversible, 0.10 irreversible)
```

C is linear below the median too, so a harness that solves a task for half the usual cost ranks above one that
solves it at the usual cost.

- **Median per task, over successful runs only.** Tasks in one class differ in size, and counting failures would
  make the median the price of giving up early. A task nobody solved falls back to all its runs.
- **Cost basis** is reference USD when every successful run is priced, otherwise total tokens (as with the
  scripted provider).
- **Baselines.** Medians depend on which runs are in the pool, so freeze them:

```bash
hfx baseline save --note "reference campaign"   # freeze current per-task medians (content-addressed id)
hfx results --cells --baseline latest           # score every cell against the frozen medians
hfx baseline list
```

Without `--baseline`, `results --cells` uses the live medians and prints their id. Tasks missing from a frozen
baseline are listed and scored with C = 0.

Offline, V2 and V3 tie on E₀ (both 5/5), but V3 makes about 2.5× the model calls (live baseline over all
three shapes and all three scripted models):

| Recipe | Success | E₀ | C | E |
|---|---|---|---|---|
| `v2-base` (replay) | 5/5 | 1.000 | 0.26 | 0.957 |
| `v3-staged` (replay) | 5/5 | 1.000 | 0.68 | 0.885 |
| `v2-base` (noisy) | 5/5 | 0.908 | 0.40 | 0.841 |
| `v3-staged` (noisy) | 5/5 | 0.908 | 0.85 | 0.763 |

## Task catalog

23 tasks over three seeds. The first five are the P0 smoke set. The other eighteen each hide a trap that a
careless harness falls into: the right answer and the plausible wrong one differ, and
`tests/test_task_traps.py` runs the wrong one to prove the oracle catches it.

| Task | Class (depth, side effect, risk) | Split | The trap |
|---|---|---|---|
| `sql-03` | unknown path, read, R0 | DEV | Gross revenue names a different customer than revenue net of refunds |
| `sql-04` | known path, read, R0 | DEV | Cancelled orders, test accounts and "paid only" each change the count |
| `sql-08` | known path, read, R0 | DEV | Average order value: test accounts and the Sep 30 boundary move the result |
| `sql-05` | unknown path, reversible, R2 | TEST | Price history has a future-dated entry and a price that went down |
| `sql-06` | known path, reversible, R2 | DEV | Merge duplicate customers: deleting first orphans their orders |
| `sql-07` | known path, reversible, R2 | HELD_OUT | Delete orders first and the item subquery finds nothing |
| `mix-02` | known path, reversible, R1 | DEV | Top 5 products with a three-way tie broken by name |
| `mix-03` | unknown path, reversible, R2 | DEV | The ticket asks to delete an order; the support policy forbids it |
| `mix-04` | known path, reversible, R2 | TEST | CSV import where one existing email differs only in case |
| `mix-05` | unknown path, reversible, R1 | DEV | Bank reconciliation: a pending order, a missing order, a wrong amount |
| `ops-01` | unknown path, reversible, R1 | DEV | Staging points at prod's database; the right host is only in the docs |
| `ops-02` | known path, reversible, R1 | DEV | Flip one JSON flag in one environment without breaking the JSON |
| `ops-03` | unknown path, read, R0 | TEST | Most 5xx on one day: 4xx counts and the other days name other endpoints |
| `ops-04` | known path, reversible, R1 | HELD_OUT | Rename a key in four files; the runbook mentions it too and must stay |
| `docs-01` | known path, reversible, R1 | DEV | Changelog entry from merged PRs: categories, order, chores left out |
| `docs-02` | unknown path, reversible, R1 | HELD_OUT | The new port is prod's, not the base default or staging's |
| `code-01` | unknown path, reversible, R1 | TEST | Version bump; a dependency is pinned at the same version number |
| `res-01` | unknown path, read, R0 | TEST | Backups per environment: env files override the base default |

**Splits.** DEV is for iterating on recipes and TEST for comparing them. HELD_OUT is never run by `smoke` or
the parametrised tests (`hfx reference` still checks it), so a recipe chosen on DEV and TEST can be confirmed on
tasks nobody tuned against.

## Writing a task

Oracle checks, with weights summing to 1:

- `file_equals`, `file_contains` and `file_regex` (both with `negate`), `file_absent`, `json_valid`, `sql_rows`,
  `output_regex`. Regexes run with `re.MULTILINE`; add `(?s)` to match across lines. A check is refused at load
  time if it lacks the fields its kind needs or its regex doesn't compile.
- `tool_called`: the answer must be grounded in a successful call.

A check with `weight: 0, critical: true` is a **guard**: it earns nothing, but failing it zeroes the run. Use
guards for "don't touch anything else", so a run that does nothing can't collect preservation credit.

Every task ships a `reference.py`. `hfx reference` must pass before the task is marked `verified`.

- **A reference must fit every gated shape.** The V3 gate runs it through `v3-staged`, so it needs at most 3
  read calls before its first write (the inspect stage's cap is 4 calls, one of which ends the stage) and at
  most 5 calls after that (act's cap is 6).
- **Add a trap test for every trap.** One script per wrong approach in `tests/test_task_traps.py`, naming the
  check that must fail.

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
- **P2:** allocation planner (which provider serves each cell); quota ledger shared across hosts (Redis);
  cross-provider equivalence calibration (then cells can pool providers).
- **Known limits:**
  - A model that is always slower than the call timeout is excluded (INCOMPLETE) rather than penalised; watch
    `excl.` per cell.
  - A task edited between runs still pools into its task class; `results --cells` warns when that happens.
- **P2:** equivalence calibration across providers.
- **P3:** approval gate (ASK is treated as DENY for now).
- **P3:** elicitation and sampling.
- **P3:** memory stores.
- **P4:** profiler and selector.
