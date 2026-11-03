# Harness Factory

Builds agent harnesses from declarative recipes, runs them against versioned tasks, grades what changed in the
environment, and records an effectiveness score per (task class × recipe × model). Requirements: *Harness Factory —
Requirements v1*. Design: *Harness Factory UML*.

Status: **P0 done, P1 in progress.** Five harness shapes (V1 single call, V2 tool loop, V3 workflow graph, V4 planner, V5 orchestrator) run
end to end over real MCP servers, and every run is graded, traced and stored.

## Quickstart

```bash
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"

hfx tasks                 # 29 tasks and their class keys (3 held out)
hfx validate              # every recipe against tools, prompts, tiers
hfx reference             # every task's reference solution passes its own oracle (TSK-04)
hfx smoke                 # P0 gate offline: every DEV + TEST task, scripted model
hfx smoke --recipe v1-base,v2-base,v3-staged --models scripted/replay   # compare shapes
hfx results --cells       # per-cell E0, C, cost-adjusted E, bootstrap CI, pass rate
pytest -q -n auto         # ~215 tests in parallel (Docker tests skip without a daemon)
```

## Adding things: `hfx new` and `hfx catalog`

Prompts, skills, recipes, tool servers and fault profiles can be added without editing files by hand. Every change
is staged in a copy of the catalog, checked with the same validators as `hfx validate`, and written only if it
passes (ADR 0009). The API and the UI use the same code.

```bash
hfx new prompt system/terse -f terse.j2                       # a Jinja2 template (system: tools, recipe; task: task)
hfx new recipe v2-terse --set prompts.system=system/terse \
    --note "Matrix (prompt): a terse system prompt"            # base v2-base + one change
hfx new skill csv-checks --name "Checking CSV files" --description "..." -f body.md
hfx new tool calc --tool add --param a:float --param b:float   # a local server: writes a stub to fill in
hfx new tool --spec calc.yaml --code calc_server.py           # or your own module
hfx new remote-tool --spec kb.yaml                            # an existing MCP server (url, version, auth_env)
hfx new fault --spec slow_sql.yaml
hfx catalog list [recipe]                                      # every item, what uses it, its hash
hfx catalog show recipe v2-terse
hfx catalog check                                              # everything wrong with the catalog now
hfx catalog rm fault slow-sql
```

- **Nothing broken gets written.** A template that doesn't parse, a recipe that fails validation, a server module
  that imports anything but `mcp` and the standard library (it runs in the offline sandbox), raises `ValueError`
  or serves tools other than the ones declared: the change is rejected and no file changes.
- **Identity changes are shown first.** Editing an existing item (a base recipe, a skill, a prompt, a server) lists
  the recipes whose hash or tool environment changes and how many stored runs that leaves on the old identity.
  It is written only with `--yes`. New items never change existing identities. `--dry-run` shows all of it and
  writes nothing.
- **`--update`** replaces an item instead of creating one. A recipe update replaces the whole file.
- **Local tool servers** are discovered on the spot: the server is started as the sandbox would, its tools are
  listed, and their schema hashes go into `tools.yaml` (what `hfx tools freeze` would write). A new tool reaches
  the model only when its task allows it, or with `granted_by` on a READ tool.
- **Not here:** tasks (they need a reference solution, trap scripts and a do-nothing check), and the built-in
  servers `fs`, `sql`, `search` and `skills`.
- Files are written, never committed: review them with `git diff`.

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
- **Projection and preflight (MOD-05).** Before a campaign, `smoke` prints a projection for each bucket:
  requests, tokens, list-price dollars, how many runs today's quota covers, the days the plan needs, and the
  minutes of pacing the per-minute limits impose.
  - **Estimates come from history,** most specific first: same model, recipe and task; then same model and
    recipe; then the same recipe on any model of the same provider kind; then a default per shape. Scripted
    runs never stand in for real models. The "estimated from" column says which level was used.
  - **A plan longer than today is normal** on free tiers: runs past today's quota end INCOMPLETE, and `--resume`
    finishes them after the reset (00:00 UTC).
  - **It refuses only what can't work:** a bucket already spent today, a plan needing more than
    `--deadline-days`, or a cost above `--budget-usd`. `--dry-run` prints the projection and stops.
  - **On Groq, tokens per day** (200k per model) run out before requests.
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
- **Newest result wins.** The store reads the newest scored row per RunKey, so a rerun replaces an earlier
  result instead of being counted twice, and an INCOMPLETE or ERROR rerun (quota, provider down) never hides a
  scored one. Older rows are kept as history.
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

Since ADR 0012 the image also carries pytest (vendored like `mcp`) and git. git comes from the one `RUN` step,
which installs it with apt only when the base image lacks it. So a base with git preinstalled still builds with
no network, and any other base must be Debian-based.

### Running code and git (ADR 0012)

Two servers let a task involve running code and version control:

| Server | Tools | Side effect |
|---|---|---|
| `code` | `run_tests(path=".", args="")`: pytest on a folder, file or `file::test`, returns exit code, counts and the end of the output; `run_python(path, args="")`: runs a workspace script | REVERSIBLE, R2: code can change anything in the sandbox, and a reset undoes it |
| `git` | `status`, `diff(path, staged)`, `log(n)`; `commit(message)` (stages everything), `branch(name)`, `checkout(name)` | reads READ; the rest REVERSIBLE, R1 |

- **Limits.** Every call is a child process with a hard time limit (code 60 s, git 20 s); its whole process group
  is killed when the limit runs out. Output is capped (head and tail kept), and the environment is scrubbed (no
  keys, no `HF_*` paths, no git or pytest config from your machine). pytest options are limited to selection and
  reporting (`-k`, `-m`, `-x`, `-v`, `--tb`, `--maxfail`, `-r`, `--co`).
- **A task asks for a repository** with `env_seed.git: true`. The sandbox commits the seed files once, at a fixed
  date as a fixed identity, so the seed commit id is the same on every machine. The git directory sits in the
  state folder next to the database, never in the workspace, so the agent's file tools can't touch it and file
  checks never see `.git`. Later commits are dated one minute after their parent. git runs with hooks, fsmonitor,
  pagers, signing and remote transports turned off from the command line, and there are no remotes.
- **Real models run code in Docker.** With a real model, the provisioner refuses a run whose recipe opens `code`
  or whose task has a `tests_pass` check in a local sandbox, unless you set `HF_ALLOW_LOCAL_CODE=1`. Scripted
  runs are exempt.
- **Requirements.** pytest is a main dependency. git must be on `PATH`: `/usr/bin/git` on Linux, Git for Windows
  on Windows. The factory image installs it.
- **Campaigns plan only what a recipe can reach.** A recipe that can't call every tool a task allows is not
  planned on that task, the same rule the selector prunes by. So `v2-base` skips `code-02` and `code-03`, and
  `v2-code` (below) runs them.
## Business systems

The `biz` server is a local, offline mock of a company's back office, served over stdio from the sandbox like
`fs` and `sql`. Its state is one JSON document: a task names its seed with `env_seed.biz_state`, and every run
gets a fresh copy in its state folder (`HF_BIZ`, next to the database; never in the workspace, so `fs` can't
edit it). New ids come from sequences in the state and "today" is the seed's `meta.today`, so runs repeat
exactly. The seed is generated (`scripts/taskgen/biz.py`) with the server's own arithmetic.

| Tool | Side effect | Notes |
|---|---|---|
| `crm_search_contacts`, `crm_get_contact` | READ | search by name, company or email; paged |
| `crm_update_contact` | REVERSIBLE R1 | editable fields only; email and tier validated |
| `inv_list_invoices`, `inv_get_invoice` | READ | filter by status and customer; balance due and overdue as of today |
| `inv_create_credit_note` | REVERSIBLE R2 | at most what is left to credit; undone by `inv_void_credit_note` |
| `inv_void_credit_note` | REVERSIBLE R2 | the invoice owes the amount again; undone by issuing the credit again |
| `inv_mark_paid` | IRREVERSIBLE R3 | a recorded payment can't be removed |
| `tkt_list`, `tkt_get` | READ | paged; the full ticket with its comments |
| `tkt_comment` | REVERSIBLE R1 | internal note; commenting on a closed ticket reopens it |
| `tkt_close` | REVERSIBLE R1 | with a resolution; undone by a comment |

A tool is REVERSIBLE only when the mock itself can undo it. Refusals (unknown ids, a field that can't be edited,
crediting more than is left, a payment dated in the future) are `ToolError`s with the reason, so the model can
correct itself. List tools return at most 50 records per page, with `total` and `next_offset`.

Graders read the state after the run with a JSON pointer, compared strictly on type (`1` never matches `true`,
`"42"` never matches `42`):

```yaml
- {id: credited_total, kind: biz_equals, weight: 0.5, path: /invoices/INV-1042/credited, text: "315.0"}
- {id: one_credit_note, kind: biz_count, weight: 0.2, path: /credit_notes,
   query: '{"invoice_id": "INV-1042", "status": "issued"}', text: "2"}
- {id: others_untouched, kind: biz_equals, weight: 0, critical: true, path: /invoices/INV-1041, text: '{...}'}
```

The state diff lists changed records (`+biz credit_notes/CN-0004; ~biz invoices/INV-1042`).

**A remote variant** (not built): the same tools behind `transport: streamable_http` with a `tenant:` would
exercise the test-tenant path end to end. Its `_tenant_reset(state)` would load `env_seed.mock_api_state[<id>]`
(the same JSON document) into a per-tenant dict instead of a file, `_tenant_export()` would return it, and
`remote_equals` checks would read it, so a task could be graded the same way on either transport.
**Server libraries (ADR 0013).** A server may import more than `mcp` and the standard library only through the
allow-list in `src/harness_factory/env/mcp/deps.py`: exact pins per library, and per server module the libraries
it uses. Today that is the `docs` server's `python-docx`, `openpyxl` and `pypdf`. Install them with the `docs`
extra (`uv sync --extra dev --extra docs`); a local sandbox, a task with office checks, and an image build refuse
to start without them and say so. The image vendors them next to `mcp`, and their pins are part of its tag and of
the `env_hash` of runs that open the server.

## Remote MCP servers

A recipe can also open an **existing** MCP server: a third-party service, or an organisation's MCP gateway, over
streamable HTTP (ADR 0004). Declare it in `catalog/tools.yaml` like a local one:

```yaml
- id: kb
  transport: streamable_http
  url: https://kb.internal.example/mcp
  version: "2026.10"              # pinned: url and version are part of the run's env_hash
  auth_env: KB_MCP_TOKEN          # the bearer token's env var, set in .env; never in a file the factory writes
  tools:
  - {name: lookup, side_effect: READ, risk: R0, timeout_s: 10}
```

- **The sandbox stays offline.** The factory's MCP client connects from the factory process; the run's sandbox
  keeps `--network none`. The model reaches the server only through the tools the recipe and task allow.
- **Changes only in a test tenant.** A remote tool that isn't `READ` must land its changes somewhere the factory can
  reset before each run and grade afterwards, so the catalog refuses it unless the server declares `tenant:` (see
  [Test tenants](#test-tenants)).
- **Opt-in.** Live remote calls make a run depend on a service the factory doesn't control, so they need
  `HF_ALLOW_REMOTE=1`. Such runs record `snapshot.remote_servers` (url@version), and a new server version starts new
  cells. A server that reports a version at initialize must report the pinned one, or provisioning fails; what it
  reported is in `snapshot.remote_versions` (empty: it reports none, so the pin can't be checked). `hfx tools
  freeze` checks remote servers only with the opt-in and keeps their hashes otherwise.
- **Failures are the environment's.** A remote server that is unreachable at the start fails provisioning with the
  reason (a missing token names its variable). One that fails mid-run turns that tool call into an error the model
  sees, marked `infra` and excluded from Q like an injected fault. Each session lives in its own task, so a dead
  server never cancels the run.
- **Record once, replay offline** (EXT-08). `HF_REMOTE_MODE=record` (with the opt-in) saves every remote answer
  under `var/remote/<server>@<version>/` (`HF_REMOTE_RECORDINGS` to put it elsewhere, e.g. a folder you commit).
  `HF_REMOTE_MODE=replay` answers the same runs from that recording with no network, no token and no opt-in, so
  they repeat exactly and can run in CI. Replayed runs show transport `REPLAY` and `snapshot.remote_mode: replay`;
  a call the live run never made gets an error marked `infra: replay_miss` (the environment, excluded from Q). A
  new server version needs a new recording. Answers are keyed by the call and the run's scope (task id and version,
  seed, the tenant's seed state), so two tasks that make the same call never share an answer. Recordings hold
  answers only, never request headers.

### Other agents as tools

Another team's agent, exposed as a remote MCP server, is a tool like any other, with `agent:` on its spec (EXT-10,
ADR 0005):

```yaml
- id: analyst
  transport: streamable_http
  url: https://analyst.internal.example/mcp
  version: "3.2"
  auth_env: ANALYST_MCP_TOKEN
  tools:
  - name: ask
    side_effect: READ
    risk: R1                      # declared like any tool: the policy and tier rules apply unchanged
    timeout_s: 120
    agent: {max_calls: 3, price_in: 0.5, price_out: 1.5}   # calls per run; reference USD per million tokens
```

- **It pays for what it uses.** Each answer must report the agent's own model usage in its result metadata:
  `_meta["harness-factory/usage"] = {tokens_in, tokens_out, usd?, model?}`. The tokens go on the tool call's span
  and count toward the run's `max_tokens` and its cost; `usd` (or, when absent, the reference rates) counts toward
  C. `rec.cost.agent_calls / agent_tokens / agent_usd` say how much of the run's total was the agent's.
- **No usage, no answer.** An answer without usage would be free work, so it is withheld and the call is marked
  `infra: agent_usage_missing` (the environment, excluded from Q). An error without usage (the agent refused
  before working) charges nothing.
- **Bounded.** Calls past `max_calls` in one run (delegated helpers included) are refused and count as invalid in Q.
- **Remote only, read-only.** An agent calls a model, which the offline sandbox can't reach, so agent tools must be
  on a remote server; one that changes state needs a test tenant, as below. Record and replay work as above, usage
  included, so a recorded agent's runs repeat offline at the same cost.
- **Identity.** `max_calls` is part of `env_hash`; prices are not. Tools without `agent:` keep their hashes.

### Test tenants

A remote server whose tools change state (refund an order, file a ticket) declares a test tenant (EXT-07, ADR 0006):

```yaml
- id: orders
  transport: streamable_http
  url: https://orders.sandbox.example/mcp
  version: "5.1"
  tenant: {header: HF-Tenant, reset: _tenant_reset, export: _tenant_export, drop: _tenant_drop}   # the defaults
  tools:
  - {name: get_order, side_effect: READ, risk: R0}
  - {name: refund_order, side_effect: REVERSIBLE, risk: R1}
```

- **One tenant per run.** Provisioning gives each run a fresh tenant id, sent as the `HF-Tenant` header on every
  request (the model can't set or see it), and calls `_tenant_reset(state=…)` with the task's seed,
  `env_seed.mock_api_state[<server>]`. Concurrent runs never see each other's changes, and each starts from the
  seed. The session's close calls `_tenant_drop` (best effort).
- **The server's side.** It keeps one state per tenant header and exposes the three admin tools. The factory hides
  them from discovery and never declares them as tools, so the model can't call them. `tests/remote_tenant_server.py`
  is an 80-line example.
- **Graded on the tenant.** After the run, `_tenant_export()` returns the tenant's state as JSON. A `remote_equals`
  check reads it with a JSON pointer:

  ```yaml
  - {id: refunded, kind: remote_equals, weight: 1.0, path: "orders:/orders/ord-7/status", text: refunded}
  - {id: others_untouched, kind: remote_equals, weight: 0, critical: true,
     path: "orders:/orders/ord-8", text: '{"status": "paid", "amount": 80}'}
  ```

  `text` is compared as a string, or as JSON when it parses. A run whose tenant can't be read afterwards is
  `INCOMPLETE` (the environment), never scored. The end state is in `snapshot.tenant_state`.
- **Replay.** Reset, calls and export are recorded. The n-th repeat of a call has its own answer, so a lookup before
  and after a change replays correctly. The recorded end state counts only when the replay changed state with
  exactly the calls the live run made; otherwise the run is `INCOMPLETE` (`replay_diverged`), so a replay that skips
  the work can't inherit the live run's result.

## Provider calibration

A canonical model served by two providers gets two cells until the providers are shown to behave alike (ADR 0010).
`hfx calibration` measures it:

```bash
hfx calibration run gpt-oss-120b --providers groq,openrouter --run --seeds 2   # run missing pairs, then judge
hfx calibration list                                                           # saved verdicts
hfx calibration cells                                                          # cells, equivalent providers pooled
```

- **Pairs** are runs that differ only in the provider: same task, recipe, model, seed and tool environment, clean
  and scored, each served by the model id its catalog entry names now. Runs store the tool environment apart from
  the served model id (`tools_hash`, `served_id`) for this.
- **Verdict** from the paired E0 difference and its bootstrap 95% interval:
  - `equivalent` when the interval is within ±0.1 (`--margin`);
  - `different` when it excludes 0;
  - otherwise, or with fewer than 20 pairs (`--min-pairs`), `inconclusive`.

  Verdicts are frozen in the results store, and the newest per pair is in force.
- **Pooling** happens only in `hfx calibration cells`, only for `equivalent` pairs whose providers still serve the
  calibrated model id. A pool of three or more needs a verdict for every pair. Cost stays per provider (each run
  against its own provider's median); the cell is `groq+openrouter` with `tools:<hash>` as its environment.
  `hfx results --cells` and the selector are unchanged.

## Allocation planner

When more than one provider serves a model, `hfx allocate` decides before the campaign which provider serves
each cell (ADR 0011). `hfx smoke` is unchanged; allocation is opt-in.

```bash
hfx allocate plan --models gpt-oss-120b,llama-3.3-70b --recipe v2-base,v3-staged --seeds 2   # decide, log, save
hfx allocate plan ... --strategy cost --deadline-days 3 --budget-usd 5 --run -j 8            # and run it
hfx allocate run <id> --resume                                                               # finish a saved one
hfx allocate show <id>
```

- **One provider per model, unless calibrated.** Without a current `equivalent` verdict (`hfx calibration`),
  every cell of a model goes to one provider. With one, the equivalent providers form a pool. The pool splits the
  model's runs in proportion to each member's remaining capacity today, interleaved so every cell gets runs from
  each member, and they aggregate as one pooled cell. A `different`, `inconclusive` or stale verdict (the served
  model id changed) never splits.
- **Capacity** is today's remaining quota in each provider's bucket (the ledger, `scope: model` included), minus
  what earlier models in the same plan already took from it. Runs are estimated as the projection estimates them.
  `--strategy capacity` (the default) picks the group that finishes soonest with the most room left, then the
  cheapest. `--strategy cost` picks the cheapest, then the soonest. `--providers` narrows the candidates.
- **Refusals** follow the preflight: a model nobody serves, a model whose candidates are all spent today or
  exceed `--deadline-days`, or a plan over `--budget-usd`. The table gives the reason per candidate, and nothing
  is saved (exit 2).
- **Logged and frozen.** Every decision, refusals included, goes to `var/decisions/` with every candidate's
  numbers and the verdict in force for each pair. A runnable allocation is saved, content-addressed, in
  `var/allocations/<id>.json`. `run <id>` uses those frozen specs, so `--resume` finds the same runs after quota
  has moved. Re-planning mid-campaign (MOD-07) is not part of it yet.

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
  seeds/pricing/          a small Python package with tests, committed as a git repository by the sandbox
  seeds/biz/              back-office state (contacts, invoices, credit notes, tickets) + the refund policy
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
  shapes/                 Harness protocol, ShapeRegistry, V1 to V5 shapes          (B4)
  provision/              Provisioner, task class key                               (B5)
  providers/              ProviderCatalog, QuotaLedger                              (B6)
  runner/                 RunKey, CostLedger, run_one / run_reference / smoke       (B7)
  grading/ results/       graders, EffectivenessRecord, ResultsStore                (B8)
  mlops/                  MLflow tracker                                            (B9)
  mcp/                    MCPClient, MCPSessionPool, ToolCatalog                    (B11)
  observability/          Span, Tracer (JSONL + OTLP)
  authoring/              AuthoringService: staged, validated catalog writes (ADR 0009)
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
| V5 orchestrator | `v5-orchestrator` | V4's plan and replan, but each step goes to a worker agent (a delegate call to `v2-base`) that starts from a fresh conversation holding only the task, earlier workers' reports and its part; the orchestrator calls no tools | long tasks whose steps don't need each other's details; keeps each context small | steps that need what an earlier step saw, not just its one-line report |
| V4 planner | `v4-planner` | The planner slot writes a numbered plan from the task and the tool list (no tool calls); the executor runs it one step at a time, each step a bounded tool loop; a step that replies `BLOCKED:` sends the remaining work back to the planner once | long known-path tasks a model loses track of | tasks whose path only shows up while working; cost: the plan and step prompts |

V3 stage tools are enforced: a write attempted in a read-only stage is refused and recorded as a
`tool_not_allowed` violation. Stage transitions are `handoff` spans, so the trace shows the path through the
graph. A V3 recipe declares its graph under `workflow:` (see `catalog/recipes/base/v3_staged.yaml`).

A V4 recipe declares `planning:` (`max_plan_steps`, `step_max_steps`, `max_replans`, and the three prompts under
`prompts/plan/`). The planner runs on the run's model (a pinned planner model is refused: only the executor's model
is in the RunKey). Plans, replans and steps are `handoff` spans; tool calls carry scope `step:<n>`; the plans and
each step's result are in `snapshot.plans` and `snapshot.plan_trail`. A plan longer than `max_plan_steps` has its
tail merged into the last step, an unnumbered plan becomes one step (`plan_unparsed`), and a step past its cap is
ended like a V3 stage (`step_cap`). See ADR 0007.

A V5 recipe declares `planning:` with a `worker_prompt` (`prompts/plan/worker.j2`) and a `delegation:` (the worker
recipe, with `max_calls` covering every step of every plan). Workers are the delegate machinery: same sandbox, same
gateway, a grant that is a subset of the orchestrator's, a budget capped by what the run has left and charged back
to it. A worker that replies `BLOCKED:` or stops early triggers the replan. Each worker is a `delegate.run` span with
scope `step:<n>`; its conversation is in `snapshot.delegations`. See ADR 0008.

Offline comparison (`scripted/replay`, the 20 DEV + TEST tasks, cost baseline over these five recipes):

| Recipe | Success | E₀ | C | E | Tokens |
|---|---|---|---|---|---|
| `v1-base` | 1/20 (fs-01 only) | 0.100 | 0.054 | 0.094 | ~4,500 |
| `v2-base` | 20/20 | 1.000 | 0.257 | 0.954 | ~28,000 |
| `v3-staged` | 20/20 | 1.000 | 0.615 | 0.887 | ~62,000 |
| `v4-planner` | 20/20 | 1.000 | 0.696 | 0.878 | ~81,000 |
| `v5-orchestrator` | 20/20 | 1.000 | 0.446 | 0.919 | ~46,000 |

V2 to V5 tie on E₀; efficiency C separates them (see *Cost and efficiency*). V5 costs less than V4 with the same
plan because each worker starts from a short conversation, where V4's executor carries every step's history.
Offline, the scripted model never loses track, so planning can only cost; whether it pays with real models on longer
tasks is a Phase 2 question.

**Structured plans, concurrent workers, parallel tool calls.** `planning.structured: true` makes V4 and V5 ask for
a JSON plan (steps with `id`, `instruction`, tool hints, `depends_on`, `done_when`), validated before anything runs.
A plan that doesn't validate is re-asked with the reason (`planning.plan_retries`, default 1; each re-ask is an
invalid call in Q), then the run stops as `malformed_output`. JSON mode is sent when the model's caps declare
`structured_output`. V4 runs the steps in dependency order; V5 starts each step once its dependencies have finished,
up to `planning.max_parallel` workers at a time, each with a budget carved from what the team has left, and replans
only after running workers finish. `config.parallel_tool_calls: true` runs the calls of one reply concurrently when
all of them are READ; any call with a side effect makes the batch sequential, and results go back in call order.
Matrix recipes: `v4-structured`, `v5-parallel`, `v2-parallel-tools` (ADR 0007 and 0008, "Later").

## Recipe matrix

The factory exists to find which harness wins for which task class, across tools, skills, prompts, config and
memory, not only shape. A **matrix recipe** is a base recipe plus one change, so a difference in its results
can be put down to that change:

```yaml
# catalog/recipes/matrix/v2_recover.yaml
id: v2-recover
base: v2-base                    # everything from v2-base ...
config: {recover_malformed: 2}   # ... except this
```

Mappings merge key by key and anything else is replaced. The hash is taken of the resolved recipe, base id
included, so editing a base changes every recipe built on it. Existing recipe hashes are unchanged.

| Dimension | Recipe | The one change |
|---|---|---|
| prompt | `v2-plan-check` | System prompt asks for a numbered plan with the first tool call and a check before the final answer (`prompts/system/plan_check.j2`) |
| config | `v2-recover` | When the provider rejects a reply as malformed (a call to a tool that doesn't exist, Phase 1's `code-01`), re-ask with the error and the real tool list, at most twice per run (`config.recover_malformed`) |
| tools | `v2-biz` | Adds the `biz` server: a mock back office with CRM, invoicing and ticketing tools over the run's business state (see [Business systems](#business-systems)) |
| tools | `v2-search` | Adds the `search` server: `search.find` (files by name) and `search.grep` (contents, `path:line: text`). Phase 1's `code-01` model reached for a search tool that wasn't there |
| tools | `v2-fsx` | Adds the `fsx` server: `fsx.edit` (exact-string replace), `fsx.append`, `fsx.move`, `fsx.mkdir` and `fsx.tree`. `fs` can only rewrite a whole file |
| skills | `v2-skills-upfront` | Every skill's how-to appended to the system prompt (about 5k characters) |
| skills | `v2-skills-on-demand` | An index (name and one line each) in the prompt, plus a `skills.load` tool to read the ones that fit |
| tools | `v2-code` | Adds the `code` and `git` servers (ADR 0012): run the tests or a script, and status, diff, log, commit, branch and checkout over a git-seeded workspace. The only recipe that reaches `code-02` and `code-03` |
| delegation | `v2-delegate` | A `delegate__run` tool: the model can hand a sub-task to a helper (another `v2-base`) that works in the same sandbox (`delegation: {recipe: v2-base, max_calls: 2, max_depth: 1}`) |
| config | `v2-compact` | Context compaction: past 75% of the model's `context_len`, older turns are replaced by a summary the model writes; the last two turns stay verbatim (`config.compaction`, ADR 0015) |
| tools | `v2-docs` | Adds the `docs` server: read and edit `.docx` (`docx_read`, `docx_replace`, `docx_append_paragraph`) and `.xlsx` (`xlsx_sheets`, `xlsx_read`, `xlsx_write`), read `.pdf` (`pdf_text`, `pdf_info`). The office tasks name these tools, so plans run them only on recipes that open the server |
| retrieval | `v2-retrieval-bm25` | A `retrieve__search` tool over the task's corpus, BM25, 5 passages per call (`retrieval: {mode: bm25, top_k: 5}`); see [Retrieval](#retrieval) |
| retrieval | `v2-retrieval-hybrid` | The same tool with hybrid search (dense + BM25, reciprocal rank fusion) and a reranker (`retrieval: {mode: hybrid, top_k: 5, rerank: true}`) |

**Skills** live in `catalog/skills/<id>/SKILL.md` (a `name` and `description` header, then the body). Five
general how-tos so far: `sql-analysis`, `sql-changes`, `editing-files`, `file-exports`,
`requests-and-policies`. A skill's full text is part of the recipe hash. On demand, the provisioner installs
the recipe's skills in the run's state folder, outside the workspace: the model reads them only through
`skills.load` and can't change them. `skills.load` is granted to every task (`granted_by: []`) because it reads
harness material, nothing from the task's world.

> **Caveat:** these skills were written by the same author as the tasks, held-out ones included. Skill gains on
> this catalog are an upper bound. Tasks written later, by an author who hasn't seen the skills, give the honest
> estimate.

**New tools without task edits.** A task's `allowed_tools` decides what a run may call, and editing every task
would change every task hash. A READ tool that reveals nothing beyond other tools declares them instead:
`granted_by: [fs.list_dir, fs.read_file]` in `tools.yaml`. Any task allowing those allows it too. So `code-01`
gets `find` and `grep`, `mix-01` (no `read_file`) gets only `find`, and SQL-only tasks get neither.
`granted_by` never grants a tool with side effects. The search server is a separate MCP server, so recipes that
don't open it keep their environment hash (`v2-base` kept Phase 1's `87cf105d96ea` until tool descriptions joined
the environment hash, a one-time reset of every environment hash).

**Write tools without task edits (`fsx`).** `fsx` tools with side effects use `granted_by` too, under a stricter
rule (`ToolCatalogFile.grantable`): a tool with side effects is granted only when the task allows every tool it
names *and* one of them is at least as strong in side effect and risk as the tool itself. So `fsx.edit` (granted
by `fs.read_file` + `fs.write_file`), `fsx.append` and `fsx.mkdir` (`fs.list_dir` + `fs.write_file`) reach only
tasks that already allow file writes; read-only tasks such as `ops-03` get only `fsx.tree` (`fs.list_dir`), and
SQL-only tasks get nothing. `fsx.move` makes its source disappear, which `write_file` can't, so it also needs
`fs.delete_file`: no current task allows that, and a task that wants `move` names `fsx.move` itself. A granted
tool never raises a task's class, and its calls fall under the recipe's permission rule for its own side effect
(all five are REVERSIBLE or READ, so `v2-base`'s `IRREVERSIBLE: DENY` refuses `fs.delete_file` but not
`fsx.move`). `fsx.edit` refuses an `old` text that is missing or occurs a different number of times than `count`
(and says how many times it occurs); `fsx.append` only extends existing files; `fsx.move` never overwrites;
`fsx.tree` lists at most 200 entries. Recipes that don't open `fsx` keep every hash.

**Delegation (agent as a tool).** `delegation: {recipe, max_calls, max_depth}` gives any shape a
`delegate__run(task)` tool (ADR 0003). The helper runs the named recipe on the sub-task in the same sandbox
(same files, database and MCP sessions) through the same model gateway, so its tokens, cost and quota count in
the parent run. Its tools are its own slice intersected with the parent's, its permissions are the stricter of
the two per side effect, and its budget is its own config capped by what the parent has left; what it uses is
charged back to the parent, so delegating never buys extra steps. A V3 recipe offers the tool only in stages
whose tool patterns allow `delegate.run` (`v3-staged`'s act stage, `"*"`). The helper's conversation is kept in
the run's transcript (`snapshot.delegations`), and its tool calls are traced under scope `delegate:<n>`. The
helper's own hash is part of the delegating recipe's hash, and `load_recipes` refuses delegation cycles. A
helper that stops early is recorded as `delegate_stopped`, not as the parent's overrun. A helper that ends on
infrastructure (INCOMPLETE) or a setup failure (ERROR) ends the parent run the same way, so the parent is never
graded on work an outage cut short. Offline, with
`scripted/delegate` (hands the whole task over when it can), `v2-delegate` passes 20/20 like `v2-base` at about
37% more tokens, so its E is lower: delegation has to earn its cost on real models.

Rules for a dimension:

- **It lives in `shapes/common.py`,** so every shape gets it the same way. Retry-after-rejection works in V2 and
  V3 alike. A rejected reply counts against a V3 stage cap, and V1's single step leaves no room to re-ask.
- **It is never free.** A rejected reply uses a step, and the trajectory grader counts it as an invalid call
  (with or without retry). So recovering beats stopping, but never matches a clean run.
- **It has an offline test,** with a scripted model that triggers it. `scripted/invent` makes its first call to
  a tool that doesn't exist. On the 20 visible tasks, `v2-base` passes 0/20 with it and `v2-recover` passes
  20/20, at E₀ 0.930 rather than 1.0.

## Retrieval

`retrieval: {mode, top_k, rerank}` gives any shape a `retrieve__search(query)` tool over the task's own corpus
(KNW-02, ADR 0017). Modes: `bm25`, `dense`, `hybrid`; `rerank` reranks a pool of `top_k x 3` candidates down to
`top_k`. It is built from LlamaIndex's own components (optional extra `retrieval`, included in `dev`):

| Mode | LlamaIndex |
|---|---|
| all | `Document` per corpus file, `SentenceSplitter` (64 words, overlap 12; passages keep exact character offsets) |
| `bm25` | `BM25Retriever` (llama-index-retrievers-bm25: bm25s, English stemmer and stopwords) |
| `dense` | `VectorStoreIndex` on `QdrantVectorStore`, qdrant-client in local in-memory mode (no server, no files) |
| `hybrid` | `QueryFusionRetriever` over the two, reciprocal rank fusion, one query (no LLM query generation) |
| `rerank` | a `BaseNodePostprocessor` on the first stage's results |

**Offline by design.** CI makes no model downloads, so the embedding is `HashingEmbedding` (a `BaseEmbedding`:
signed feature hashing of words and word pairs; deterministic, ranks by shared vocabulary) and the reranker is
`TermCoverageRerank` (share of query words in each candidate). Both are named in the run's environment hash; a
real embedding model or cross-encoder would be a different environment.

**Where it runs.** In the factory process, not in the sandbox (sandbox servers may import only `mcp` and the
standard library). The tool is a local tool of the run's `ToolExecutor`, so a call goes through the same checks
as an MCP call: allowance, V3 stage filter, arguments, READ permission, faults, timeout, a `tool_call` span and a
budget charge. Its answer is JSON: a note that the passages are data, not instructions, then source, offsets,
score and text per passage.

**When it is offered.** The recipe retrieves, the task's seed has a top-level `corpus/` folder (`.md`, `.txt`),
and the task allows `fs.read_file` (the passages are from files the model could read anyway). Otherwise the
recipe runs exactly like its base. The provisioner indexes the corpus from the task's seed before the run's clock
starts; the run's snapshot (and transcript) records the index id, its size, the framework versions and the
evidence: every passage returned, by source and offsets. A V3 stage offers the tool if its patterns allow
`retrieve.search`; a delegated helper gets it only if its own recipe retrieves.

**Identity.** The retrieval config is in the recipe hash (unset recipes keep theirs); for retrieval recipes,
`env_hash` also covers the engine and the six framework versions. The corpus is in the task hash through the seed.

**Tasks.** `rag-01`, `rag-02` (TEST) and `rag-03` (HELD_OUT) use the `kb` seed: 60 short documents under opaque
names (`corpus/kb-001.md` ...), so listing the folder says nothing and reading every file costs more than the
budget. Answers cite the document id. Reference plans call `retrieve.search` as an optional step: the scripted
model skips it when the tool isn't offered, and `hfx reference` checks that the retrieval step returns every file
the plan reads next.

```bash
uv run hfx smoke --recipe v2-retrieval-bm25,v2-retrieval-hybrid --models scripted/replay --strict
```

## Selector

The payoff: given a task, which recipe? `catalog/selector.yaml` holds the rules; `src/harness_factory/select/`
implements SEL-01 to SEL-06 from the requirements.

```bash
hfx select show     --model gpt-oss-120b --provider groq     # the pick per task class, with its evidence
hfx select freeze   --model gpt-oss-120b --provider groq     # content-addressed policy in var/policy/<id>.json
hfx provision --task code-01 --model gpt-oss-120b --provider groq [--policy <id>]   # select, log, run
hfx select evaluate --model gpt-oss-120b --provider groq     # held-out: selector vs global vs rules vs hand-picked
hfx report --out docs/reports/phase-2.md                     # markdown report from the store (paired deltas)
```

**Reports.** `hfx report` writes a markdown campaign report from the store. It has six sections:
1. scope;
2. status per recipe and model;
3. each matrix recipe against its base, paired by task and model (mean ΔE, wins/losses/ties, bootstrap 95%
   interval, and a verdict only when the interval excludes 0);
4. the selector's picks;
5. scored runs short of SUCCESS, grouped by how they failed;
6. runs that weren't scored, by cause.

The Phase 2 plan is in `docs/runbooks/phase-2.md`.

How a decision is made, for one task class, model and provider:

1. **Hard rules prune (SEL-01).** A recipe is dropped if any of these fails:
   - its tier covers the class's risk;
   - its shape fits the step depth (V1 only for single steps);
   - the model can do what the shape needs;
   - the recipe validates;
   - given a task, it reaches every tool the task allows.
2. **Measured E ranks (SEL-02).** The survivors are ranked by cost-adjusted E (with R once faulted runs exist),
   with a bootstrap 95% interval. Only cells with at least `min_runs` scored runs (default 3) are ranked; the
   rest are reported as "below min".
3. **Only current evidence counts.** A cell is used only if its recipe hash and environment hash are the
   recipe's current ones. Results from an older version of a recipe never choose for the new one. Rows of a task
   whose definition changed since (same id and version, different task hash) are left out too, here and in
   `results --cells` and `report`.
4. **Cold start (SEL-03).** With nothing ranked, the first matching rule-based default that survives the hard
   rules is used: `UNKNOWN_PATH` → `v3-staged`, otherwise `v2-base`. If a model can't call tools, that leaves V1.
5. **Close call (SEL-04).** When the top two intervals overlap, both are chosen, `provision` runs both, and
   their results update the table.
6. **Logged (SEL-05).** Every decision used to provision a run goes to `var/decisions/` with its rule, the
   ranked and pruned recipes, every number used, the baseline and the selector config hash.

**Held-out evaluation (SEL-06).** `hfx select evaluate` picks a recipe for each held-out task four ways:
- the selector;
- the single best global recipe;
- rules only;
- `hand_picked` from `selector.yaml`.

Held-out runs are never used as evidence. Strategies that agree share a run, done runs are reused, and every
strategy is scored against one cost baseline that includes the evaluation runs. So a strategy that solves a task
more cheaply scores higher. v1 is done when the selector beats the best single recipe here.

**Pooled evidence (opt-in, ADR 0010).** With `pool_providers: true` in `catalog/selector.yaml`, or `--pool` on
`hfx select show|freeze|evaluate` and `hfx provision`, a provider in a pool of providers calibrated as
`equivalent` for the model (`hfx calibration`) decides from the pooled cells, so its pool partners' runs count as
evidence. A pooled cell is current only if every run in it has the recipe's current recipe hash, the recipe's
current tools hash (`tools_hash`), and the served model id its provider's catalog entry names now. Cost stays per
provider. The decision, and so its log in `var/decisions/`, carries a `pool` entry: the pool label, the served ids,
the ids of the `equivalent` records it rests on, and the scored runs from each provider (in total and per recipe).
For a held-out evaluation every verdict in force is judged again on the evidence rows alone, so a pool never rests
on held-out pairs. A provider in no pool decides exactly as with pooling off. Off, the selector, its decisions and
frozen policies are unchanged (same ids).

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
  - Each clean run's composite is recomputed with R, which counts as R·O like Q·O (w_r joins the weighted
    mean), so a run that achieved nothing stays at 0.
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
| **Harness overran** | step/tool/token/wall budget, V3 stage cap, V4 step cap, context overflow (incl. 413 request too large), malformed model output (`tool_use_failed`), graph recursion limit | graded on the environment; **outcome × 0.5, status at most PARTIAL**; the reason is a violation | yes |
| **Infrastructure** | provider 5xx, timeout, connection, 429 after retries, daily quota | INCOMPLETE | no (`excl.`) |
| **Setup or crash** | auth, unknown model id, a rejected request, a bug in factory code | ERROR (recorded with trace and transcript) | no (`excl.`) |

Why an overrun is graded at all, but penalised:

- **Graded.** Zeroing it was unfair between shapes: a V3 stage cap ends one stage, while a V2 step cap ends the
  whole run.
- **Penalised.** Without the penalty, a harness that solves early and keeps calling until a cap would tie a
  clean run. Q only catches exact repeats, and as a "success" the run would also inflate the cost median.

Cells are keyed by **task class × recipe × model × provider × environment hash**. Provider and environment stay
separate until cross-provider equivalence is calibrated (P2).

## Context management

ADR 0015. Before every model call the prompt (messages and tool schemas) is estimated offline: UTF-8 bytes ÷ 3,
plus 4 per message. That over-counts prose and JSON, so it errs towards acting early. Each `model_call` span records
`est_tokens_in` and `context_len` next to the provider's `tokens_in`.

- **Too big to fit.** A prompt over `context_len − max_output_tokens` (the model's window minus the reply budget)
  stops the run before the call, as `context_overflow`: a harness stop, the same in every shape.
- **Compaction** (`config.compaction: {at, keep_last_turns, prompt}`, matrix recipe `v2-compact`). Past
  `at × context_len`, the older turns are replaced by a summary that the run's model writes
  (`prompts/compaction/summary.j2`). The task message and the last `keep_last_turns` turns stay verbatim, and a tool
  call is never separated from its result. The summary uses a step and its tokens, like any call. It is traced as a
  `compaction` span, and the replaced messages stay in the transcript (`snapshot.compactions`).
- **Long tool output** (over 16,000 characters) is saved in the run's state folder, never the workspace. The model
  sees the first 6,000 and last 2,000 characters and a handle, and from then on is offered `spill__read(handle,
  page)` to read it in 8,000-character pages. Each read is a tool call, charged and counted in Q. At most 4M
  characters and 64 spills per run; past that, output is cut as before.
- **Validation.** `hfx validate` checks every slot's `max_output_tokens` against the `context_len` of every served
  model, and at provisioning against the run's model.

## Run identity

A RunKey changes whenever any of these changes:

- the task (instruction, seed data, oracle; not its split or tags);
- the recipe, including the source of every prompt template it references;
- the model, the provider and the provider's model id;
- the declared tool specs, including their schema and description hashes;
- the seed.

The seed is never part of the recipe hash, and a recipe can't set one (`seed_is_campaign_axis`). Tool schemas and
descriptions are frozen in `tools.yaml`: the description is what the model reads to decide what a tool does, so a
changed one is drift, like a changed schema.

```bash
hfx tools freeze           # rediscover every tool and write its schema and description hashes
hfx tools freeze --check   # exit 1 if a server's tool schemas or descriptions changed (drift)
```

**Calls stronger than their tool.** A tool's declared side effect holds for most calls, not all. `sql.execute` is
REVERSIBLE (R2), but a statement that destroys data wholesale (`DELETE` with no `WHERE` on a column, `DROP`, `ALTER
TABLE ... DROP`) is IRREVERSIBLE (R3): its `escalation:` in `tools.yaml` names a classifier
(`env/mcp/effects.py`), and the ToolExecutor applies the recipe's permission for that side effect to the call, so
`IRREVERSIBLE: DENY` refuses it like `fs.delete_file`. A row delete scoped by a condition stays REVERSIBLE, like an
`UPDATE ... WHERE`.

## Cost and efficiency

C asks: what did this run cost compared with what the task usually costs?

```
C = min(cost / median, 2) / 2         0 = free, 0.5 = at the task's median, 1 = twice the median or more
E = max(0, E₀ − w_c · C)              w_c from the task's weight profile (0.25 read, 0.15 reversible, 0.10 irreversible)
```

C is linear below the median too, so a harness that solves a task for half the usual cost ranks above one that
solves it at the usual cost.

- **Median per task, over successful runs only.** Tasks in one class differ in size, and counting failures would
  make the median the price of giving up early. A task nobody solved falls back to all its scored runs
  (INCOMPLETE and ERROR runs never set a median).
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

33 tasks over seven seeds. The first five are the P0 smoke set. The other twenty-eight each hide a trap that a
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
| `docs-03` | known path, reversible, R1 | DEV | Handover note (rubric check): pass the order deletion on as a to-do, or handle the ticket instead |
| `code-02` | known path, reversible, R2 | DEV | Fix a failing test and commit: editing the test, special-casing the tested order, or not committing each cost points (hidden tests) |
| `code-03` | known path, reversible, R2 | TEST | Fix VAT rounding on a new branch: committing on `main`, or rounding by adding half a fils, fails |

`code-02` and `code-03` (ADR 0012) use a fourth seed, `pricing` (a small package with tests, in a git
repository), and run under `v2-code`.
| `biz-01` | unknown path, irreversible, R2 | DEV | The ticket asks to credit two torn mats and the delivery; one mat was credited already, VAT is added, delivery never is |
| `biz-02` | unknown path, read, R0 | TEST | Overdue total for a company: two contacts, a credit note, a look-alike company, an invoice not yet due |

The `biz-*` tasks need the `biz` server, which only `v2-biz` opens. Campaigns leave out a task x recipe pair
whose recipe can't call every tool the task allows, a server it doesn't open included (`provisioner.reaches`; `hfx smoke` lists what it left out),
so `v2-base` still runs the same 20 visible tasks. `biz-01` is irreversible by class because its allowance
(`biz.*`) includes `inv_mark_paid`, which the policy says support must never call.
| `office-01` | known path, reversible, R1 | TEST | Price list from a PDF quotation: prices exclude VAT, one item isn't on the list, page 2 quotes the withdrawn quotation |
| `office-02` | known path, reversible, R1 | DEV | Fill in a letter: the expiry date, not the quote date; the withdrawn quotation's number stays; sign in the Signature style |

The two office tasks (seed `office`, built by `scripts/taskgen/office.py`) need the `docs` server. A plan leaves
out any task × recipe pair whose recipe can't call a tool the task names (`hfx smoke` lists them), so they run
on `v2-docs`, not on the base recipes.
| `rag-01` | unknown path, read, R0 | TEST | Refund limit: a superseded policy, an unapproved draft and the wholesale desk's limit sit next to the current one |
| `rag-02` | unknown path, read, R0 | TEST | Service port: the catalog entry is stale, a change notice moved it, a later move was cancelled |
| `rag-03` | unknown path, read, R0 | HELD_OUT | Incident cause and owner: the catalog's owner is stale, and INC-2219 looks like INC-2291 |

**Splits.** DEV is for iterating on recipes and TEST for comparing them. HELD_OUT is never run by `smoke` or
the parametrised tests (`hfx reference` still checks it), so a recipe chosen on DEV and TEST can be confirmed on
tasks nobody tuned against.

## Writing a task

Oracle checks, with weights summing to 1:

- `file_equals`, `file_contains` and `file_regex` (both with `negate`), `file_absent`, `json_valid`, `sql_rows`,
  `output_regex`. Regexes run with `re.MULTILINE`; add `(?s)` to match across lines. A check is refused at load
  time if it lacks the fields its kind needs or its regex doesn't compile.
- `tool_called`: the answer must be grounded in a successful call.
- `tests_pass` (ADR 0012): pytest on the end state passes (exit code 0). `path` is what to run (default `.`).
  `text` is a hidden test module, run from outside the workspace, which the agent never sees. `pattern` is a `-k`
  expression. It runs inside the sandbox with the grader's own empty config and `--noconftest`.
- `git_log_contains` (ADR 0012): a commit on `ref` (default `HEAD`) after the seed commit, whose message matches
  `pattern` and which touches `path` if one is given. With `negate` it means no such commit, which is the
  "leave `main` alone" guard. The task needs `env_seed.git: true`.
- `biz_equals` and `biz_count`: the business state after the run (see [Business systems](#business-systems)).
- `xlsx_cell` (`path`, `sheet`, `cell`, `expected`; omit `expected` for "empty"; `tolerance` for numbers, and
  text is never a number), `docx_contains` (`text` in any paragraph or table cell) and `docx_paragraph_regex`
  (some paragraph matches `pattern`, optionally with a given `style`; `negate`: none does). They read office files
  in the factory process, so they need the `docs` extra.
- **An answer check must refuse a hedge.** "It is 2, 3 or 4" must not pass a check for 3. Anchor the pattern on
  `\A`, not `^`: with `re.MULTILINE`, a `^` lookahead is tried again at every line start, so "wrong\nright"
  slips through. `scripts/taskgen/tasks.py` has the builders: `one_number` passes one number and no other,
  except numbers the instruction uses; `one_of` passes one value and none of the others the seed offers.
  `tests/test_task_traps.py` hedges every `output_regex` check's reference answer with each wrong answer from
  the trap scripts, so a task with an answer check needs at least one wrong-answer trap.

A check with `weight: 0, critical: true` is a **guard**: it earns nothing, but failing it zeroes the run. Use
guards for "don't touch anything else", so a run that does nothing can't collect preservation credit.

Every task ships a `reference.py`. `hfx reference` must pass before the task is marked `verified`.

- **A reference must fit every gated shape.** The V3 gate runs it through `v3-staged`, so it needs at most 3
  read calls before its first write (the inspect stage's cap is 4 calls, one of which ends the stage) and at
  most 5 calls after that (act's cap is 6).
- **Add a trap test for every trap.** One script per wrong approach in `tests/test_task_traps.py`, naming the
  check that must fail.
- **A run that does nothing scores 0,** on every task. The same file runs a do-nothing script against the whole
  catalog. A check that passes on the untouched seed ("no duplicates", "valid JSON", a negated "never mentions
  X") is a guard (`weight: 0, critical: true`) or is tied to the change, never weighted on its own.

### More check kinds

| Kind | Fields | Passes when |
|---|---|---|
| `sql_rows_unordered` | `query`, `rows` | the query returns these rows in any order, duplicates counted (a multiset) |
| `number_close` | `value`, `abs_tol` / `rel_tol`; the number from `query` (one SQL value), `path` (+ optional `pattern`), or the final answer (optional `pattern`) | \|actual − value\| ≤ max(`abs_tol`, `rel_tol` × max(\|actual\|, \|value\|)); neither tolerance set = `rel_tol` 1e-9. NULL, NaN, no rows, several values or text that isn't a number fail |
| `json_schema` | `path`, `json_schema` (inline) | the file is JSON and validates against the schema (checked at load time) |
| `output_exact` | `text`, `normalize: [whitespace, case]` | the final answer equals `text` after trimming both ends, and optionally collapsing whitespace and ignoring case |

`sql_rows` stays an exact, ordered comparison. Use it when an `ORDER BY` fixes the order and the values are
integers or text. Use `sql_rows_unordered` for a query without a defined order, and `number_close` for anything
computed in floating point (`0.1 + 0.2` is not `0.3`). A `pattern` in `number_close` uses its first group, or
the whole match; `1,866.88` style thousands separators are accepted.

### Rubric checks (open-ended deliverables)

When many texts are right (a summary, a note, a report), a judge model scores the deliverable against a rubric
(ADR 0014):

```yaml
- id: handover_quality
  kind: rubric
  weight: 0.5
  path: reports/handover.md          # leave out to judge the final answer
  rubric:
    threshold: 0.75                  # weighted criterion score at which the check passes
    criteria:                        # weights sum to 1; each is scored 0, 0.5 or 1
    - {id: order_deletion, weight: 0.35, text: Says the request to delete "order 2005" must be "declined", ...}
```

- Pair it with deterministic checks (required: some weight must be deterministic) and guard with deterministic
  guards: a rubric check is never critical.
- **Uncalibrated judges carry weight 0.** Until the judge's current hash has a calibration record on >= 100
  hand-scored items with Cohen's kappa >= 0.7, its verdicts are reported (`grades[].checks[].judge`) but the
  check's weight is 0 and the other checks are scaled to sum to 1. The composite never depends on an
  uncalibrated judge.
- The judge sees only the criteria and the deliverable, framed as data between markers; the agent never sees
  the rubric. Its calls go through `ModelGateway` (quota, timeouts, error classification) on their own tracer:
  their tokens and cost are in `runs.judge_usage`, never in the run's cost or C.
- A judge failure never scores the agent 0. Uncalibrated: the check is `ungraded`. Calibrated: the run is
  INCOMPLETE (ERROR for a judge setup problem), and `hfx regrade` can judge it again later.
- In the reference check (`hfx reference`), a judged rubric check the reference fails fails the reference.

The judge is `catalog/judge/judge.yaml` (offline default: `scripted-judge` / `scripted/judge`, deterministic and
never calibrated); `HF_JUDGE_PROVIDER` / `HF_JUDGE_MODEL` override it.

```bash
hfx judge status                                             # the judge in effect, its hash, trusted or not
hfx judge calibrate --set my-150-items.yaml --model gpt-oss-120b --provider groq   # kappa, MAE; stores a record
```

A calibration set's format is in `grading/calibration.py`; `catalog/judge/calibration/seed.yaml` is a 16-item
format example, not a calibration. Editing the judge's model or prompts (`prompts/judge/`) is a new judge hash
and needs a new calibration.

## Graders: versions, families and re-grading

A grader is registered with `@register_grader` (`grading/base.py`) and declares:

- `id`: the name tasks list in `grader_ids`;
- `version`: stored with every grade it gives (`grades[].version` and the run's `grader_versions` column).
  Changing a grader's behaviour means a new version, and that is a scoring change;
- `family`: what it feeds. `outcome` feeds O and the run's status, `trajectory` feeds Q, `safety` adds a
  violation when its grade fails, and `None` stores the grade without scoring it;
- `combine`: how it combines with the task's other graders of that family. `product` (default) or `min` for
  outcome and trajectory; `count` (default) or `critical` (a failed grade zeroes E and FAILs the run) for safety.

`default_pipeline()` holds every registered grader, so a new one needs no core edit. `state_diff` (outcome) and
`trajectory` (trajectory) are the built-ins. A task with only these two scores exactly as before.

**End states.** After grading, each run keeps what it left behind next to its trace, in
`<trace>.endstate.zip`: the workspace files, the task database, the before-state, the spans the graders saw and
how the run ended. The run's `endstate_path` column points to it.

- `HF_KEEP_ENDSTATE=0` turns this off.
- `HF_ENDSTATE_MAX_MB` (default 50) skips runs whose workspace and database together are bigger.

**Re-grading.** `hfx regrade` runs the current graders, and the task's current checks, over stored end states:

```bash
hfx regrade --run-id 12 --run-id 13   # specific runs (ids from hfx results)
hfx regrade --task sql-03             # the latest run of each RunKey of one task
hfx regrade --all --changed           # everything; list only the runs whose grade changed
```

Each re-grade is a new row in the `regrades` table, with grader versions and a `changed` flag. The run's own row
is never touched, and aggregation still reads only runs. Rubric checks are judged again by the current judge. A re-grade shows what a grader or check change would do
to stored results; new runs are what put that change into the numbers. Runs stored before this change have no
end state and are reported as skipped.

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
| `scripted/retry` | Repeats a step whose tool failed or returned nothing | SUCCESS under `first-call-fails` |
| `scripted/invent` | First call names a tool that doesn't exist (rejected as malformed) | FAIL, SUCCESS with `v2-recover` |
| `scripted/delegate` | Hands the task to its helper when offered `delegate__run`; otherwise replay | SUCCESS, more tokens |

These prove the factory measures differences before any API budget is spent.

## Not in P0

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

## Benchmarks in one file

A whole benchmark fits in one readable YAML file: which tasks, recipes, models (with their provider), seeds and
fault profiles, under which limits, and how to judge the result. `docs/design/benchmark.example.yaml` is the
annotated example; `tests/fixtures/benchmark.scripted.yaml` is the same benchmark on scripted models (offline,
seconds).

```bash
hfx bench docs/design/benchmark.example.yaml --dry-run   # validate, print the plan and projection, stop
hfx bench docs/design/benchmark.example.yaml             # run it
hfx bench docs/design/benchmark.example.yaml --resume    # after an interruption or the 00:00 UTC quota reset
```

- **Same campaign as `smoke`.** The file becomes the plan `hfx smoke` would build (one block per provider, run
  one provider at a time), with the same RunKeys, preflight (`limits.budget_usd`, `limits.deadline_days`, spent
  buckets), concurrency (`limits.concurrency`, `-j` overrides it), faults and `--resume`. Results land in the same
  store, so `hfx results --cells` and `hfx report` see them too.
- **Tasks.** `tasks.splits` (DEV, TEST) plus an optional `tasks.tag`, or `tasks.ids` to name tasks one by one.
  HELD_OUT is refused: held-out tasks are only run by `hfx select evaluate`.
- **Validated before anything runs.** Unknown or misspelt keys, wrong types, unknown task ids, recipes, models,
  providers or fault profiles are listed field by field (exit 2). So are values that are planned but not built:
  any runner other than `native`, and `calibration.enabled: true` (calibration runs on its own).
- **Judge.** `judge.freeze_cost_baseline: true` saves the cost baseline once every planned run is scored (an
  unfinished plan says so and waits for `--resume`). `judge.report: true` writes the report, restricted to the
  runs this file planned, against that baseline. `judge.compare` (`base` + `against`) adds the recipe-pair
  section.
- **The file is stored with its campaign.** `var/bench/<name>-<sha256[:12]>/` holds a byte-for-byte copy
  (`benchmark.yaml`), `manifest.json` (the file's sha256, every RunKey it planned, and each invocation with its
  counts, baseline id and report path) and `report.md`. A stored result traces back to the file through its
  RunKey; an edited file is a different benchmark and gets its own directory.

**Pairing any two recipes.** `hfx report --compare v2-base:v3-staged,v2-base:v4-planner` (or a bench file's
`judge.compare`) adds section 3b to the report: each pair matched on task, model, provider and seed, with mean ΔE
(the second recipe minus the first), wins/losses/ties, a bootstrap 95% interval and a verdict only when the
interval excludes 0. This is how shapes are compared with each other; section 3 (each matrix recipe against the
recipe it extends) is unchanged.

## Analysis

`hfx analyze` answers ANL-01's two shape questions from the results store: which shape wins per model and task
class, and where the best shape changes with step depth. It needs the `analysis` extra (polars, scipy), which
`dev` includes.

```bash
uv run hfx analyze                                   # markdown to stdout, every shape recipe (no `base:`)
uv run hfx analyze --out var/analysis --views        # analysis.md + CSVs, and SQL views for Superset
uv run hfx analyze --recipes v1-base,v2-base --models scripted/replay --baseline <id>
```

- **Same numbers as the cells.** Rows are the same evidence as `hfx report` and `hfx select`, cut to
  current recipe and environment hashes, clean and scored runs only. Each run's E is the E its
  `aggregate_rows` cell averages (E0 with the cell's R, cost-adjusted), and the per-run means are checked
  against the cells. Nothing is stored per run and no hash changes.
- **Shape × model by task class.** One column per arm (a recipe of one shape): mean E, a 95% bootstrap interval
  (`scipy.stats.bootstrap`, percentile) and n. The winner is the arm with the highest mean. It is **clear** only
  when the paired interval of its difference with every other arm lies above 0 and the pairs come from at least
  2 tasks. Pairs are runs of the same task and seed, as in report §3, and the interval matches
  `report.paired_delta` (both are kept in the comparisons CSV). Arms that don't pair are compared unpaired: clear
  only when their intervals don't overlap. Arms with the same mean are a **tie**.
- **Crossover by step depth.** The same arms per depth bucket, on two axes: the class's expected depth
  (SINGLE_STEP, KNOWN_PATH, UNKNOWN_PATH) and the reference plan length (tool calls in `reference.py`). A
  crossover is where the best arm changes between adjacent buckets (a tie is not a change). It is
  **significant** when the winners on both sides are clear.
- **Superset.** `--views` replaces the tables `anl_shape_model_class`, `anl_shape_winners`, `anl_shape_depth`
  and `anl_depth_crossover` in the results DB (SQLite or Postgres) and creates the views `v_shape_model_class`,
  `v_shape_winners`, `v_shape_depth` and `v_depth_crossover` over them. Point Superset datasets at the views.
  Every row carries the `baseline_id` it was scored against. Running it again gives the same result. `runs` and
  `cost_baselines` are never touched.
- **CSV.** `--out DIR` writes `analysis.md` plus `shape_model_class`, `shape_class_winners`,
  `shape_class_comparisons`, `shape_depth`, `shape_depth_winners`, `shape_depth_comparisons`, `depth_crossover`
  and `runs` as CSV.
