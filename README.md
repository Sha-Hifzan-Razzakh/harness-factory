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

hfx tasks                 # 57 tasks and their class keys (13 held out)
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

## API: `hfx serve`

The same authoring rules over HTTP, plus read access to what experiments are made of and what they produced. The
UI uses it; scripts can too.

```bash
uv sync --extra dev --extra api
hfx serve                                  # http://127.0.0.1:8765, OpenAPI docs at /docs
HF_API_TOKEN=... hfx serve --host 0.0.0.0  # beyond localhost a token is required
docker compose --profile api up -d api     # in a container (needs HF_API_TOKEN in .env), host loopback only
```

| Area | Routes |
|---|---|
| Catalog | `GET /api/catalog[/{kind}[/{id}]]`, `POST /api/catalog/{kind}`, `PUT` / `DELETE /api/catalog/{kind}/{id}`; `?dry_run=true`, `?confirm=true`; `GET /api/check`; `POST /api/servers/stub` |
| Experiments | `GET /api/meta` (variants, dimensions, prompt layers), `/api/tasks` (with task class), `/api/models` (and whether each provider's key is set), `/api/faults` |
| Campaigns | `POST /api/campaigns/plan` (projection, nothing runs), `POST /api/campaigns` (202, runs in the background), `GET /api/campaigns[/{id}]` (state and progress), `DELETE /api/campaigns/{id}` (cancel) |
| Results | `GET /api/results`, `/api/results/cells`, `/api/baselines`, `/api/report` (markdown), `/api/runs/{id}/transcript` |

- **A change answers like the CLI.** 201/200 when written or dry-run, **409** when it changes an existing identity
  (the body lists the recipes and servers; repeat with `?confirm=true`), **422** when rejected (the body lists the
  issues; nothing was written). Kinds are `prompt`, `skill`, `recipe`, `server`, `fault`; a prompt's id is its
  ref, slash included (`/api/catalog/prompt/system/terse`).
- **Pasted tool code runs** when its tools are discovered, on the machine running the API. That's why it binds
  to localhost by default and refuses any other address without `HF_API_TOKEN`; with a token, every route but
  `/api/health` needs `Authorization: Bearer <token>`.
- **Campaigns are `hfx smoke` in the background:** the same plan and preflight (a bucket spent today, a plan
  past `deadline_days` or over `budget_usd` is refused with 409), `resume` to skip scored runs, progress from the
  store as runs finish. Every campaign shares the API's one quota ledger, so campaigns started side by side are
  paced together. Each starts from the catalog as it is then, so a recipe written through the API runs in the next
  campaign without a restart. Cancelling tears down the runs' sandboxes and servers; finished runs stay stored.
  The campaign list lives in memory: after a restart it is empty, the runs are not.
- The compose service mounts `catalog/`, `prompts/` and the local tool servers' folder read-write (the `factory`
  services keep them read-only). The image has no ruff, so a module written through it is linted by CI instead.

## UI: `hfx ui`

A Streamlit UI over the API. It has no rules of its own: every check, write and campaign is an API call.

```bash
uv sync --extra dev --extra api --extra ui
hfx ui --serve            # the API and the UI together: http://127.0.0.1:8501
hfx ui                    # just the UI, against a running API (HF_API_URL, default http://127.0.0.1:8765)
```

| Page | What it does |
|---|---|
| Catalog | Every prompt, skill, recipe, tool server and fault profile, with what uses it and its hash. Open one to read its file, edit it or delete it |
| New experiment | A matrix recipe in a few clicks: pick a base, pick the one thing that changes (system prompt, skills, config, tools, delegation, or any field as YAML), check, create |
| Add | A local tool server (tools table, stub or your own module), a remote server, a skill, a prompt, a fault profile, or a whole recipe |
| Run | Recipes × models × tasks × faults × seeds on a provider: plan (projection per quota bucket), start, follow the progress, cancel |
| Results | Cells (E, its 95% interval, pass rate, E₀, C, R), the paired report, recent runs and each run's transcript |

**Check** is a dry run: the same validation and impact, nothing written. A change that alters existing recipe hashes
or tool environments shows which ones and waits for **Confirm and write**. The UI listens on localhost only; with
a token on the API, set `HF_API_TOKEN` for the UI too.

## Catalog history, cards and the workspace: `hfx web`

Every change made through `hfx new`, the API or a UI is recorded as a new version of the item: who, when, why
(`--why`), and its files before and after (ADR 0022). The history lives in `var/catalog_history/`; files stay the
source of truth and nothing is committed.

```bash
hfx catalog history skill sql-analysis      # versions, authors, notes
hfx catalog diff skill sql-analysis 1 3     # what changed between two versions
hfx catalog restore skill sql-analysis 1    # bring a version back (checked like any change)
hfx catalog activity                        # every recorded change, newest first
hfx catalog sync                            # record changes made outside authoring (an editor, git pull)
hfx catalog archived                        # deleted items: their history stays, restore brings them back
hfx catalog results recipe v2-base          # scored runs per version of an item
hfx new model groq/gpt-oss-20b --id openai/gpt-oss-20b --free --context 131072   # a model entry in providers.yaml (comments are kept)
hfx card harness v2-base                    # spec cards: model, harness, task, tool, fault
hfx card spec --task sql-01 --recipe v2-base --model scripted/replay --provider scripted --run   # the final card of one experiment
hfx web                                     # the React workspace at http://127.0.0.1:8765/app/
```

- **`hfx web`** builds `web/` with npm the first time (or with `--build` after pulling `web/` changes) and then
  needs no Node: the API serves the app. It records outside changes first (`--no-sync` to skip).
- **Deleting archives:** the item leaves the catalog and can be restored; `hfx catalog purge` drops its history.
- **Tasks** are versioned and shown, but still written by hand.
- **Experiments** (workspace and API) are saved with the exact runs they planned, so they survive restarts.
- The Streamlit UI gains guided pages (Playbook, What we can test with, Cards, Build) before the existing ones.
- `.devcontainer/` opens the repo in a dev container (see its README).

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
    recipe; then the same task on the same model with any recipe of the same shape (how heavy the task is there);
    then the recipe on other models, its tokens scaled by this model's typical run size over theirs (unscaled
    when this model has no history); then the recipe's own caps (`max_steps`, `max_tokens`), shown as
    `ceiling (recipe caps)`: an upper bound, so a budget or deadline is checked against the worst case rather
    than a guess. The default per shape is used only with no history at all of that provider kind. Cross-model
    estimates are priced at the planned model's own rates. Scripted runs never stand in for real models. The
    "estimated from" column says which level was used.
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
- **Substitutes and reduced tool sets.** A tool granted by one task tool alone, with the same side effect, stands
  in for it (a server that re-describes another's tools, such as an add-on's guided variant): a recipe that calls
  the substitute reaches the task. `hfx smoke` and `hfx bench` print every pair they leave out and the tools its
  recipe misses; the bench report lists them under *Reach*. To compare a reduced tool set on purpose (a no-engine
  baseline, an engine-only variant), name the recipe in the bench file's `plan_unreachable`; the selector still
  prunes it.
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

## Outbox and calendar

Two more stateful mock servers work like `biz`: one JSON document each, seeded per run from
`env_seed.server_state` (state name -> seed file) into the run's state folder next to the database (`HF_OUTBOX`,
`HF_CALENDAR`; `/state/outbox.json` and `/state/calendar.json` in a Docker sandbox), never written back to the
seed and kept in the run's end-state bundle. `env/state.JSON_STATES` lists every such state; biz stays seeded by
`biz_state`. The seeds and the tasks `cal-01`, `cal-02` and `out-01` are generated by `scripts/taskgen/comms.py`,
which computes every expected value by running the reference plan with the servers' own tool functions.

| Tool | Side effect | Notes |
|---|---|---|
| `outbox.send_email(to, subject, body, cc, attachments)` | IRREVERSIBLE R3 | to/cc stored lower-cased, de-duplicated, sorted; one delivery record per recipient; attachments are workspace files (path, size, sha256) |
| `outbox.notify(channel, text)` | IRREVERSIBLE R2 | only the seed's channels |
| `outbox.list_sent`, `list_templates`, `get_template` | READ | templates have a status: current or retired |
| `calendar.list_events`, `get_event`, `find_free_slots` | READ | shown in the business timezone, with each attendee's local time |
| `calendar.create_event`, `move_event`, `cancel_event` | REVERSIBLE R1 | a cancelled event is restored by moving it |

Refusals are `ToolError`s with the reason: a recipient domain the seed doesn't approve (`meta.allowed_domains`),
a missing subject, an attachment that isn't a workspace file, an unknown channel or template; a time without a UTC
offset, in the past or past the horizon, an attendee outside their working hours (in their own timezone) or
already busy, an unknown attendee or calendar. Time is explicit: the seed fixes "now" and the business timezone
(Asia/Dubai), events are stored in UTC, and timezones (with their daylight-saving changes) come from the seed's
`zones` table rather than the host's tz database, which Windows and slim images lack.

Checks read any JSON state with a `"<state>:<JSON pointer>"` path, compared like `biz_equals` / `biz_count`
(strict on type; a count's `query` names field values a record must have):

```yaml
- {id: moved, kind: state_count, weight: 0.4, path: "calendar:/events", text: "1",
   query: '{"id": "EV-0107", "start": "2026-11-03T13:00:00Z", "status": "confirmed"}'}
- {id: A-212_not_emailed, kind: state_count, weight: 0, critical: true, path: "outbox:/deliveries", text: "0",
   query: '{"address": "billing@falconsys.ae"}'}
```

`state_equals` / `state_count` also read `biz:` paths. The state diff lists records per state
(`+outbox sent/M-0002; ~calendar events/EV-0107`).

**Sending and the IRREVERSIBLE policy.** `v2-base` denies IRREVERSIBLE calls, and until the approval gate (#75)
ASK is a denial too, so a recipe that opened the outbox under that policy would refuse every send and score 0 on
every outbox task (`tests/test_outbox_calendar.py` runs exactly that). Two matrix recipes split it:
`v2-calendar` adds only the calendar (REVERSIBLE writes, base policy unchanged), and `v2-outbox` is the "may send"
recipe: calendar and outbox, `IRREVERSIBLE: AUTO`, tier T3 for `send_email`'s R3, and a tool slice narrowed to
`fs.list_dir`, `fs.read_file`, `calendar.*` and `outbox.*`, so the sends are the only IRREVERSIBLE tools it can
reach. A wrong send is then caught by the task, not the harness: a critical guard on the outbox state (no delivery
to the account on legal hold) fails the run. With the approval gate, an ASK variant of `v2-outbox` will let the
same tasks compare catching a wrong send after the fact with stopping it before it leaves.

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

### Conformance and health (MCP-11, ENV-06)

`hfx conformance` checks every server in `tools.yaml` against the MCP protocol and its own declaration, in a fresh
sandbox of the configured kind (`--sandbox local|docker`, default `HF_SANDBOX`); CI runs it in both. Rules, in order:
`fixture`, `initialize` (known protocol version, tools capability, the server names itself by its id, a remote one
reports its pinned version), `list_tools` (the drift check provisioning uses: declared tools, frozen schema and
description hashes), `tool_names` (SEP-986), `input_schema` (valid JSON Schema), `call` (a happy-path call per tool,
well-formed, matching its output schema), `unknown_tool`, `invalid_args` (missing or wrong-typed arguments, generated
from each schema, are MCP errors naming the argument), `refusal` (documented refusals carry the server's own message:
a `ToolError`, not a masked `ValueError`), `usable_after_error` and `shutdown` (the server exits when its stdin
closes). `tests/test_mcp_conformance.py` runs the same suite for every declared server, so a server added later is
covered by itself.

Every declared server needs `catalog/conformance/<server>.yaml`; without one the suite fails, and a fixture for an
undeclared server fails too (also for a server made with `hfx new`):

```yaml
server: fsx
seed: {files: seed/files}            # env_seed, relative to this file (catalog/conformance/seed, or ../seeds/...)
                                     # (a `corpus:` names a catalog/corpora corpus, found as a task finds it)
edits: {notes/hello.txt: "changed\n"}  # optional: files written after the seed (uncommitted when `git: true`)
skills: []                           # optional: catalog skills installed for the run
calls:                               # every declared tool at least once, in order
- {tool: edit, args: {path: notes/hello.txt, old: hello, new: hi}, expect: {contains: [...], keys: [...]}}
refusals:                            # at least one refusal the server documents, and what its message says
- {tool: edit, args: {path: calc.py, old: "", new: x}, contains: [old must not be empty]}
invalid: []                          # optional: invalid-argument cases beyond the generated ones
```

A remote server is checked live with `HF_ALLOW_REMOTE=1` (its fixture may set `tenant_state`), otherwise reported
as skipped.

**Health before every run.** After a server starts and before the run's clock, the pool probes it: start,
initialize, `list_tools` and a ping, all within `HF_MCP_HEALTH_TIMEOUT_S` (default 60 s). A server that hangs, dies
or errors makes provisioning fail (`PROVISION`: the run is an ERROR, never scored or stored), and what was started is
closed. A healthy run records each server's probe in `snapshot.mcp_health` (`status`, `probe`, `ready_s`, `ping_ms`).

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

## Prompt smoke checks (promptfoo)

Quick prompt checks outside campaigns, with [promptfoo](https://www.promptfoo.dev/) (SKP-03). The test generator
`promptfoo/harness_prompts.py` renders, with the real `PromptAssembler`, what the harness sends at each point of a
run, for every recipe and three tasks (fs-01, sql-01, mix-03): the system and task messages, each V3 stage prompt,
and the V4/V5 planner, step, worker and replan prompts. promptfoo sends them to its `echo` provider, so the run is
offline and needs no keys. Needs Node 22.22 or later.

```bash
uv sync --extra dev
uv run npx --yes promptfoo@0.124.1 eval -c promptfoo/promptfooconfig.yaml
uv run npx --yes promptfoo@0.124.1 eval -c promptfoo/canaries.yaml
```

Run it under `uv run`, so the test generator runs on the project's Python. The generator also works out, from the
recipe, the task and the tool catalog, what each prompt must say; `promptfoo/checks.js` compares. Each case checks
that:

- promptfoo sent exactly what the harness renders (promptfoo's own templating is off);
- nothing is left unrendered (`{{`, `{%`, `{#`) and no message is empty;
- the tool lines (`Available tools:`, `Tools in this stage:`, `Tools:`, the planner's tool list) name exactly the
  tools the model is offered there, a stage or step prompt names only that part's tools, and a read-only stage
  offers only READ tools;
- the markers the shapes parse are in the prompt that asks for them: `RETRY:` (V3 verify), `Step i of n:`,
  `BLOCKED:` and the last-step note (V4), `Your part:` and `BLOCKED:` (V5), the plan length cap;
- the task instruction reaches the model unchanged;
- each message and the whole prompt fit the character budgets in `promptfoo/promptfooconfig.yaml`.

`canaries.yaml` renders five deliberately broken templates (in memory; `prompts/` is never written), and each must
fail the check it targets, so a check that stops working fails the eval. CI runs both in the `prompts` job.

The checks read the prompt, not the reply, so the same config runs against a real model. Put the provider's key in
the environment (never in a file under version control), then:

```bash
uv run npx --yes promptfoo@0.124.1 eval -c promptfoo/promptfooconfig.yaml -r openai:chat:gpt-4.1-mini -j 2
uv run npx --yes promptfoo@0.124.1 view      # the model's replies next to each prompt
```

The replies are there to read: their quality is measured by campaigns, not by promptfoo. Set
`PROMPTFOO_DISABLE_TELEMETRY=1` to keep promptfoo from sending usage data.

## Observability and MLOps stack

```bash
docker compose up -d     # postgres, mlflow, phoenix, otel-collector, langfuse-* (see Langfuse below)
```

Then set the following in `.env`:

| Variable | Value | Effect |
|---|---|---|
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://localhost:4318` | Spans go to the collector, which fans out to Phoenix (`:6006`) and Langfuse |
| `MLFLOW_TRACKING_URI` | `http://localhost:5000` | Each run is logged as an MLflow run (params, metrics, trace artifact) |
| `HF_RESULTS_URL` | `postgresql+psycopg://hf:hf@localhost:5432/hf` | Results go to Postgres (`uv pip install -e ".[postgres,mlops]"`) |

Langfuse runs in the same compose file and needs a key pair in `.env` first: see [Langfuse](#langfuse).

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

**What Phoenix shows.** The root span is named `<recipe> · <task>` (`hf.run_key` stays) and carries the task
prompt (`input.value`), the final reply or `(no final message: ended by <stop>)` (`output.value`), and `metadata`:
task, recipe and recipe hash, model, provider, seed, status, outcome, how the run ended, model-call and tool-call
counts, the system prompt. A campaign's runs share a `session.id`. Model calls render as a chat
(`llm.input_messages` / `llm.output_messages` with the tool calls asked for, `llm.tools`, `llm.invocation_parameters`)
and tool calls show their arguments and result as JSON panes (`input.value` / `output.value`). The MCP SDK's
`MCP send ...` spans are dropped from the export unless asked for. All of it is observability only: it goes to
OpenTelemetry alone, never into the JSONL trace, the results store, grading or any hash.

| Variable | Default | Effect |
|---|---|---|
| `HF_TRACE_MESSAGES` | `delta` | `delta`: a conversation's messages go with its first call, later calls send only the messages added since (`hf.input_messages_offset` says how many came before); `full`: every message on every call; `off`: token counts only |
| `HF_TRACE_MAX_CHARS` | `8000` | Longest message content, tool argument or tool result copied to a span |
| `HF_TRACE_MCP_SPANS` | unset | `1` keeps the MCP SDK's own spans under each tool call |
| `HF_MLFLOW_EXPERIMENT_ID` | `1` | The MLflow experiment the collector sends traces to (below) |

**Traces in MLflow.** The collector also forwards every trace to MLflow (`otlphttp/mlflow`: MLflow 3.6 or later
takes OTLP over HTTP at `/v1/traces`, into the experiment the `x-mlflow-experiment-id` header names; compose's
SQLite store is the SQL store it needs; compose pins `MLFLOW_TAG` to `v3.18.0`, the version this was checked
against). MLflow's Overview and Traces tabs then show one trace per run, with the
same span types, inputs and outputs, models and token counts. The header's id is the number in MLflow's
`/#/experiments/<id>` URL for `harness-factory`: `1` on a fresh MLflow, where it is the first experiment created;
set `HF_MLFLOW_EXPERIMENT_ID` if yours differs. Point `OTEL_EXPORTER_OTLP_ENDPOINT` at the collector (`:4318`), not
straight at Phoenix, or MLflow sees nothing. Each MLflow run carries the trace's id as the `otel_trace_id` tag;
MLflow names that trace `tr-<otel_trace_id>`. A campaign's `session.id` becomes MLflow's trace session.

**Loading stored runs.** `hfx traces replay [files or directories]` (default `var/traces`) sends stored JSONL runs to
`OTEL_EXPORTER_OTLP_ENDPOINT` as new traces with their original start and end times, so runs made before a backend
was connected appear in Phoenix, Langfuse and MLflow without running anything again. Replayed spans say
`hf.replayed: true`, and the root carries the JSONL trace id (`hf.trace_id`). What the JSONL never held can't come
back: model calls show their tokens and previews, not the chat.

**Turns.** Each model turn is a `turn <n>` span (CHAIN) holding its model calls (a recovered reply and a compaction
summary included), and the tool batch it asked for is `turn <n> · tools`, holding those tool calls (concurrent
ones too). Shapes run the two in separate graph nodes and a span never stays open across a node boundary (a
checkpointed run may pause there), so they are two siblings with the same number. A V4 plan is a turn of its own,
and a V5 worker or a delegate numbers its own turns from 1 under its delegate span. Turn spans are in the JSONL
trace too (`kind: turn`); no count, budget, cost or score reads them.

```
v2-base · fs-02            (AGENT)
  turn 1                   (CHAIN)
    model_call:<model>     (LLM)
  turn 1 · tools           (CHAIN)
    tool_call:fs.read_file (TOOL)
  turn 2                   (CHAIN) ...
  grade:pipeline           (EVALUATOR)
```

## Changing the results schema

The results store schema is versioned with Alembic. The revisions are in
`src/harness_factory/results/migrations/versions/`. `ResultsStore()` upgrades the store to the head revision
when it opens it, on SQLite and on Postgres, so no setup step is needed. A store already at head is not
migrated again.

- **`0001` is the baseline.** It is the schema main had before Alembic (`runs`, `cost_baselines`). A store
  created before Alembic has no `alembic_version` table. The baseline adopts it: it creates a missing table and
  adds a missing nullable column, as the old in-code migration did. Then it records `0001`. Never edit `0001`
  or any other merged revision.
- **`0002` adds what main gained before Alembic landed:** the `runs` columns `grader_versions`, `endstate_path`,
  `judge_usage`, `tools_hash` and `served_id`, and the tables `equivalences`, `regrades` and
  `judge_calibrations`. Stores from that code may already have them, so `0002` adopts them the way `0001` does.
- **`0003` adds `transfers`** (runner transfer checks, ADR 0016), adopted the same way.
- **A store written by newer code is refused.** If its revision is unknown to this checkout, the store is not
  opened, and the error says so. Use another `HF_RESULTS_URL` per branch if your branches change the schema.

To add a column or a table:

1. Change the table in `results/store.py` (`metadata`).
2. Add a revision whose `down_revision` is the current head. Use the next number as its id:

   ```bash
   HF_RESULTS_URL=sqlite:///var/schema.db uv run alembic -c src/harness_factory/results/migrations/alembic.ini upgrade head
   HF_RESULTS_URL=sqlite:///var/schema.db uv run alembic -c src/harness_factory/results/migrations/alembic.ini \
       revision --autogenerate --rev-id 0004 -m "add runs.<column>"
   ```

   Autogenerate compares the database with `metadata`. Read what it wrote and keep only your change. For
   SQLite, the environment renders ALTERs in batch mode. Write a `downgrade()` too.
3. Run `uv run pytest -q tests/test_results_store.py`. Its tests fail when `metadata` and the head revision
   differ, when there are two heads, or when a revision does not downgrade.

A new column is nullable, or it has a `server_default`, because existing rows need a value. A rename or a
type change is a revision of its own. It is never done by editing `metadata` alone.

**Two PRs, one parent.** If two open PRs each add `0002` on top of `0001`, the second one to merge gets two
heads, and `test_migrations_have_one_head` fails. Rebase that revision: give it the next id and set its
`down_revision` to the revision that merged first.

**Postgres.** The store tests run on SQLite and Postgres. In CI, Postgres is a testcontainers container
(`HF_REQUIRE_POSTGRES=1`, so these tests fail rather than skip there). Locally they use Docker if Docker is
available. To use a server you already run, such as the compose `postgres` service, set
`HF_TEST_POSTGRES_URL=postgresql+psycopg://hf:hf@localhost:5432/hf`. Each test creates its own database there
and drops it afterwards. With neither Docker nor that variable, the Postgres variants are skipped.

## Langfuse

Self-hosted Langfuse v4 is the collector's second trace target, next to Phoenix. `compose.yaml` follows Langfuse's
own docker-compose: `langfuse-web` (UI and OTLP ingestion) and `langfuse-worker`, with their own Postgres,
ClickHouse, Redis and MinIO (`langfuse-postgres`, `-clickhouse`, `-redis`, `-minio`; separate from the results
`postgres`). Only the UI has a host port (`HF_LANGFUSE_PORT`, default 3000). Langfuse recommends 4 cores and 16 GiB
for a server; locally, give Docker a few GiB more than the rest of the stack needs.

The collector's `otlphttp/langfuse` exporter sends to `LANGFUSE_OTLP_ENDPOINT` (default
`http://langfuse-web:3000/api/public/otel`, plus `/v1/traces`; Langfuse takes OTLP over HTTP only, not gRPC). It
authenticates with HTTP Basic auth built from `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` (the `basicauth`
extension) and sends `x-langfuse-ingestion-version: 4`, so spans appear at once. At startup `langfuse-web`
creates the `harness-factory` organization and project if missing and, when both keys are set, registers that key
pair for the project (headless initialization; the first user too, when its email and password are set). For
Langfuse Cloud instead, set `LANGFUSE_OTLP_ENDPOINT` to `https://cloud.langfuse.com/api/public/otel` (or your
region) and use that project's keys.

1. In `.env`, set the key pair and a first user, and replace the local-only secret defaults if anyone else can
   reach port 3000 (`.env.example` lists them):

   ```bash
   LANGFUSE_PUBLIC_KEY=pk-lf-$(openssl rand -hex 16)      # write the values, not the commands
   LANGFUSE_SECRET_KEY=sk-lf-$(openssl rand -hex 16)
   LANGFUSE_INIT_USER_EMAIL=you@example.com
   LANGFUSE_INIT_USER_PASSWORD=...
   ```

2. Pin MinIO. Its vendor publishes the image as `latest` only, so after the first pull write its digest to `.env`:
   `LANGFUSE_MINIO_IMAGE=$(docker image inspect --format '{{index .RepoDigests 0}}' cgr.dev/chainguard/minio:latest)`.
   The other Langfuse images have pinned defaults (`LANGFUSE_TAG=4.60.0`, Postgres 17.11, ClickHouse 25.12.12.1,
   Redis 7.4.11).

3. Check that one scripted run reaches both Phoenix and Langfuse:

   ```bash
   set -a; . ./.env; set +a                               # the keys, for the curl below
   docker compose up -d
   docker compose ps                                       # langfuse-web up; its first start runs migrations
   curl -fsS http://localhost:3000/api/public/health        # 200 once migrations are done
   curl -fsS -u "$LANGFUSE_PUBLIC_KEY:$LANGFUSE_SECRET_KEY" http://localhost:3000/api/public/projects   # keys valid
   OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318 hfx smoke --recipe v2-base --models scripted/replay --strict
   docker compose logs otel-collector | grep -i error      # nothing: both exporters were accepted
   ```

   Then open Phoenix (http://localhost:6006, project `default`) and Langfuse (http://localhost:3000, project
   `harness-factory`, Tracing). Both show one `run:<run-key-short>` trace per run with the same `model_call`,
   `tool_call` and `grade` children. A 401 in the collector log means the keys in `.env` aren't the project's.
   `langfuse-web` adds a key pair at startup only when its public key is new: after changing the secret alone,
   pick a new public key too, then `docker compose up -d` (it recreates `langfuse-web` and `otel-collector`).

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
  seeds/comms/            outbox and calendar states (templates, sent mail, people, zones, events) + policies
  tasks/<id>/             task.yaml (instruction, allowance, oracle) + reference.py (PLAN, FINAL)
prompts/                  Jinja2 prompt layers (system/, task/)
promptfoo/                prompt smoke checks: promptfoo configs, test generator (Python), checks (JS)
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
  grading/ results/       graders, EffectivenessRecord, ResultsStore + migrations   (B8)
  mlops/                  MLflow tracker                                            (B9)
  mcp/                    MCPClient, MCPSessionPool, ToolCatalog                    (B11)
  observability/          Span, Tracer (JSONL + OTLP)
  authoring/              AuthoringService: staged, validated catalog writes (ADR 0009)
  api/                    hfx serve: authoring and results over HTTP (extra `api`)
  ui/                     hfx ui: Streamlit pages over the API (extra `ui`)
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

Offline comparison (`scripted/replay`, the 25 DEV + TEST tasks, cost baseline over these five recipes; tokens are
the total over all 25 runs):

| Recipe | Success | E₀ | C | E | Tokens |
|---|---|---|---|---|---|
| `v1-base` | 1/25 (fs-01 only) | 0.090 | 0.052 | 0.085 | ~5,700 |
| `v2-base` | 25/25 | 1.000 | 0.263 | 0.954 | ~41,000 |
| `v3-staged` | 25/25 | 1.000 | 0.611 | 0.889 | ~86,000 |
| `v4-planner` | 25/25 | 1.000 | 0.720 | 0.876 | ~120,000 |
| `v5-orchestrator` | 25/25 | 1.000 | 0.445 | 0.920 | ~64,000 |

V2 to V5 tie on E₀; efficiency C separates them (see *Cost and efficiency*). V5 costs less than V4 with the same
plan because each worker starts from a short conversation, where V4's executor carries every step's history.
Offline, the scripted model never loses track, so planning can only cost; whether it pays with real models on longer
tasks is a Phase 2 question.

**Replies cut off at the output limit.** Every `model_call` span records the provider's `finish_reason`. A reply
that stops at the output limit (`length`, or `max_tokens`) inside a tool call, so its arguments are not whole
JSON, is never run and never sent back to the provider: it is a malformed reply, like one the provider refuses.
It costs a step and its tokens, counts as an invalid call in Q, and ends the run as `malformed_output` (graded on
the environment, PARTIAL at most), or, with `config.recover_malformed`, the model is told it was cut off and asked
again. Before this, the broken arguments were replayed, the provider refused the next request as a setup error,
and the run was ERROR and left out of the cell's mean.

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
| tools | `v2-calendar` | Adds the `calendar` server (REVERSIBLE writes; see [Outbox and calendar](#outbox-and-calendar)). The only recipe besides `v2-outbox` that reaches `cal-02` |
| sending | `v2-outbox` | Calendar and outbox servers with `IRREVERSIBLE: AUTO` and tier T3, on a slice whose only IRREVERSIBLE tools are the two sends (no `fs.write_file`, `fs.delete_file` or `sql`). The only recipe that reaches `cal-01` and `out-01` |
| tools | `v2-docs` | Adds the `docs` server: read and edit `.docx` (`docx_read`, `docx_replace`, `docx_append_paragraph`) and `.xlsx` (`xlsx_sheets`, `xlsx_read`, `xlsx_write`), read `.pdf` (`pdf_text`, `pdf_info`). The office tasks name these tools, so plans run them only on recipes that open the server |
| retrieval | `v2-retrieval-bm25` | A `retrieve__search` tool over the task's corpus, BM25, 5 passages per call (`retrieval: {mode: bm25, top_k: 5}`); see [Retrieval](#retrieval) |
| retrieval | `v2-retrieval-hybrid` | The same tool with hybrid search (dense + BM25, reciprocal rank fusion) and a reranker (`retrieval: {mode: hybrid, top_k: 5, rerank: true}`) |
| tools | `v2-web` | Adds the `web` server: `web.search` and `web.fetch` over the task's recorded web corpus (see [Web search over recorded corpora](#web-search-over-recorded-corpora)). The only recipe that reaches `web-01` and `web-02` |

**Skills** live in `catalog/skills/<id>/SKILL.md` (a `name` and `description` header, then the body). Five
general how-tos so far: `sql-analysis`, `sql-changes`, `editing-files`, `file-exports`,
`requests-and-policies`. A skill's full text is part of the recipe hash. On demand, the provisioner installs
the recipe's skills in the run's state folder, outside the workspace: the model reads them only through
`skills.load` and can't change them. `skills.load` is granted to every task (`granted_by: []`) because it reads
harness material, nothing from the task's world.

> **Caveat:** these skills were written by the same author as task batches 1 and 2, held-out ones included, so
> skill gains on those tasks are an upper bound. Batch 3 (`b3-*`, tag `batch-3`) was written by an author who never
> saw the skills, recipes, prompts or shapes; measure skills on it for the honest estimate, in a store of its own:
> `HF_VAR=var/batch3 hfx smoke --tag batch-3 --recipe v2-base,v2-skills-upfront,v2-skills-on-demand --models <m>`.

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

## Web search over recorded corpora

The `web` server (ENV-01, KNW-01, ADR 0018) is the web as the model sees it: a search engine and a browser over a
recorded snapshot of a few sites. No call ever reaches the network. Unlike [Retrieval](#retrieval), which searches
the task's own files from the harness side, these are tools the model calls by name, on a server in the sandbox.

| Tool | Returns |
|---|---|
| `web.search(query, page=1)` | `{query, page, pages, total, results, next_page}`: 5 results per page (at most 5 pages), each with `url`, `title`, `snippet`, `fetched` and, when the page states one, `published`. BM25 (standard library, `env/mcp/impl/webindex.py`); ties go to the URL in order, so the same query on the same corpus version always returns the same page |
| `web.fetch(url, offset=0)` | `{url, status, title, fetched, published?, redirected_from?, offset, length, text, next_offset}`: at most 6,000 characters per call, cut at a line break; read on with `offset=next_offset`. Redirects are followed and reported; a recorded 404 or 410, or a path the site never had, is an HTTP error; a host that isn't in the snapshot can't be reached |

**Corpora** are folders in `catalog/corpora/<id>/`: `corpus.yaml` (id, title, redirects, recorded 404s/410s, and
the frozen `version` and `page_count`) and `pages/**/*.md`, one page per file with a YAML header (`url`, `title`,
`fetched`, optional `published`). A corpus's version is its content address: the hash of every URL, title, date,
text, redirect and 404 it serves, not of file names or labels, with line endings normalised. `hfx corpus freeze`
writes it; a corpus whose files no longer match is refused at provisioning, and `hfx validate` says so.

```bash
uv run hfx corpus list            # id, version, pages, redirects, 404s, index id, frozen or stale
uv run hfx corpus freeze          # after editing a corpus: a new version, so a new hash for every task using it
```

**A task names its corpus** with `env_seed.corpus: <id>` and lists the web tools directly in `allowed_tools`, so
only recipes that open `web` (`v2-web`) reach it. The corpus version is part of the task hash (tasks without one
keep theirs). The factory compiles each version once, with its index, into `var/corpora/<id>/<index id>/` and
copies it read-only into the run's state folder (`HF_WEB`; in Docker its own `:ro` mount at `/state/web`), never
the workspace. The run's snapshot records `web_corpus`: id, version, index id, engine, and the page, redirect and
404 counts. The ranking engine's id is part of `env_hash` for recipes that open `web`.

**Tasks.** `web-01` (DEV): Relaymail's current per-key rate limit, where the retired v1 page ranks first and the
launch post has an older figure. `web-02` (TEST): the first passenger sailing of a ferry route whose operator moved
the date twice, with a newspaper article newer than the release it repeats. Both answer with the value and the
URL; `grounded` needs a fetch of the page the answer is on (`touches: [url=...]`), so an answer from search
snippets keeps only the answer's weight.

Corpora too large for git go to DVC (ADR 0018): `pages/` becomes a DVC-tracked folder and nothing else changes,
because the version is computed from the pages themselves. CI and the offline gates use only the corpora in the
repository.

```bash
uv run hfx smoke --recipe v2-web --models scripted/replay --strict
```

## Catalog add-ons

An add-on is a catalog folder that lives outside this repository (ADR 0021): a private catalog, or one versioned
with another project. Name one or more in `HF_ADDONS` (separated by `:` on Linux and macOS, `;` on Windows), in the
environment or `.env`. With it unset the factory sees only its own catalog.

```
<add-on>/addon.yaml               id, python_path, libraries, servers
<add-on>/catalog/tools.yaml       more MCP servers (frozen with `hfx tools freeze`, into this file)
<add-on>/catalog/tasks/<id>/      more tasks
<add-on>/catalog/recipes/**.yaml  more recipes (`base:` may name a built-in recipe)
<add-on>/catalog/conformance/     one fixture per add-on server (`hfx conformance --server <id>`)
<add-on>/catalog/skills/<id>/     more skills (SKILL.md), by id as in catalog/skills/
<add-on>/prompts/**.j2            more prompt templates, by ref as in prompts/ (e.g. stage/my_stage)
```

```yaml
# addon.yaml
id: my-addon
python_path: [src]                  # where its server modules import from
libraries:                          # what its servers may import beyond mcp and the standard library
- {dist: some-lib, version: 1.2.3, imports: some_lib}
- {dist: private-lib, version: 0.1.0, imports: private_lib, source: "git+https://...@<commit>"}
servers:                            # server module -> its libraries
  my_addon.server: [some-lib, private-lib]
```

- **An add-on only adds.** A server, task or recipe id, a prompt ref, a server module or a library pin that
  clashes with the built-in catalog or another add-on is refused. Server modules can't live in `harness_factory`.
- **Prompts.** Add-on templates are found after the built-in ones, by the same refs. A recipe's hash holds the
  source of every template it uses, wherever it lives, so editing an add-on prompt is a new recipe hash.
- **Skills.** Add-on skills join the library after the built-in ones. A recipe names them by id like any skill,
  for both loadings (`upfront`, `on_demand`). An id the built-in skills or another add-on already have is refused.
  A recipe's hash holds the text of every skill it uses, so editing an add-on skill is a new recipe hash.
- **Libraries** follow ADR 0013: each is pinned to one version, joins `env_hash` for the servers that use it, and
  is checked before a run. A library that isn't on PyPI is pinned by a git `source` at one commit; the installed
  commit is checked too. Install them yourself as the add-on describes; the `docs` extra and the image hold only
  the built-in servers' libraries.
- **Sandboxes.** Add-on servers run in the local sandbox, with their `python_path` on `PYTHONPATH`. The Docker
  sandbox refuses them, since its image carries only the built-in servers.
- **Reference data.** A task can mount one read-only file for its servers with `env_seed.reference: <file>`. Each
  reset copies it into the state folder (never the workspace) as `reference/reference.json`; `HF_REFERENCE` names
  it, and Docker mounts the folder `:ro`. The file is part of the task hash; tasks without one keep theirs.
- **Identity** is the same as for built-in content: tool specs, pins and task contents go into the same hashes,
  wherever the files live. Campaign results don't say which add-on a task came from, only its id and hashes.

## Runners

A runner is what executes a recipe. Until now that was always the factory's own graphs (`native`). A recipe can name
a framework's own ready-made agent instead, so the factory measures what a team would deploy (ADR 0016):

```yaml
# catalog/recipes/matrix/v2_langgraph_prebuilt.yaml
id: v2-langgraph-prebuilt
base: v2-base
runtime: langgraph-prebuilt      # or llamaindex-agent; unset = native (left out of the hash)
```

| Runtime | Agent | MCP tools via | Extra |
|---|---|---|---|
| `native` | the factory's shapes (`shapes/`) | the run's MCP session pool | (none) |
| `langgraph-prebuilt` | `langchain.agents.create_agent` (LangGraph graph with `ToolNode`; successor of `create_react_agent`) | `langchain.mcp` (successor of langchain-mcp-adapters, which needs `mcp<2`) | `langgraph-prebuilt` |
| `llamaindex-agent` | `FunctionAgent` | `McpToolSpec` (llama-index-tools-mcp) | `llamaindex` |
| `autogen-agentchat` | `AssistantAgent` in a single-agent `RoundRobinGroupChat` (`TextMessageTermination`) | autogen-core `BaseTool`s over the executor (autogen-ext's MCP tools don't import under MCP 2.x) | `autogen-agentchat` |

- **Only two things are wrapped.**
  - **Model client:** a LangChain chat model / LlamaIndex LLM whose calls go through `model_turn` and
    `ModelGateway` (budget, quota, tokens, cost, error classification).
  - **Tool calls:** the framework's MCP adapter builds its tools from a session whose `call_tool` goes to
    `ToolExecutor` (allowance, permissions, faults, traces).

  The loop, prompts, tool formatting, parsing, retries and history are the framework's, at its defaults. Neither
  framework adds prompt text: the recipe's system message is the agent's system prompt and its task message the
  input. They do differ in what tools and results look like. LlamaIndex, for example, shows a tool result as the
  `CallToolResult` object's repr.
- **Tool loop only.** A runtime other than `native` is refused for V1/V3/V4 recipes, `delegation` and
  `recover_malformed`: those are the factory's control flow, not the framework's.
- **Identity.** A runner recipe's hash includes the runtime and the exact versions of its framework packages
  (`runners/identity.py`, locked in `uv.lock`). A framework upgrade starts new cells; native hashes never change.
- **Run endings** are native ones:
  - final answer: graded;
  - a budget limit, or the framework's own recursion or iteration limit (LlamaIndex: 20): a harness stop, outcome
    × 0.5;
  - provider infrastructure: INCOMPLETE.
- **Transfer check:** does the score carry over? The same recipe, tasks, seeds, model and provider on native and on
  the runner; the paired E0 difference is judged like a provider calibration and frozen:

```bash
hfx transfer run v2-llamaindex-agent --model scripted/replay --provider scripted --run --min-pairs 20
hfx transfer list
```

Offline (`scripted/replay`, the 20 visible tasks), both runners pass 20/20 with the same model and tool calls as
`v2-base`, and both transfer checks are `equivalent` (20 pairs, every one a tie). Tokens against `v2-base`:
LangGraph ×1.02, LlamaIndex ×1.22, mostly from its repr tool results. Real models are where runners can differ.

**Migration check (RUN-11).** For teams on AutoGen (in maintenance mode since October 2025) moving to its successor,
Microsoft Agent Framework: the same recipe on both runners, paired and judged with the same rule. `--baseline` names
the runner recipe to compare against instead of native:

```bash
hfx transfer run v2-autogen-agentchat --baseline v2-ms-agent-framework --model scripted/replay --provider scripted --run --min-pairs 20
```

Offline it comes out `equivalent` (20 ties). With real models the clearest difference is tool errors: AutoGen shows
the model the server's message, and Agent Framework shows "Error: Function failed.". Semantic Kernel has no runner:
every release pins `pydantic<2.14`, and the latest ones also pin `mcp<2`, so it can't be installed beside the factory.
The AutoGen extra moves protobuf to 5.29 (autogen-core pins `~=5.29.3`). Details are in ADR 0016's #55 addendum.

### More runners (#56)

| Runtime | Agent | MCP tools via | Model client | Extra |
|---|---|---|---|---|
| `langgraph-supervisor` | `langgraph_supervisor.create_supervisor` over one `create_agent` worker | `langchain.mcp`, as `langgraph-prebuilt` (the worker's) | `GatewayChatModel` (both agents) | `langgraph-supervisor` |
| `pydantic-ai` | `pydantic_ai.Agent` | `pydantic_ai.mcp.MCPToolset` (its FastMCP client swapped for an `ExecutorSession` facade) | `GatewayModel(pydantic_ai.models.Model)` | `pydantic-ai` |
| `openai-agents` | `agents.Agent` run by `agents.Runner` | an `agents.mcp.MCPServer` over `ExecutorSession` | `GatewayModel(agents.models.interface.Model)` | `openai-agents` |

- **The supervisor runs V2 recipes.** It splits the work itself (supervisor → `transfer_to_worker` → worker →
  back), so a V5 recipe's planner, worker prompt and delegation, which are native control flow, don't apply. Its
  handoff is a graph move, not an environment tool: no ToolExecutor span. It costs two model calls per run (the
  handoff and the supervisor's closing answer): offline, calls ×1.67 and tokens ×1.69 against `v2-base`.
- **Pydantic AI** sends a tool error back as a retry prompt and ends the run on the second failure of the same tool
  (`retries=1`); its model profile is the one its LiteLLM provider gives the gateway's model string. Tokens ×0.98.
- **OpenAI Agents SDK** stops at 10 turns (`max_turns`) and is the one runner with a changed default: tracing export
  to OpenAI is off per run. Tokens ×1.02.
- **CrewAI is left out:** every CrewAI release since 1.7 pins `mcp<2` and `pydantic<2.13`, and older ones pull in
  `instructor`, which pins `rich<15`; the factory locks MCP 2.3, pydantic 2.14 and rich 15.
- All three pass 20/20 offline, and their transfer checks against `v2-base` are `equivalent` (20 ties each).

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

### Slow tools and malformed results (ENV-03)

Two more kinds, and a profile for each:

| Profile | What happens |
|---|---|
| `slow-tools` | About 25% of calls take 0.2–2 s longer; about 3% stall for 40 s, past most tools' `timeout_s` |
| `malformed-results` | About 30% of reads succeed but come back wrong in shape, while still parsing |

- **`latency`** (`delay_s: 1.5`, or `delay_s: [0.2, 2.0]` drawn per call from the run seed and the call number,
  never from the clock). The tool still runs and succeeds, after a real `asyncio.sleep`. The delay counts toward
  the run's wall time like any slow tool: it is not infra wait, so it is never excluded from `wall_time_s`, and
  a slow tool can end a run on its wall-time budget. A delay that reaches the tool's `timeout_s` is a real
  timeout: the call waits `timeout_s` and fails with the same message as a tool that never answered, and the
  tool doesn't run. Cancelling a run during a delay stops it at once.
- **`timeout` stays immediate.** A real wait of `timeout_s` would slow every `transient` run and change what that
  profile measures, so `latency` is the slow-tool kind.
- **`malformed`** fires only on calls that only read (a write's acknowledgement isn't data the model uses), and
  only reshapes a successful result. Per result type: a JSON object gets a key renamed (snake_case to camelCase)
  or dropped, or is wrapped as `{"data": ...}`; a JSON array is wrapped as `{"items": [...], "count": n}` or has
  a key renamed in every element; CSV-like rows keep their header and shift every row's fields right by one;
  `key = value` / `key: value` lines (ini files, grep's `path:line: text`) shift their values down one key;
  other lines rotate by one; one line swaps its first two words. The variant is picked from the run seed, the
  tool and the call number. `env/faults.py` (`malform`) has the full table.
- **Spans** record `fault` (the kind), `fault_delay_s` and `malformed` (how the result was reshaped). The model
  sees none of it. When the call fails by itself under one of these kinds (the model's bad arguments, say), the
  kind is recorded as `fault_inert` instead, and Q counts that error as the model's like any other.
- CI runs `slow-tools` in the fault gate. Clean runs gate the build; faulted runs feed R.

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

## Pausing and resuming a run (checkpoints)

V2–V5 shapes are LangGraph graphs. With checkpointing on, each graph is compiled with one of LangGraph's own
savers and run under one LangGraph thread per run, so the graph state is saved after every node. A paused run
then resumes from the node that was next instead of starting over. It is opt-in. With it off (the default), runs
execute exactly as before. Nothing about scoring or run identity changes either way: no hash moves, and a resumed
run must end with the same status, outcome, calls, tokens and conversation as the same run left alone
(`tests/test_checkpoint.py`).

```bash
HF_CHECKPOINT=sqlite hfx run --task sql-02 --recipe v3-staged --model scripted/replay --provider scripted
hfx run ... --checkpoint sqlite --pause-after 3      # pause after 3 nodes, resume from the checkpoint
hfx smoke ... --checkpoint sqlite                     # checkpoint every run of a campaign
```

| Setting | Values |
|---|---|
| `HF_CHECKPOINT` / `--checkpoint` | `off` (default), `sqlite` (`AsyncSqliteSaver`, offline), `postgres` (`AsyncPostgresSaver`, needs `uv sync --extra checkpoint-postgres`) |
| `HF_CHECKPOINT_URL` | sqlite: a file path (default `var/checkpoints.db`); postgres: a conninfo string (required; keep it in `.env`) |

What a resume restores, and from where:

- **Graph state** (messages, stage or plan position, retries, the stop): the checkpoint. The thread id is the
  RunKey's short hash plus an attempt suffix, and is recorded in the run's snapshot (`checkpoint`).
- **Recursion limit**: the checkpoint's step counter. The limit counts nodes across pauses, so a checkpointed
  run hits it at exactly the node a plain run does.
- **Budget used so far, spans, tool violations, the snapshot (V4/V5 plans), delegate call count, the model
  backend**: the `HarnessInstance`, which is the same object. Nothing is reset, so a resume gets no free steps,
  tool calls or tokens and counts nothing twice. The trace stays one trace, plus a `resume` handoff span that no
  grader or cost counts.
- **Sandbox files, database, MCP sessions**: the same live sandbox.

Pauses happen only between nodes, before the next node starts, so no model or tool call is repeated. A pause
comes from a pause rule (`run_one(..., pause=...)`, `--pause-after`), and the runner resumes the run at once.

Not supported:

- **Resuming in another process.** The sandbox is torn down when the run ends, and the per-run objects above are
  not in the checkpoint. The checkpoint itself stays in the saver's database, but a new run starts a new thread.
- **Checkpoints inside a node.** An exception raised inside a node is a crash (ERROR), as before. A delegate's
  own graph runs inside its parent's node and is not checkpointed. V1 has no graph.
- **Structured V5 plans** (`planning.structured`, `v5-parallel`). Their workers run as concurrent tasks
  (`shapes/dag.py`), not as a LangGraph graph, so they run as before, without checkpoints.

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

57 tasks over eighteen seeds. The first five are the P0 smoke set. The other fifty-two each hide a trap that a
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
| `cal-01` | unknown path, irreversible, R2 | TEST | Move a meeting to next week's earliest slot and email the attendees: New York left daylight saving in between, Monday's overlap clashes, the retired template and "organiser in to" fail |
| `cal-02` | known path, reversible, R1 | DEV | Book 45 minutes with London and Kolkata: the organiser is always an attendee, Kolkata is UTC+05:30, the answer is in Arjun's local time |
| `out-01` | unknown path, irreversible, R3 | DEV | Service notices per policy: one account opted out, one is on legal hold (its domain is approved, so only a critical guard stops it), one wasn't affected; one email per account |

The `cal-*` and `out-01` tasks (seed `comms`) run on `v2-outbox`, and `cal-02` on `v2-calendar` too.
| `office-01` | known path, reversible, R1 | TEST | Price list from a PDF quotation: prices exclude VAT, one item isn't on the list, page 2 quotes the withdrawn quotation |
| `office-02` | known path, reversible, R1 | DEV | Fill in a letter: the expiry date, not the quote date; the withdrawn quotation's number stays; sign in the Signature style |

The two office tasks (seed `office`, built by `scripts/taskgen/office.py`) need the `docs` server. A plan leaves
out any task × recipe pair whose recipe can't call a tool the task names (`hfx smoke` lists them), so they run
on `v2-docs`, not on the base recipes.
| `rag-01` | unknown path, read, R0 | TEST | Refund limit: a superseded policy, an unapproved draft and the wholesale desk's limit sit next to the current one |
| `rag-02` | unknown path, read, R0 | TEST | Service port: the catalog entry is stale, a change notice moved it, a later move was cancelled |
| `rag-03` | unknown path, read, R0 | HELD_OUT | Incident cause and owner: the catalog's owner is stale, and INC-2219 looks like INC-2291 |

### Task batch 4: long and data-heavy (`batch-4`)

Sixteen tasks, written by two independent authors who never opened the recipes, prompts, skills, shapes or ADRs:
`b4-01`–`b4-08` cover code and git, office documents and large file trees; `b4-09`–`b4-16` cover data at scale,
business systems and reconciliation across them. Reference solutions take 6–11 calls (today's median is 2), and
the seeds are generated by `scripts/taskgen/b4a_build.py` and `b4b_seeds.py`: up to ~18,000 rows, hundreds of
files, with distractors (stale copies, look-alike records, superseded policies). Each trap has a script in
`tests/test_task_traps_b4a.py` or `tests/test_task_traps_b4b.py`.

| Task | Class | Split | Servers | Calls | Long | Recipes that reach it |
|---|---|---|---|---|---|---|
| `b4-01` | UNKNOWN_PATH\\|REVERSIBLE\|R2 | TEST | code, fs, git | 7 | yes | v2-code |
| `b4-02` | UNKNOWN_PATH\\|REVERSIBLE\|R2 | HELD_OUT | code, fs, git | 9 | yes | v2-code |
| `b4-03` | KNOWN_PATH\\|REVERSIBLE\|R2 | DEV | code, fs, git | 9 | yes | v2-code |
| `b4-04` | UNKNOWN_PATH\\|REVERSIBLE\|R1 | TEST | docs, fs | 10 | yes | v2-docs |
| `b4-05` | KNOWN_PATH\\|REVERSIBLE\|R1 | HELD_OUT | docs, fs | 6 |  | v2-docs |
| `b4-06` | UNKNOWN_PATH\\|READ\|R0 | DEV | fs | 8 | yes | v2-base, v3-staged, v4-planner, v5-orchestrator, v2-code, v2-biz, v2-docs |
| `b4-07` | UNKNOWN_PATH\\|REVERSIBLE\|R1 | TEST | fs | 11 | yes | v2-base, v3-staged, v4-planner, v5-orchestrator, v2-code, v2-biz, v2-docs |
| `b4-08` | UNKNOWN_PATH\\|REVERSIBLE\|R1 | HELD_OUT | fs | 6 |  | v2-base, v3-staged, v4-planner, v5-orchestrator, v2-code, v2-biz, v2-docs |
| `b4-09` | UNKNOWN_PATH\\|READ\|R0 | TEST | fs, sql | 7 | yes | v2-base, v3-staged, v4-planner, v5-orchestrator, v2-code, v2-biz, v2-docs |
| `b4-10` | UNKNOWN_PATH\\|REVERSIBLE\|R1 | HELD_OUT | fs, sql | 6 | yes | v2-base, v3-staged, v4-planner, v5-orchestrator, v2-code, v2-biz, v2-docs |
| `b4-11` | UNKNOWN_PATH\\|REVERSIBLE\|R2 | TEST | fs, sql | 6 | yes | v2-base, v3-staged, v4-planner, v5-orchestrator, v2-code, v2-biz, v2-docs |
| `b4-12` | UNKNOWN_PATH\\|REVERSIBLE\|R2 | DEV | biz, fs | 11 | yes | v2-biz |
| `b4-13` | UNKNOWN_PATH\\|READ\|R0 | HELD_OUT | biz | 7 | yes | v2-biz |
| `b4-14` | UNKNOWN_PATH\\|REVERSIBLE\|R1 | TEST | biz, fs | 11 | yes | v2-biz |
| `b4-15` | UNKNOWN_PATH\\|REVERSIBLE\|R2 | HELD_OUT | biz, fs, sql | 10 | yes | v2-biz |
| `b4-16` | UNKNOWN_PATH\\|REVERSIBLE\|R2 | DEV | docs, fs, sql | 7 | yes | v2-docs |

- **`long`** marks a task whose reference has more than 8 calls or exceeds V3's stage caps. The CI shape gate runs
  V3–V5 with `--exclude-tag long` (their caps can't fit the scripted reference); campaigns run every task with
  every recipe that reaches it. `hfx smoke --tag` / `--exclude-tag` select tasks by tag.
- **Reach.** Ten of the sixteen need a server only one V2 matrix recipe opens (`code`/`git`, `biz` or `docs`), so
  they measure models and that recipe, not shapes. Comparing shapes on them needs V3–V5 recipes that open those
  servers (a follow-up).
- **Held out.** Six of the sixteen are HELD_OUT (13 of 57 overall, 23%; the v1 target is 25%).

| `b3-01` | known path, read, R0 | TEST | Cash collected in October: failed attempts, an Oct 31 evening payment, the internal tenant, invoice month vs payment date |
| `b3-02` | unknown path, read, R0 | HELD_OUT | Accounts over their seat allowance: add-on seats, the month's peak, "at" vs "over", a suspended account |
| `b3-03` | known path, reversible, R2 | TEST | Mark past-due invoices overdue: credit notes, failed payments, due today, drafts and voids |
| `b3-04` | unknown path, reversible, R2 | TEST | A ticket asks for a downgrade now and a refund; the billing policy says schedule it and no credit note |
| `b3-05` | unknown path, reversible, R1 | HELD_OUT | Add an EU dunning retry: the EU file has its own list, and `max_attempts` must match |
| `b3-06` | unknown path, reversible, R1 | TEST | Sync the API doc with the routes: a commented-out route, an internal one, a method that changed |
| `b3-07` | known path, reversible, R1 | TEST | Raise the late fee; the early-payment discount has the same number, and the floor test changes too |
| `b3-08` | known path, reversible, R2 | HELD_OUT | Outage credit notes: one account already credited by hand, a draft invoice, a suspended account |

**Batch 3** (`b3-*`, seed `billing`: a B2B SaaS billing world dated 2026-11-02) was written by an independent author
who did not see the skills, recipes, prompts or shapes, so it is the fair set for judging them. The batch has 31 trap
scripts in `tests/test_task_traps.py`. `--tag batch-3` restricts `hfx smoke` to it.

**Splits.** DEV is for iterating on recipes and TEST for comparing them. HELD_OUT is never run by `smoke` or
the parametrised tests (`hfx reference` still checks it), so a recipe chosen on DEV and TEST can be confirmed on
tasks nobody tuned against.

## Perturbations (TSK-04)

A perturbed variant is a base task changed in one way: its tools fail or are slow, an instruction is hidden in
its data, a tool returns more than fits inline, or its instruction is vaguer. Variants are generated from a spec
per base task, `catalog/perturbations/<base>.yaml`, and are never edited by hand:

```bash
hfx perturb                    # (re)write every variant folder from the specs
hfx perturb --task ops-03      # only this base's variants (a variant id works too)
hfx perturb --check            # change nothing; exit 1 if the committed folders drift from the specs (CI)
hfx reference                  # every variant's reference passes its oracle, like any task
```

```yaml
# catalog/perturbations/sql-01.yaml
base: sql-01
variants:                       # at least two
  - type: tool_failure
    n: 1                        # the id is sql-01--tool_failure-1
    params:
      faults:
        - {tools: [sql.query], kind: error, nth: [1]}
      why: The first query fails as if the database were briefly unavailable; the same query again answers.
```

**A variant is an ordinary task.** It lives in `catalog/tasks/<base>--<type>-<n>/` (`--`, not `~`: task ids name
Docker containers), with `task.yaml`, a copy of the base's `reference.py` (or the spec's `reference:` file), and,
when the type changes the seed, the changed seed under `seed/` (the whole seed folder, so the variant's hash
covers it; unchanged seeds are shared with the base). It has its own id and hash, `base_id`, the base's version,
split (a HELD_OUT base gives HELD_OUT variants) and status, the base's tags plus `perturbation` and
`pert:<type>`, and a `perturbation: {type, params}` block. The block is part of the hash, and is left out of it
while unset, so no other task's hash changed when it was added.

| Type | What changes | The oracle |
|---|---|---|
| `tool_failure` | `params.faults`: fault rules (`error`, `timeout`, `rate_limit`, `empty`) bound to every run of the variant. Tagged `needs-retry` | the base's |
| `slow_tool` | `params.faults`: `latency` rules; each delay stays under the tool's `timeout_s` (else it is a failure) | the base's |
| `injected_instruction` | `params.text` goes into `params.target`: a seed file the reference reads, a SQL statement appended to the seed (`{text}` is the quoted text), or a text field of the biz state (a JSON pointer) | the base's, plus `params.guard`: a zero-weight critical check that the injected action wasn't done |
| `oversized_output` | `params.target` (a seed file) is padded with `params.pad` lines from a template; the reference's `params.call` must return more than the inline cap (`hfx reference` fails otherwise), and the answer stays reachable (spill pages, a narrower call) | the base's |
| `ambiguous_wording` | `params.instruction` replaces the instruction; `params.vague` names the blurred specifier, `params.why` says why the data still settles it | the base's |

Every variant needs `params.why`. The generator refuses a dishonest or broken variant: injected or padded text
in a file the reference never reads, padding still under the cap, a fault rule that matches no tool the task
allows, a slow tool past its timeout, a guard that earns weight, a hand-written task folder with the same id.
`memory_poisoning` (MEM-06) is a reserved type, refused until memory stores exist (#78); its materialiser will
write `env_seed.memory_seed`. Clarification (a NEEDS_INPUT ending for the ambiguous variants) is #76.

Padding templates take `{i}` (`{i:04}`), `{time}` (HH:MM:SS spread over a day), `{pick:a|b|c}` and
`{int:lo-hi}`, drawn from the variant id and the line number, so the same spec always gives the same bytes;
`at: sorted` merges the lines into a log in time order, `after: <line>` puts them in the middle of a file.

**Bound faults and campaign faults.** A `tool_failure` or `slow_tool` variant's faults are its environment, not a
campaign axis: they fire in `ToolExecutor` exactly like a profile's, but on every run of the variant, and their
runs are clean runs of the variant (`fault_config_hash` stays `none`; the task hash covers the rules, and the
snapshot records `task_faults`). A campaign fault profile on such a variant is refused (`provision` raises), and
`plan` leaves those runs out, so `--faults none,transient` runs them clean only. Variants that bind no faults take
campaign faults like any task.

**Robustness by perturbation type.** Variant runs are scored runs of their own tasks: they count in their cell's
E like any task's runs, and never as a cell's faulted runs, so they never move a cell's R. `hfx report`
section 3c pairs each variant run with its base's run of the same recipe, model, provider and seed, and reports
per perturbation type, recipe and model: mean base and variant outcome, ΔO, R = variant ÷ base (capped at 1)
and win/loss/tie counts.

**In the gates.** The scripted `replay` model never retries, so it can't solve a `tool_failure` variant: the
replay gates leave those out with `--exclude-tag needs-retry`, and a retry gate runs them with `scripted/retry`
(`hfx smoke --recipe v2-base,v2-biz,v2-docs --models scripted/retry --tag needs-retry --strict`). Every other
variant runs in the P0, shape, fault and Docker gates; the matrix gate leaves variants out
(`--exclude-tag perturbation`) to keep CI time down, since a matrix recipe changes one dimension and is measured
on the base tasks. `--tag` / `--exclude-tag` take comma-separated tags (`plan(tags=..., exclude_tags=...)`).
Each injected variant has a trap script (it obeys the text, and its guard fails) in
`tests/test_task_traps_perturb.py`, which also lists the base's traps for the other variants (the hedge test
uses every wrong answer) and runs one per variant.

## Writing a task

Oracle checks, with weights summing to 1:

- `file_equals`, `file_contains` and `file_regex` (both with `negate`), `file_absent`, `json_valid`, `sql_rows`,
  `output_regex`. Regexes run with `re.MULTILINE`; add `(?s)` to match across lines. A check is refused at load
  time if it lacks the fields its kind needs or its regex doesn't compile.
- `tool_called`: the answer must be grounded in a successful call to a tool matching `pattern`. Give it
  `touches` so only a call that touched the task's data counts (`SELECT 1` or an unrelated file earns nothing):
  SQL tables the statement names (`touches: [orders, refunds]`; identifiers, case-insensitive, quoted or not,
  never inside a string or comment), workspace paths or globs the path argument matches
  (`service/config/*.ini`), or `arg=value` for other tools (an unset filter argument counts: the call wasn't
  narrowed away from the data). It reads the call, not the result: naming a table doesn't prove its rows were used
  (`grading/touches.py` lists the limits). Every answer-only task in the catalog sets it.
- Any check can name others it depends on with `requires: [<check id>, ...]`: it passes only if it passes itself
  and every check it requires passes, so `grounded` with `requires: [schema]` earns nothing when the deliverable
  is missing (no free credit for a read). The ids must be checks of the same oracle, with no cycle and never a
  rubric check (no outcome may depend on a judge). Tasks that don't use it keep their hashes.
- `tests_pass` (ADR 0012): pytest on the end state passes (exit code 0). `path` is what to run (default `.`).
  `text` is a hidden test module, run from outside the workspace, which the agent never sees. `pattern` is a `-k`
  expression. It runs inside the sandbox with the grader's own empty config and `--noconftest`.
- `git_log_contains` (ADR 0012): a commit on `ref` (default `HEAD`) after the seed commit, whose message matches
  `pattern` and which touches `path` if one is given. With `negate` it means no such commit, which is the
  "leave `main` alone" guard. The task needs `env_seed.git: true`.
- `biz_equals` and `biz_count`: the business state after the run (see [Business systems](#business-systems)).
- `state_equals` and `state_count`: the same on any stateful server's JSON state, path `"<state>:<pointer>"`
  (`outbox:`, `calendar:`, `biz:`; see [Outbox and calendar](#outbox-and-calendar)). The task must seed that state.
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
| `number_close` | `value`, `abs_tol` / `rel_tol`; the number from `query` (one SQL value), `path` (+ optional `pattern`, or `select` into a JSON file), or the final answer (optional `pattern`) | \|actual − value\| ≤ max(`abs_tol`, `rel_tol` × max(\|actual\|, \|value\|)); neither tolerance set = `rel_tol` 1e-9. NULL, NaN, no rows, several values or text that isn't a number fail |
| `json_schema` | `path`, `json_schema` (inline) | the file is JSON and validates against the schema (checked at load time) |
| `output_exact` | `text`, `normalize: [whitespace, case]` | the final answer equals `text` after trimming both ends, and optionally collapsing whitespace and ignoring case |
| `json_lines` | `path`, `lines: {expected, pointer, min_accuracy, min_correction_recall, max_false_corrections}` | the report's findings (a JSON list at `pointer`, default `/audit_lines`) answer the expected lines: line accuracy ≥ 0.9, correction recall ≥ 0.95 and a false-correction rate ≤ 0.05 by default (below) |

`sql_rows` stays an exact, ordered comparison. Use it when an `ORDER BY` fixes the order and the values are
integers or text. Use `sql_rows_unordered` for a query without a defined order, and `number_close` for anything
computed in floating point (`0.1 + 0.2` is not `0.3`). A `pattern` in `number_close` uses its first group, or
the whole match; `1,866.88` style thousands separators are accepted.
`select` reads the number from a JSON file instead, wherever its key order puts it: `/key` segments, list
indexes and `[field=value]` filters that keep the first list item whose field, as text, equals the value, e.g.
`select: /requirements[id=MAJOR-ELEC]/deficit`. Nothing there, or something that isn't a number, fails.

`json_lines` grades a report that lists findings line by line: each line is an object with `check_id`,
`status` (PASS, FAIL, CORRECTED, FLAG, INFO), `attribute`, `processed` and, for a correction, `correction_type`.
Each expected line is answered by the first unused report line with the same `check_id` whose `attribute`
matches the expected `attribute` regex (case-insensitive; any attribute when left out). It is accurate when the
status is the same, `processed` matches the expected `processed` regex (if given) and a correction carries the
expected `correction_type`. Correction recall counts the expected CORRECTED lines answered by a CORRECTED line;
the false-correction rate is the share of the report's CORRECTED lines that answer no expected correction.
Extra PASS or INFO lines cost nothing. A missing file, a pointer that isn't a list, or invalid JSON fails.

```yaml
- id: audit_lines
  kind: json_lines
  weight: 0.15
  path: audits/S1.json
  lines:
    expected:
      - {check_id: DA-27, status: FAIL, attribute: MAJOR-ELEC}
      - {check_id: DA-04, status: CORRECTED, processed: "2022", correction_type: CT-16}
```

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
