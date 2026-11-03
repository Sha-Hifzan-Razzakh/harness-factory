<!-- Snapshot of the "Harness Factory — Requirements v1" Claude Doc (https://claude.ai/artifact/1pWpX5ui9KmP751n8KvaTz), exported 2026-10-30 at doc revision 93 so sessions without access to the doc can read the requirement IDs. The doc is the source of truth: when it changes, export it again over this file. Its 4 diagrams are left out (placeholders below). -->

# Harness Factory — Requirements v1

Oct 27, 2026 · @Hifzan

## Purpose and scope

The Harness Factory builds an agent harness per task from a declarative recipe, measures how well it performs, and learns which recipe to pick for each class of task. It targets general task automation (code, documents, data, business APIs, research, file operations), not coding alone, and calls models through several API providers chosen per campaign by cost and quota.

The core loop:

1. **Profile** the task into a TaskProfile and a task class.
2. **Select** a recipe: hard rules prune, measured effectiveness ranks.
3. **Provision** the recipe into a running harness inside an isolated sandbox.
4. **Run** it against a seeded environment, optionally with injected faults.
5. **Grade** with deterministic graders first, a calibrated LLM judge second.
6. **Record** an EffectivenessRecord per task class, recipe and model.
7. **Learn**: the selector updates, so the next task of that class gets a better recipe.

What varies between recipes: shape (variants 1 to 5), control tier (T1 to T4), model slots, tool slice, skill set and loading mode, prompt layers, runtime config, policies, memory profile, retrieval config, the MCP servers it connects to (local or remote), and the other agents it may delegate to.

**Scale rule.** Scale lives in what runs (tasks, tools, environments, runs), not in what is specified. Every phase ends with runs and a written finding.

| Term | Meaning |
| --- | --- |
| Recipe | Versioned manifest that fully defines one harness configuration |
| Task class | Group of tasks with the same profile on the deciding dimensions |
| Cell | One task class × recipe × model combination |
| Run | One execution of one task under one recipe, model, seed and fault config |
| Campaign | A declared matrix of cells executed together and reported as one |

## Scale targets (v1 done)

v1 is done when a full campaign of about 9,000 runs completes across 6 domains, 5 shapes and 3 models served by several API providers, and the selector beats the best single recipe on held-out tasks.

| Dimension | v1 target | Why this size |
| --- | --- | --- |
| Task domains | 6: code, documents, data/SQL, business APIs, web research, file and ops | Enough spread that no single recipe wins everywhere |
| Base tasks | 60 (10 per domain) | 4 to 6 task classes per domain with 2+ tasks each |
| Perturbed variants | 120+ (2 per base task, generated) | Robustness is measured, not assumed |
| Depth mix | ≥ 20% single-step, ≥ 40% known-path, ≥ 30% unknown-path | Shallow tasks cannot separate shapes |
| Side-effect tasks | ≥ 20 that write state (DB rows, files, API records, git) | Tier and approval rules need real stakes |
| Held-out tasks | 15 (25%), never used for tuning | Honest selector evaluation |
| Harness shapes | 5 (V1 to V4 in campaign 1, V5 in campaign 3) | Full shape axis |
| Models | 3 open-weight models, small to large, each served by ≥ 2 API providers where possible | Shows where harness compensates for model |
| MCP servers | 8 | Realistic tool surface across domains |
| Tools | ≥ 40 operations | Large enough that tool selection and schema budgeting matter |
| Skills | ≥ 12 | Skill axis has on/off and loading-mode effects to measure |
| Prompt templates | ≥ 20 across system / operator / task layers | Prompt axis is ablatable |
| Recipes | ≥ 40 (8+ base recipes × axis overlays) | Selector has real choices |
| Seeds per cell | ≥ 5 | Free models are high-variance; spread must be reported |
| Runs per full campaign | 8,000 to 12,000 after pruning | 60 tasks × \~10 eligible recipes × 3 models × 5 seeds |
| LLM-judge calibration | ≥ 100 hand-scored items | Judge agreement is measured before it is trusted |
| Selector target | ≥ 10% higher composite score than the best global recipe, or equal score at ≤ 60% cost, on held-out tasks | The factory has to beat a fixed choice |
| Profiler target | ≥ 80% agreement with authored profiles on depth, side effects and risk | Selection is only as good as profiling |

## System components

The factory is 17 components in five rows: registries, the control loop, the per-run harness, the environment, and scoring.

&#91;embedded content: Harness Factory components · 17 boxes, 5 rows, 1 feedback loop\]

A task enters at the runner, is profiled and matched to a recipe, and the provisioner builds a sandboxed harness that reaches tools only through the fault proxy. Graders score the run, and the results store feeds the selector, so every run improves the next choice.

## Functional requirements

Each requirement has an ID, a statement, and a priority: **Must** for v1, **Should** for v1 if time allows.

### Task registry (TSK)

| ID | Requirement | Priority |
| --- | --- | --- |
| TSK-01 | A task is data: id, version, domain, instruction, input fixtures, environment seed, allowed tool superset, success oracle, grader ids, risk class, expected depth, tags | Must |
| TSK-02 | Every task has a scripted reference solution that passes its graders; a task without one cannot enter a campaign | Must |
| TSK-03 | Environment state resets to the task's seed before every run | Must |
| TSK-04 | Perturbations are generated from a base task by type: tool failure, ambiguous wording, injected instruction in data, oversized tool output, slow tool | Must |
| TSK-05 | Tasks are versioned; every result references the task version | Must |
| TSK-06 | Tasks are split into dev, test and held-out; held-out is locked from tuning | Must |
| TSK-07 | An authoring CLI validates the schema, runs the reference solution and registers the task | Should |

### Environment and tools (ENV)

| ID | Requirement | Priority |
| --- | --- | --- |
| ENV-01 | 8 MCP servers exposing ≥ 40 tools: filesystem, SQL database, git and code runner, documents (docx, pdf, xlsx), mock business APIs (CRM, invoicing, ticketing), email and notification outbox, web search and fetch over a recorded corpus, calendar | Must |
| ENV-02 | All external services are local mocks or recorded fixtures; live mode is opt-in and flagged in results | Must |
| ENV-03 | A fault-injection proxy sits between harness and MCP servers: error rate, latency, malformed result, truncation, set per run | Must |
| ENV-04 | A state-inspection API returns the before/after diff of environment state for graders | Must |
| ENV-05 | Every tool declares its schema, side-effect class (read, reversible, irreversible) and risk class | Must |
| ENV-06 | Tool servers report health; a run does not start against an unhealthy server | Should |

### MCP client (MCP)

The ENV requirements cover the server side; these cover the harness side of the protocol. Every tool call goes through an MCP session the run opens, and the tool set is frozen for the run so results stay comparable.

| ID | Requirement | Priority |
| --- | --- | --- |
| MCP-01 | Each run opens one MCP client session per server in its tool slice, negotiates protocol version and capabilities, and closes every session at teardown | Must |
| MCP-02 | Tool discovery: list\_tools at session start builds the run's tool catalog; schema hashes are recorded in the run snapshot, and drift from the declared catalog fails provisioning | Must |
| MCP-03 | Both transports: stdio for servers inside the sandbox, streamable HTTP for shared servers; the transport is recorded per run | Must |
| MCP-04 | MCP resources (corpora, task files) are read as data blocks and counted in context accounting | Should |
| MCP-05 | Server-provided prompts are disabled in campaigns; prompts come only from the prompt registry, keeping the prompt axis clean | Must |
| MCP-06 | Elicitation: a server's request for input is routed like an approval in attended runs and ends a headless run as NEEDS\_INPUT | Must |
| MCP-07 | Sampling: server-initiated model calls are refused by default; when a recipe allows them they go through the model gateway, charged to the run's budget and quota, and traced | Should |
| MCP-08 | list\_changed notifications mid-run are logged and not applied; the tool set is frozen per run | Must |
| MCP-09 | Tool binding is an ablation axis: the same tool called over MCP or in-process, to measure protocol overhead | Should |
| MCP-10 | HTTP servers receive a per-run scoped token from the secret broker; tokens never reach the model or a trace | Must |
| MCP-11 | Every server passes an MCP conformance suite (initialize, list\_tools, call\_tool, error cases) in CI | Should |

### Tools, external MCP servers and other agents (EXT)

Which tools a harness gets, which MCP servers it connects to, and which other agents it can call are recipe variables, measured like the shape or the prompt. MCP covers how sessions work; EXT covers what may be connected, how it stays isolated and graded, and how its effect is measured. Rows marked *when used* apply as soon as that capability is in a recipe.

| ID | Requirement | Priority |
| --- | --- | --- |
| EXT-01 | The tool set is a recipe axis: a matrix recipe adds or removes MCP servers and tools (`tools.mcp_servers`, `tools.tool_ids`); the report gives its paired effect, and the selector ranks tool sets per task class | Must |
| EXT-02 | Each run records what the model was offered: the servers started, their transports, and the tools left after recipe slice ∩ task allowance | Must |
| EXT-03 | Bring your own tool: a new local MCP server declares each tool's side effect, risk and timeout; its schemas are frozen, and any change to them makes a new experiment (`env_hash`) | Must |
| EXT-04 | Rule-granted tools: a read-only tool that reveals nothing beyond tools a task already allows declares `granted_by` and is offered without editing tasks; a tool with side effects is never granted this way | Should |
| EXT-05 | External MCP servers: an existing remote server (a third-party service or an organisation's MCP gateway) is named in the tool catalog by URL and pinned version; its tool schemas are frozen like local ones, and URL and version are part of `env_hash` | Should |
| EXT-06 | Network allowance (*when used*): the sandbox gets no network for a remote server: the factory's MCP client connects to it from the factory process, only to servers declared in the catalog, while the sandbox keeps --network none (met this way in ADR 0004) | Must |
| EXT-07 | Checkable remote effects (*when used*): a remote server with side effects runs against a test tenant or repository that is reset before each run and readable by graders; a remote server without one is allowed read-only | Must |
| EXT-08 | Record and replay: remote calls are recorded during a campaign and replayed in re-runs and CI; replayed runs are marked, and live calls are flagged in results (ENV-02) | Should |
| EXT-09 | Agent as a tool: any shape, not only V5, can be given a delegate tool that runs a child recipe on a sub-task in the same sandbox through the same model gateway; the child's tokens, cost and quota count in the parent run, and its transcript is linked from the parent's trace (extends SHP-05) | Should |
| EXT-10 | External agents: an agent exposed as an MCP server, or reached through an agent-to-agent protocol, is a tool with a declared side effect and risk; its reported usage and latency are recorded, and its calls count toward the run's budgets | Should |
| EXT-11 | Delegation limits (*when used*): a maximum delegation depth and number of delegate calls per run; an agent may not delegate to itself or to a recipe already on its call chain; a delegate's grant is a subset of its parent's | Must |
| EXT-12 | Delegation is a recipe axis: none, child recipe, or external agent, compared with paired verdicts like any other change | Should |
| EXT-13 | Credentials for remote servers and external agents come only from the secret broker, scoped per run (MCP-10); they never reach the model, a trace, a task file or a recipe | Must |

### Task profiler (PRF)

| ID | Requirement | Priority |
| --- | --- | --- |
| PRF-01 | TaskProfile has 10 dimensions: step depth, side effects, tool surface, input ambiguity, cross-step state, external knowledge, parallelism, risk class, duration, human need | Must |
| PRF-02 | Profile-to-task-class mapping is deterministic and versioned | Must |
| PRF-03 | Seed tasks carry hand-authored profiles | Must |
| PRF-04 | An automated profiler (rules plus LLM) profiles new tasks with ≥ 80% agreement on depth, side effects and risk | Should |
| PRF-05 | The profiler reports confidence; low confidence routes to a conservative recipe | Should |

### Recipe registry (RCP)

| ID | Requirement | Priority |
| --- | --- | --- |
| RCP-01 | HarnessRecipe fields: id, version, variant, headless flag, tier, model slots, tool slice, skills and loading mode, prompt layers, runtime config, policies, memory profile, grader hooks | Must |
| RCP-02 | Recipes reference tools, skills, prompts and models by id and version; no inline content | Must |
| RCP-03 | Overlay inheritance: a recipe is a base plus a diff, so a one-axis variation is a one-line file | Must |
| RCP-04 | A generator expands a base recipe over declared axis values into a recipe set for ablation | Must |
| RCP-05 | Static validation before provisioning: refs resolve, tier ≥ highest tool risk, model meets the shape's needs (tool calling, context length, JSON mode), budgets set | Must |
| RCP-06 | Recipe identity is the content hash of the resolved recipe | Must |

### Provisioner (PRV)

| ID | Requirement | Priority |
| --- | --- | --- |
| PRV-01 | provision(recipe) returns a runnable harness: substrate, shape plugin, MCP clients, skill loader, prompt assembler, policies | Must |
| PRV-02 | Each run gets its own sandbox with scoped MCP servers; no shared mutable state across runs | Must |
| PRV-03 | The resolved config snapshot is stored with the run | Must |
| PRV-04 | Teardown is guaranteed, including on crash or timeout | Must |
| PRV-05 | Provisioning overhead is under 5 s, excluding model load | Should |

### Shared substrate (SUB)

| ID | Requirement | Priority |
| --- | --- | --- |
| SUB-01 | A model gateway gives one interface to all catalogued providers for ≥ 3 models, with per-model capability metadata, retries and rate-limit handling (see MOD) | Must |
| SUB-02 | Tool-calling emulation for models without native support (prompted JSON, parse, repair), flagged in the trace | Must |
| SUB-03 | The tool executor validates args, enforces timeouts, retries per policy and attaches idempotency keys to side-effecting calls | Must |
| SUB-04 | Budgets on tokens, tool calls, steps and wall time; exceeding any ends the run as BUDGET\_EXCEEDED | Must |
| SUB-05 | One typed error taxonomy is shared by all shapes | Must |
| SUB-06 | Context-window accounting per section: system, tools, history, retrieval, tool results | Must |
| SUB-07 | Large tool results are truncated or spilled to a file with a reference | Must |
| SUB-08 | Structured output with schema validation and repair | Must |
| SUB-09 | Checkpointing for shapes 3 to 5, so a run resumes after a crash | Should |
| SUB-10 | Deferred tool loading or tool search when the tool slice exceeds a token threshold | Should |

### Harness shapes (SHP)

| ID | Requirement | Priority |
| --- | --- | --- |
| SHP-01 | All shapes implement run(task, recipe) → Result {output, status, trajectory, cost, violations}; status ∈ SUCCESS, PARTIAL, FAIL, BUDGET\_EXCEEDED, NEEDS\_INPUT, POLICY\_BLOCKED, ERROR | Must |
| SHP-02 | Five shapes: V1 single-call, V2 tool loop, V3 workflow graph, V4 planner, V5 orchestrator | Must |
| SHP-03 | V3 graphs are versioned data per task class, not code per task | Must |
| SHP-04 | V4 emits an explicit plan object and records every replan | Must |
| SHP-05 | V5 sub-agents are child recipes with a delegation grant that is a subset of the parent's; handoffs are traced | Must |
| SHP-06 | Headless overlay: no user turns; a clarification need ends the run as NEEDS\_INPUT | Must |
| SHP-07 | Adding a shape means registering a plugin; no core changes | Should |

### Runners (RUN)

A runner is the framework that executes a harness. Every score so far comes from the native runtime, while production agents usually run on a framework, so runners are benchmarked like models. A framework runner must be that framework's own agent, never a generic loop given a framework's name. Design notes: `docs/design/test-matrix.md` in the repo.

| ID | Requirement | Priority |
| --- | --- | --- |
| RUN-01 | A recipe names its runner (`runtime`: native, langgraph-prebuilt, llamaindex-agent, …); unset means native and is left out of the recipe hash, so existing hashes stay | Should |
| RUN-02 | Each framework runner is built from that framework's own components and idioms (e.g. LangGraph's prebuilt agent and ToolNode, LlamaIndex's FunctionAgent or AgentWorkflow). Its prompts, tool formatting, call parsing, retries and history handling stay at the framework's defaults. No generic loop re-implemented in factory code counts as a framework runner | Must (for any runner) |
| RUN-03 | Only the model client and the tool callables are wrapped: model calls go through ModelGateway (quota, tokens, cost, error classification), tool calls through ToolExecutor (allowance, policy, faults, traces). A runner whose model calls can't be routed this way is not benchmarked as a runner | Must (for any runner) |
| RUN-04 | The runner and its exact framework version are part of the run's identity; a framework upgrade starts new cells | Must (for any runner) |
| RUN-05 | Transfer check: same recipe, tasks, seeds and model under native and under a runner; the paired E0 difference is judged with the provider-calibration rule (equivalent, different, inconclusive) and the verdict is frozen | Should |
| RUN-06 | The runner is a cell axis (task class × recipe × model × runner), and the selector may pick runner and recipe together per task class | Should |
| RUN-07 | A runner report adds: tokens spent on the framework's hidden prompts and tool formatting, extra model calls from framework retries, invalid tool calls caused by tool formatting, R under injected faults, wall-time overhead | Should |
| RUN-08 | Every runner is testable offline: scripted models drive it through the wrappers, and CI gates each runner | Must (for any runner) |
| RUN-09 | Every runner gets the same tasks, tool permissions and per-run budget; framework defaults are recorded, not tuned per runner (tuning is its own experiment) | Must (for any runner) |
| RUN-10 | Every framework a team may deploy on is a candidate runner (catalog below). The "one agent framework" decision still holds for shapes: frameworks enter only as runners, a separate axis, so shape comparisons stay free of framework effects | Should |
| RUN-11 | A framework in maintenance mode (AutoGen) is benchmarked for existing deployments and migration, e.g. the same recipe on AutoGen and on its successor Microsoft Agent Framework, not as a default pick | Should |

**Runner catalog.** The model-client and MCP columns name each framework's extension points as known today; check them against the framework's current docs when its runner is built.

| Runner | Framework (Python) | Status, Oct 2026 | Model calls into ModelGateway via | MCP tools via | Order |
| --- | --- | --- | --- | --- | --- |
| native | the factory's own graphs | built | (direct) | MCP SDK sessions | built |
| langgraph-prebuilt | LangGraph ready-made agent | active | a LangChain chat-model class that calls the gateway | langchain-mcp-adapters | 1 |
| llamaindex-agent | LlamaIndex FunctionAgent / AgentWorkflow | active | a LlamaIndex LLM class that calls the gateway | LlamaIndex MCP tool spec | 1 |
| ms-agent-framework | Microsoft Agent Framework (`agent-framework`) | 1.0 GA on 3 Apr 2026; successor to Semantic Kernel and AutoGen | a custom chat client | built-in MCP support | 2 |
| semantic-kernel | Semantic Kernel v1.x agents | supported at least a year after MAF GA; new features go to MAF | a custom chat-completion service | SK MCP plugin (Python) | 3 |
| autogen-agentchat | AutoGen AgentChat 0.4+ (`autogen-agentchat`) | maintenance mode since 2 Oct 2025; fixes only | a custom chat-completion client | autogen-ext MCP tools | 3 (migration) |
| langgraph-supervisor | LangGraph supervisor | active | as langgraph-prebuilt | as langgraph-prebuilt | 3 (V5) |
| crewai | CrewAI | check at build | a custom LLM class | CrewAI tools MCP adapter | 4 |
| openai-agents | OpenAI Agents SDK | check at build | a custom model provider | the SDK's MCP server classes | 4 |
| pydantic-ai | Pydantic AI | check at build | a custom model class | Pydantic AI MCP toolsets | 4 |
| google-adk | Google ADK | check at build | a LiteLLM-backed model wrapper | ADK MCP toolset | 5 |
| smolagents | smolagents | check at build | a custom model class | MCP tool collection | 5 |
| ag2 | AG2 (community fork of AutoGen) | third-party, active | check at build | check at build | 5 |

Sources for the Microsoft rows: [Semantic Kernel and Agent Framework](https://atlan.com/know/ai-agent/microsoft/semantic-kernel/) · [AutoGen status](https://atlan.com/know/ai-agent/what-is-autogen/) (vendor summaries of Microsoft's announcements; confirm against Microsoft's repos before relying on dates).

### Skills and prompts (SKP)

| ID | Requirement | Priority |
| --- | --- | --- |
| SKP-01 | A skill registry holds ≥ 12 skills in SKILL.md form: trigger description, not-for, steps, resources, version | Must |
| SKP-02 | Skill loading mode is a recipe axis: none, all up front, on demand by trigger | Must |
| SKP-03 | A prompt registry holds versioned templates in system, operator and task layers; per-model variants allowed | Must |
| SKP-04 | Prompt assembly is deterministic for a given recipe and task; the assembled prompt's hash is in the trace | Must |
| SKP-05 | Assembly keeps a stable prefix (system, tools, skill index) so provider prompt caching can apply | Must |
| SKP-06 | Skill loads are traced (which skill, when, triggered by what) so each skill's value can be measured | Should |
| SKP-07 | Skills are judged on tasks written by someone who has not seen them; gains on tasks by the skills' author are reported as an upper bound. A skill is general how-to, never a task's answer, and its text is part of the hash of every recipe that uses it | Must |
| SKP-08 | On-demand skills are served through a skills tool (MCP) from outside the workspace, so file tools can't read or change them and the graded diff never includes them | Must |

### Memory and knowledge (MEM · KNW)

Memory and retrieval are recipe axes like tools and skills, at v1 scale of 4 memory profiles, 4 retrieval configs and 2 recorded corpora. In campaigns, memory starts from the task's seed and is discarded at run end, so runs stay independent and reproducible.

| ID | Requirement | Priority |
| --- | --- | --- |
| MEM-01 | A memory profile is a recipe field and ablation axis: working memory only, plus rolling summary, plus vector recall, plus structured facts | Must |
| MEM-02 | One MemoryStore interface across memory kinds: write, recall, forget; every read and write is a span | Must |
| MEM-03 | Campaign runs start from the task's memory seed (part of the environment seed) and discard memory at run end; nothing carries between runs | Must |
| MEM-04 | A write policy decides every memory write: no secrets, no content tagged as injected, size and record-count limits; rejected writes are logged | Must |
| MEM-05 | Compaction: at the recipe's compaction threshold, older turns are replaced by a summary; the summary and the tokens dropped are traced | Must |
| MEM-06 | A memory-poisoning perturbation seeds an instruction into memory and measures whether the harness obeys it | Should |
| MEM-07 | API tasks can be multi-turn: sessions persist and resume by session id, compacting on resume | Should |
| KNW-01 | Corpora are versioned with DVC and indexed once per version; the index id is recorded with every run | Must |
| KNW-02 | Retrieval is a recipe axis: none, BM25, dense or hybrid; reranker on or off; top-k | Must |
| KNW-03 | Every retrieved chunk carries its source id and offsets in an evidence store; a grounding grader checks an answer's citations against it | Must |
| KNW-04 | Retrieved chunks pass the instruction boundary as data, never as instructions | Must |
| KNW-05 | Retrieval quality is measured on its own (context precision and recall) on tasks with gold chunks | Should |

### Governance in the harness (GOV)

| ID | Requirement | Priority |
| --- | --- | --- |
| GOV-01 | Permission modes ask, auto, deny per tool side-effect class; in campaigns, ask is answered by a scripted approver and counted | Must |
| GOV-02 | Tool outputs and retrieved content are tagged as data; injection perturbations measure whether the harness obeys them | Must |
| GOV-03 | Irreversible tools require an approval gate at tier T3 and above | Must |
| GOV-04 | Secrets are injected at the tool layer only and never appear in prompts or traces | Must |
| GOV-05 | Guardrail sets (input, tool argument, output) are recipe fields and can be ablated | Should |
| GOV-06 | Traces are redacted for configured patterns before storage | Should |

### Campaign runner (RUN)

| ID | Requirement | Priority |
| --- | --- | --- |
| RUN-01 | A campaign is a declared file: task set × recipe set × model set × seeds × fault configs | Must |
| RUN-02 | Hard rules prune ineligible cells before execution; each pruned cell is logged with its rule | Must |
| RUN-03 | Parallel execution with concurrency limits per model endpoint and per free-tier rate limit | Must |
| RUN-04 | Campaigns resume: completed runs are skipped by run key (task version, recipe hash, model, seed, fault config) | Must |
| RUN-05 | Every run is reproducible from its key and stored snapshots | Must |
| RUN-06 | A cost ledger per run: tokens in and out, tool calls, wall time, $ equivalent at a reference price | Must |
| RUN-07 | Throughput runs at the combined provider limits in force without breaching it; projected duration is shown before start | Should |
| RUN-08 | A smoke campaign (1 seed, 1 model, all tasks) finishes in under 1 hour | Should |

### Graders (GRD)

| ID | Requirement | Priority |
| --- | --- | --- |
| GRD-01 | Grader types: deterministic (state diff, schema, exact or regex match, test pass, SQL result equality), trajectory (forbidden tool, step cap, loop detection), safety (violations), LLM judge with rubric | Must |
| GRD-02 | Every task has ≥ 1 deterministic grader; an LLM judge is never the only grader | Must |
| GRD-03 | The LLM judge is calibrated on ≥ 100 hand-scored items with Cohen's kappa ≥ 0.7 before use | Must |
| GRD-04 | Graders are versioned and can re-grade stored traces without re-running | Must |
| GRD-05 | Partial credit is defined per task as weighted checks, not judge opinion | Must |

### Traces and results (TRC)

| ID | Requirement | Priority |
| --- | --- | --- |
| TRC-01 | One span schema across shapes, OpenTelemetry-compatible: model\_call, tool\_call, retrieval, plan, replan, verify, approval, handoff, skill\_load, compaction | Must |
| TRC-02 | Each span records tokens, latency, status and error type | Must |
| TRC-03 | Each run produces an EffectivenessRecord: run key, task class, status, scores, cost, trajectory metrics, violations | Must |
| TRC-04 | The results store aggregates per cell: mean, spread, 95% confidence interval | Must |
| TRC-05 | A trace viewer diffs two runs of the same task side by side | Should |

### Analysis (ANL)

| ID | Requirement | Priority |
| --- | --- | --- |
| ANL-01 | Standard reports: shape × model by task class; one-axis ablations (tools, skills, prompts, config); crossover by step depth | Must |
| ANL-02 | Differences are reported with confidence intervals; non-significant ones are flagged | Must |
| ANL-03 | A campaign-to-campaign regression report | Must |
| ANL-04 | Failure clustering by error type and span kind, with example traces | Should |

### Selector (SEL)

| ID | Requirement | Priority |
| --- | --- | --- |
| SEL-01 | Hard rules prune first: tier ≥ risk, shape fits step depth, model meets capability needs | Must |
| SEL-02 | Remaining recipes are ranked by composite effectiveness for the task class, with confidence | Must |
| SEL-03 | Cold start falls back to a rule-based default recipe per class | Must |
| SEL-04 | When the top two overlap within confidence, both run and the result updates the table | Must |
| SEL-05 | Every decision is logged with the rule and evidence used | Must |
| SEL-06 | Evaluated on held-out tasks against the best global recipe, rules-only selection, and a hand-picked baseline | Must |

### Interfaces (API)

| ID | Requirement | Priority |
| --- | --- | --- |
| API-01 | FastAPI service: submit task → profile → select → provision → run → result | Must |
| API-02 | CLI for task authoring, recipe generation, campaigns and reports | Must |
| API-03 | Streamlit console for campaign results, trace viewing and recipe diffs | Should |
| API-04 | Export a winning recipe to external harness config (agent YAML, plugin bundle) for use outside the factory | Should |

## Metrics and effectiveness score

Every run is scored on six metric families; the selector ranks recipes on one composite score whose weights depend on the task class, and any critical safety violation zeroes it.

| Family | Metrics | Source |
| --- | --- | --- |
| Outcome | Pass rate; partial-credit score 0 to 1 | Graders |
| Efficiency | Tokens in and out, $ equivalent, wall time, steps, tool calls | Cost ledger |
| Trajectory | Invalid tool calls, tool-error rate, loops detected, replans, wasted calls (results never used) | Traces |
| Robustness | Pass rate per perturbation type; degradation versus the clean run | Perturbed runs |
| Safety | Violations, unauthorized call attempts, injection compliance, approvals triggered | Safety graders |
| Reliability | Score spread across seeds | Seeds per cell |

```latex
E_0 = \begin{cases} 0 & \text{if any critical violation} \\ \dfrac{w_o O + w_q \, Q \, O + w_r R}{w_o + w_q + w_r} & \text{otherwise (R and } w_r \text{ left out until measured)} \end{cases} \qquad E = \max(0,\; E_0 - w_c C), \quad C = \tfrac{1}{2}\min\!\left(\tfrac{\text{cost}}{\text{median}_{\text{task}}},\, 2\right)
```

O is the outcome score (0 to 1): the oracle's weighted checks. R is robustness: the mean outcome of a cell's runs under a fault profile divided by the mean outcome of its clean runs, capped at 1. It is computed per cell at aggregation, never stored per run. It uses outcome rather than pass rate so partial results count: a harness that keeps 80% of its result under faults scores 0.8, not 0. Faulted runs feed only R; they stay out of the cell's E mean and the cost medians. Until a cell has both clean and faulted runs, or when its clean outcome is 0, R is left out with its weight, not counted as 1. Q is trajectory quality: 1 minus the share of invalid and repeated calls, where a reply the provider rejects as malformed (such as a call to a tool that doesn't exist) counts as an invalid call, whether or not the harness then re-asks; it counts as Q·O, so a clean path to a wrong end earns nothing. C is normalized cost: run cost over the task's median, capped at 2, divided by 2.

| Task class profile | w\_o | w\_r | w\_q | w\_c |
| --- | --- | --- | --- | --- |
| Read-only, risk R0 to R1 | 0.50 | 0.15 | 0.10 | 0.25 |
| Reversible writes, risk R2 | 0.45 | 0.25 | 0.15 | 0.15 |
| Irreversible writes, risk R3 | 0.40 | 0.30 | 0.20 | 0.10 |

How the score is computed in the build (P0–P2, as implemented):

- **E₀ is stored per run; E is computed at aggregation.** C is relative to other runs, so it is never baked into a stored run.
- **Median per provider and task, not per task class.** Tasks in one class differ in size, so a class median would penalize every harness on the bigger task; pooling providers would score real runs against scripted replays.
- **Median over successful runs only.** Counting failures would make the median the price of giving up early. A task nobody solved falls back to all its runs.
- **Cost basis:** reference USD when every successful run is priced, otherwise total tokens.
- **Frozen baselines.** Per-task medians are saved as a content-addressed baseline, so new runs never move old scores.
- **No free credit.** Preservation checks are zero-weight critical guards; a run that does nothing scores 0.

How a run's ending is scored (review rounds 1–3):

| How it stopped | Status | Scored? |
| --- | --- | --- |
| By itself (the model answered) | SUCCESS / PARTIAL / FAIL from the oracle | Yes |
| Harness overran: budget, V3 stage cap, context overflow, malformed model output | Graded on the environment; outcome × 0.5, status at most PARTIAL; reason recorded as a violation | Yes |
| Infrastructure: provider 5xx, timeout, 429 after retries, daily quota | INCOMPLETE | No, reported per cell |
| Setup or crash: auth, unknown model id, rejected request, factory bug | ERROR | No, reported per cell |

- **Why overruns are graded:** zeroing them was unfair between shapes (a V3 stage cap ends a stage, a V2 step cap ends the run).
- **Why they are penalised:** otherwise a harness that solves early and keeps calling until a cap ties a clean run, and as a “success” would inflate the cost median.
- **Trajectory Q:** a repeat is wasted if nothing changed since (a new write changes the world; an identical write does not); a new V3 stage starts a new scope.
- **Cells** are keyed by task class × recipe × model × provider × environment hash until cross-provider equivalence is calibrated.

Weights are versioned config. Reports always show the families separately as well, so the composite never hides a trade-off.

## Non-functional requirements

The factory must be reproducible, isolated and extendable by registration alone; the only accepted source of variance between two runs with the same key is the model itself.

| ID | Area | Requirement |
| --- | --- | --- |
| NFR-01 | Reproducibility | The same run key yields the same environment, assembled prompt and config; model nondeterminism is measured through seeds |
| NFR-02 | Isolation | No state leaks between runs, verified by a canary test: one run writes a marker, the next must not see it |
| NFR-03 | Determinism | Campaigns use mocks and recorded corpora only; no live network unless the campaign flags it |
| NFR-04 | Extensibility | A new task, tool, skill, prompt, grader, shape or model is added by registration; core code is untouched |
| NFR-05 | Single source | Tasks, recipes, profiles, weights and rules are versioned data modules in the repo; reports and console are generated from them |
| NFR-06 | Portability | Runs on one machine with Docker Compose; no GPU needed since API providers serve the models |
| NFR-07 | Cost | Models via API providers; free tiers for development, smoke runs and CI; full campaigns inside a declared request, token and dollar budget |
| NFR-08 | Storage | Postgres for registries and results; traces as compressed JSONL; campaign traces kept, smoke traces kept 30 days |
| NFR-09 | Self-observability | Runner, provisioner and gateway emit their own metrics: queue depth, failures, retries, endpoint saturation |
| NFR-10 | Testing | Unit tests for substrate and graders; every shape passes one contract test suite; every reference solution runs in CI |
| NFR-11 | Security | Secrets live in environment or vault, never in recipe files; mock services bind to the internal network only |

## Frameworks and stack

Every component maps to one framework, and shapes V2 to V5 all run on LangGraph, so a difference between shapes is never a difference between frameworks.

| Layer | Framework | Role | Requirements |
| --- | --- | --- | --- |
| Language and packaging | Python 3.12, uv, Docker Compose | One-machine install, pinned deps | NFR-06 |
| Schemas and data modules | Pydantic v2 | Task, profile, recipe, result models; content hash from canonical JSON | TSK-01, PRF-01, RCP-01, RCP-06, SHP-01 |
| LLM access | Several OpenAI-compatible providers: OpenRouter, Groq, Cerebras, Google AI Studio, Mistral and others | All model calls; provider fixed per cell by the allocation planner | SUB-01 |
| Model gateway | LiteLLM (Python router) | Router across providers: one deployment per provider, rpm/tpm as hard limits, cost-based routing for live tasks, retries, cost ledger | SUB-01, RUN-03, RUN-06 |
| Structured output and tool-call emulation | instructor; provider structured outputs where the model supports them | Pydantic-validated output with retry and repair | SUB-02, SUB-08 |
| Shape V1 | Plain Python on LiteLLM | Single call, no graph needed | SHP-02 |
| Shapes V2 to V5 | LangGraph with Postgres checkpointer | V2 prebuilt tool loop; V3 StateGraph from graph data; V4 plan-and-execute graph; V5 supervisor with subgraphs | SHP-02 to 05, SUB-09 |
| MCP servers | FastMCP | All 8 tool servers | ENV-01, ENV-05 |
| MCP client in harness | langchain-mcp-adapters | MCP Python SDK sessions wrapped by the run's session pool; tools bound into LangGraph | PRV-01 |
| Fault-injection proxy | FastMCP proxy plus middleware | Errors, latency, truncation per run | ENV-03 |
| Mock business services | FastAPI apps on Postgres seeds; python-docx, openpyxl, pypdf; GitPython | CRM, invoicing, ticketing, documents, git | ENV-01, ENV-04 |
| Retrieval for research tasks | LlamaIndex, Qdrant, BM25 plus a local cross-encoder reranker | Recorded corpus, hybrid retrieval | ENV-01 |
| Sandbox | Docker, one container per run | Isolation and teardown | PRV-02, PRV-04 |
| Campaign runner and scoring | Inspect AI (MIT) | Tasks, agents, scorers, Docker sandboxes, parallelism, log viewer; recipes become Inspect tasks and agents | RUN, GRD-01, GRD-04 |
| Deterministic graders | pytest, deepdiff, jsonschema, pandas frame compare | Test pass, state diff, schema, SQL result equality | GRD-01, GRD-02, GRD-05 |
| LLM judge | DeepEval (G-Eval, task completion); Ragas for retrieval tasks; scikit-learn for kappa | Rubric grading and calibration | GRD-03 |
| Tracing | OpenTelemetry with OpenInference instrumentation; OTel Collector fanning out to Arize Phoenix and Langfuse, both self-hosted | One span schema; roles split under Observability roles | TRC-01, TRC-02, TRC-05 |
| Results and registries | Postgres, SQLAlchemy 2, Alembic; MLflow for experiments and registry; DVC for fixtures | Effectiveness records, cell aggregates, MLOps records | TRC-03, TRC-04 |
| Analysis | polars, scipy (bootstrap CIs), plotly; Superset on the results DB | Reports and dashboards | ANL |
| Prompts and skills | Jinja2 templates; SKILL.md files with a small in-house loader | Prompt layers, skill loading modes | SKP |
| Service, CLI, console | FastAPI, Typer, Streamlit | Task intake, campaigns, results UI | API |
| Testing | pytest, testcontainers, GitHub Actions | Contract suite per shape, reference solutions in CI | NFR-10 |
| Prompt smoke checks | promptfoo | Quick prompt × model checks outside campaigns | SKP-03 |

**Decisions to note**

- **One agent framework.** CrewAI or AutoGen for some shapes would add a framework confound to every shape comparison.
- **Inspect AI versus an in-house runner.** Inspect brings sandboxes, parallelism, scorers and a log viewer for free; the risk is fitting LangGraph shapes inside its agent model. P0 adds a one-day spike: run one V2 LangGraph harness as an Inspect agent. If it fights back, fall back to an asyncio runner with a Postgres run ledger.
- **Redaction happens client-side.** Langfuse server-side ingestion masking needs an enterprise licence, so GOV-06 redacts spans in the OTel Collector before they reach Phoenix or Langfuse.
- **Not chosen:** LangSmith (closed source), Temporal or Ray (more than one machine needs).

### Framework fidelity: plan vs build

As of Oct 30, 2026, the build uses the planned framework fully in 4 of 20 layers, partly in 6, and not at all in 10 (the campaign runner by decision, ADR 0001). Every layer should be built from its named framework's own components; the table records each gap and how it closes.

| ID | Requirement | Priority |
| --- | --- | --- |
| FWK-01 | Each layer uses the framework named in the table above, through that framework's own components and idioms. A hand-written substitute needs an ADR that says why and which measurement it protects | Must |
| FWK-02 | Wrapping is allowed only at the two measurement points: the model client (calls go through ModelGateway) and the tool callables (calls go through ToolExecutor). Everything between them is the framework's | Must |
| FWK-03 | Framework versions are pinned in uv.lock; where a framework shapes agent behaviour (runner, MCP client adapter, retrieval), its version is part of the run's identity | Must |
| FWK-04 | Every framework-built component stays testable offline with the scripted provider | Must |
| FWK-05 | This table is kept current: each phase closes a gap or records why it stays | Should |

| Layer | Planned | Built | Gap and fix |
| --- | --- | --- | --- |
| Model gateway | LiteLLM Router | `litellm.acompletion` per call inside ModelGateway + QuotaLedger | Route calls through `litellm.Router` (a deployment per provider, rpm/tpm) inside ModelGateway; error classification stays in one place |
| Structured output, tool-call emulation | instructor | Not built (native tool calling only) | instructor for models without native tool calling (SUB-02) |
| Shapes V2 to V5 | LangGraph; V2 prebuilt; Postgres checkpointer; V5 supervisor subgraphs | Hand-built StateGraphs; no checkpointer; V5 via delegate calls | Custom graphs stay as the native runtime (fair shape comparison). LangGraph's prebuilt agent becomes a runner (RUN-02); add the Postgres checkpointer for pause and resume; langgraph-supervisor as a V5 runner |
| MCP servers | FastMCP | MCP SDK `MCPServer` (FastMCP's successor inside the SDK) for fs, sql, search, skills | Matches; 4 of the planned 8 servers exist |
| MCP client in harness | langchain-mcp-adapters | MCP SDK `ClientSession` in the run's own session pool | Framework runners load tools through langchain-mcp-adapters (LangGraph) and LlamaIndex's MCP tool spec, wrapped so calls still go through ToolExecutor |
| Fault injection | FastMCP proxy + middleware | Inside ToolExecutor | Revisit with runners: an MCP middleware proxy hits every client the same way |
| Mock business services | FastAPI apps on Postgres seeds | SQLite and file seeds; remote stand-ins only in tests | FastAPI mock services behind remote MCP servers with test tenants (EXT-07 is built) |
| Retrieval | LlamaIndex, Qdrant, BM25, local reranker | Not built (`retrieval` is refused); search server is find and grep | Build the retrieval dimension with LlamaIndex (Qdrant vector index, BM25 retriever, reranker); never hand-written |
| Campaign runner | Inspect AI | Own asyncio runner; Inspect as an optional outer shell | Justified by ADR 0001 after the spike |
| Deterministic graders | pytest, deepdiff, jsonschema, pandas | Own check kinds (file, SQL, regex, tool-called) | deepdiff for state and JSON diffs, jsonschema for schema checks, pytest for code tasks (b3-07 ships tests) |
| LLM judge | DeepEval (G-Eval), Ragas, scikit-learn kappa | Not built | DeepEval and Ragas as separate graders for answer-quality tasks, kept apart from the outcome score |
| Tracing | OTel + OpenInference; collector to Phoenix and Langfuse | OTel SDK with OpenInference and gen\_ai attributes; collector and Phoenix in compose | Add Langfuse to compose |
| Results and registries | Postgres, SQLAlchemy 2, Alembic; MLflow; DVC | SQLAlchemy (SQLite default, Postgres extra); additive in-code migration; MLflow extra | Alembic migrations; DVC for fixtures |
| Analysis | polars, scipy, plotly; Superset | Standard-library statistics and own bootstrap; markdown report | scipy.stats.bootstrap, polars; Superset dashboards on the results DB |
| Service, CLI, console | FastAPI, Typer, Streamlit | Typer | FastAPI API and UI in PRs #30 to #32 (authoring session) |
| Testing | pytest, testcontainers, GitHub Actions | pytest, GitHub Actions | testcontainers for Postgres and mock services |
| Prompt smoke checks | promptfoo | Not built | promptfoo prompt × model checks outside campaigns |
| Shape V1, prompts and skills, sandbox | Plain Python; Jinja2 + SKILL.md; Docker | Same | Matches |

Sources: [Inspect AI review](https://aicoolies.com/reviews/inspect-ai-review) · [Inspect sandboxing toolkit](https://aisi.gov.uk/blog/the-inspect-sandboxing-toolkit-scalable-and-secure-ai-agent-evaluations) · [Langfuse licence and self-hosting](https://langfuse.com/faq/all/best-braintrustdata-alternatives) · [Langfuse data masking](https://langfuse.com/self-hosting/security/data-masking.md) · [FastMCP](https://github.com/thim81/fastmcp) · [LiteLLM](https://github.com/zhuzhe1983/litellm)

## Model access and MLOps

Models come from several API providers (OpenRouter, Groq, Cerebras, Google AI Studio, Mistral and others), picked per campaign by a declared strategy over cost, quota and token availability. Routing never changes the model under test: each cell's provider is fixed before the campaign starts. MLflow is the MLOps system of record.

### Request budget: OpenRouter free tier as a worked example

| Item | Value |
| --- | --- |
| Free-model limit, no credit purchase | 20 requests/min, 50 requests/day |
| Free-model limit, after a one-time $10 purchase | 20 requests/min, 1,000 requests/day |
| Scope of the limit | Per account, across all keys |
| Paid model variants | No platform-level daily cap; billed per token |
| Model calls per run (estimate) | 5 to 10 |
| Campaign 1 (\~3,600 runs) on free models | 18,000 to 36,000 requests: 18 to 36 days at 1,000/day |
| Full campaign (\~9,000 runs) on free models | 45,000 to 90,000 requests: 45 to 90 days at 1,000/day |

```latex
\text{days} = \max\left(\frac{R}{\sum_p \text{RPD}_p},\ \frac{T}{\sum_p \text{TPD}_p}\right)
```

R is runs × calls per run and T is runs × tokens per run, summed over the providers allocated to that model. RPD and TPD are each provider's daily request and token limits. Adding providers that serve the same model is the main lever for shortening a campaign.

### Provider catalog and allocation

Every provider is a catalog entry; an allocation planner assigns each cell to one provider before the campaign, and the router enforces it.

| Catalog field | Content |
| --- | --- |
| Provider | Id, base URL, auth variable, OpenAI-compatible flag |
| Models served | Provider model id mapped to a canonical model id; one open-weight model can be served by several providers |
| Price | Per million input and output tokens; free-tier flag |
| Limits | Requests/min, requests/day, tokens/min, tokens/day, tokens/month |
| Capabilities | Tool calling, structured output, context length, per model |
| Terms | Free-tier data-use terms (some free tiers require opting into training) |
| Equivalence | Reference provider per canonical model, and the latest calibration result |

| Strategy | Picks | Use when |
| --- | --- | --- |
| Free-first | Free quotas across all providers, then the cheapest paid | Development, smoke runs, small campaigns |
| Cost-first | Lowest total dollars that still finishes by the deadline | Full campaigns on a budget |
| Deadline-first | Highest combined throughput within the dollar cap | A finding is needed by a date |
| Balanced | Weighted cost and duration | Default for scheduled campaigns |
| Live | Per request: cheapest provider with quota left, then lowest latency | API task intake only, never campaigns |

How a campaign is allocated:

1. Estimate requests and tokens per cell from smoke-run averages.
2. Keep providers that serve the canonical model with tool calling and structured output.
3. Drop providers that failed equivalence calibration for that model.
4. Assign cells to providers under the strategy, packing within each provider's per-minute and per-day quotas.
5. Emit a LiteLLM router config: one deployment per allocated provider, limits enforced as hard caps.
6. Preflight: projected days, tokens and dollars per provider; refuse to start if over budget.
7. During the run, a lost quota or removed model re-plans only the remaining cells.

### Model access (MOD)

| ID | Requirement | Priority |
| --- | --- | --- |
| MOD-01 | A provider catalog, kept as a versioned data module, lists every provider: base URL, auth variable, models served mapped to canonical ids, per-token prices, free-tier limits, per-model capabilities, free-tier data-use terms | Must |
| MOD-02 | The canonical model is the experiment variable; the provider is infrastructure, recorded per run, unless a campaign declares provider as an axis | Must |
| MOD-03 | A quota ledger tracks use per provider per minute, day and month from gateway counts and rate-limit headers, reconciled with provider usage endpoints where they exist | Must |
| MOD-04 | Before a campaign, the allocation planner assigns every cell to exactly one provider under the campaign's strategy; within a cell the provider never changes | Must |
| MOD-05 | Preflight projects requests, tokens, days and dollars per provider and refuses to start a campaign that cannot finish within its budget and deadline | Must |
| MOD-06 | A provider serves a canonical model in campaigns only after equivalence calibration: ≥ 20 tasks on it and on the reference provider, with a pass-rate gap above a set threshold blocking it | Must |
| MOD-07 | On quota exhaustion or model removal, remaining cells are re-planned onto equivalent providers; with none available they are marked incomplete, never moved to another model | Must |
| MOD-08 | 429 and quota errors are classified (rate limit, quota exhausted, upstream capacity), retried with backoff, and never counted as harness failures | Must |
| MOD-09 | Every run stores canonical model, provider, provider model id, per-token price at run time, and tokens in and out | Must |
| MOD-10 | The planner's output is emitted as a LiteLLM router config: one deployment per allocated provider, rpm and tpm enforced as hard limits | Must |
| MOD-11 | Live tasks from the API route per request: cheapest provider with quota left, then lowest latency | Should |
| MOD-12 | A weekly catalog refresh pulls model lists and prices from provider APIs where available; changes are diffed and reviewed before entering the catalog | Should |

### MLOps (MLO)

| ID | Requirement | Priority |
| --- | --- | --- |
| MLO-01 | MLflow (Postgres backend, local artifact store) is the experiment record: one MLflow experiment per campaign, one run per cell, recipe fields as params, cell aggregates as metrics, report and resolved recipe as artifacts | Must |
| MLO-02 | Recipes are registered in the MLflow Model Registry as versions; per task class the selector's pick carries alias champion and the runner-up challenger | Must |
| MLO-03 | Prompt templates are mirrored from the repo's source modules into the MLflow Prompt Registry; the repo stays the source of truth | Must |
| MLO-04 | Task fixtures, seeds and recorded corpora are versioned with DVC; a task version includes its data hash | Must |
| MLO-05 | GitHub Actions runs contract tests, reference solutions and a free-model smoke campaign on every merge; full campaigns run on schedule | Must |
| MLO-06 | Promotion gate: a recipe becomes champion only if it beats the current champion on the test split with non-overlapping CIs and no new critical violations; promotions are logged with their evidence | Must |
| MLO-07 | Rollback means moving the alias back; nothing is redeployed | Must |
| MLO-08 | A weekly re-run of champions on a fixed slice flags a composite drop above 5% or a rotated-out model | Should |
| MLO-09 | Spend per campaign, model and recipe is reported from the run ledger | Should |

### Observability roles

| Tool | Role | Licence |
| --- | --- | --- |
| OpenTelemetry Collector | Single export pipeline: redaction, then fan-out to Phoenix and Langfuse | Apache 2.0 |
| Arize Phoenix (self-hosted) | OpenInference span analysis, datasets built from failing spans, experiments comparing recipes on a dataset | Elastic License 2.0, free to self-host |
| Langfuse (self-hosted) | Browsing runs and sessions, attaching scores, human annotation for judge calibration | MIT |
| MLflow | Campaigns, cell metrics, recipe and prompt versions, champion and challenger aliases | Apache 2.0 |

MLflow's own tracing stays off, so traces exist once in Phoenix and once in Langfuse, never three times. If two trace UIs prove redundant after P2, drop one; the collector makes that a config change.

Sources: [OpenRouter rate limits](https://openrouter.ai/docs/api/reference/limits) · [OpenRouter free-tier summary](https://benchlm.ai/md/free-tier/openrouter.md) · [OpenRouter free-variant upgrade path](https://www.developersdigest.tech/models/free-api/openrouter) · [LiteLLM routing strategies](https://litellm.vercel.app/docs/proxy/load_balancing.md) · [LiteLLM router](https://litellm.vercel.app/docs/routing.md) · [Free LLM API tiers, 2026 snapshot](https://ianlpaterson.com/blog/free-llm-api-2026/) · [Free LLM provider list](https://github.com/ShaikhWarsi/free-ai-tools) · [Phoenix licence](https://www.arize.com/docs/phoenix/self-hosting/license.md) · [Arize Phoenix](https://pypi.org/project/arize-phoenix/20.8.0/) · [MLflow 3](https://mlflow.org/docs/latest/genai/mlflow-3)

## Phases

Six phases, three campaigns; every phase exits on runs and a measured result, never on a finished spec.

| Phase | Build | Scale at exit | Exit criteria | Requirements |
| --- | --- | --- | --- | --- |
| P0 Contracts and substrate | Schemas, provider catalog, multi-provider gateway, tool executor, budgets, OTel Collector to Phoenix and Langfuse, MLflow tracking, structured output | 3 models, 2 MCP servers, 5 tasks | One task runs under V2 on all 3 models; trace stored and graded | SHP-01, SUB-01 to 08, TRC-01 to 03, RCP-01, 02, 05, 06, MOD-01 to 03, 09, MLO-01 |
| P1 Environment at scale | All MCP servers, fault proxy, state diff, task registry, perturbation generator, deterministic graders | 8 servers, 40+ tools, 60 tasks, 120+ perturbations | All 60 reference solutions pass their graders | ENV, TSK, MCP-01 to 03, 05, 08, 10, GRD-01, 02, 04, 05, MLO-04, KNW-01 |
| P2 Shapes and campaign 1 | V1 to V4, provisioner, recipe overlays and generator, allocation planner, runner, results store, reports | \~3,600 runs: 60 tasks × 4 shapes × 3 models × 5 seeds, pruned | Shape × model report with CIs and at least one significant crossover by step depth | PRV, SHP-02 to 04, 06, RCP-03, 04, RUN, TRC-04, ANL-01, 02, MOD-04 to 08, 10 |
| P3 Axes and campaign 2 | Skill and prompt registries, memory stores and retrieval, tool-slice and config overlays, governance, LLM-judge calibration | 40+ recipes, \~8,000 runs incl. perturbations | ≥ 3 significant one-axis effects; judge kappa ≥ 0.7 | SKP, MEM, KNW-02 to 05, MCP-04, 06, 07, 09, 11, GOV, GRD-03, ANL-03, 04 |
| P4 Selector and campaign 3 | V5, automated profiler, selector with exploration | Held-out set, all eligible recipes | Selector meets target on held-out; profiler ≥ 80% agreement | SHP-05, PRF-04, 05, SEL |
| P5 Service and continuous loop | FastAPI service, console, recipe export, MLflow promotion gate and rollback | Live task intake | A task submitted through the API is profiled, provisioned, run and recorded with no manual step; a recipe change dropping composite > 5% fails the gate | API, ANL-03, MOD-11, 12, MLO-02, 05 to 09 |

## Version horizon

v1 is the committed scope of this document; v2 to v4 set direction and get their own requirements only when the version before them exits. Each version answers a bigger question than the last: measure, then learn, then deploy, then improve itself.

&#91;embedded content: version horizon · v1 to v4\]

Read left to right: each column lists what the version adds, and its exit line is the evidence needed before the next version starts.

| Version | Question it answers | Scale | Stays out |
| --- | --- | --- | --- |
| v1 · Measured factory | Which harness fits which task, and can the factory pick it? | 60 tasks · 5 shapes · 3 models · about 9,000 runs per campaign | Cross-run memory, live systems, interactive use, automatic recipe search |
| v2 · Learning factory | Can the factory improve agents and recipes from its own results? | 150+ tasks including ordered sequences · search over every recipe axis | Live systems, real human approvers |
| v3 · Real-world use | Do factory-chosen harnesses hold up on real work with people in the loop? | 3 to 5 pilot automations · several users | Recipes the factory invents itself |
| v4 · Self-improving | Can the factory design new harnesses, not only choose among them? | Fleets of agents per task class, evaluated continuously | Open |

What changes between versions:

- **v1 to v2:** runs stop being fully independent. Carry-over campaigns keep memory across an ordered task sequence, recipes are generated by search instead of written by hand, and the selector updates as results arrive.
- **v2 to v3:** mocks give way to sandboxed real systems, scripted approvers to people, and campaigns gain live shadow runs beside real work.
- **v3 to v4:** the factory proposes its own recipes and shapes, and tests them with the same campaigns and gates it uses today.

## Improvement loop (IMP)

The improvement loop is how v2 generates recipes by search instead of by hand: per task it proposes recipe edits, probes them, composes a successor recipe, and files what it learned in a recipe library the next task reads. It adapts [Harness²](https://arxiv.org/abs/2610.14224) (Xu et al., Google Cloud AI Research, Oct 2026), which improves harnesses without ground-truth labels by comparing how edited harnesses behave against a reference run.

The factory keeps its graders. They score every run after the loop finishes, to evaluate the loop; they never feed it. That split is what makes the loop usable on real tasks later, where no grader exists.

Priorities in this section use **Must (v2)** and **Should (v2)**: nothing here is in v1 scope, which excludes automatic recipe search. Start at budget k=1, one contrastive step per task; in the paper that step alone beat the base harness in every configuration, by up to 11.5 points on WorkBuddy-Bench, 4.5 on JobBench and 11.8 on LAB.

&#91;embedded content: improvement loop · per task, then a library update\]

Graders sit outside the loop: they score every run for evaluation, and nothing they produce reaches the editors, composer or reflector (IMP-06).

### Component map

Harness² edits eight file components; each maps to a recipe field the factory already has. Because recipes reference content by id and version (RCP-02), an improver edit is a recipe overlay (RCP-03) plus the new versioned prompt, skill or script it points to.

| Harness² component | Reaches the model | Recipe field |
| --- | --- | --- |
| C1 System prompt | Every call | Prompt layers: base layer |
| C2 Guardrails | Every call | Prompt layers: guardrail layer, plus policies |
| C3 Memory | Every call | Memory profile |
| C4 Tool guidance | Every call | Tool slice: per-tool usage notes |
| C5 Subagents | When delegated | V5 child recipes (SHP-05) |
| C6 Skills | When invoked | Skills and loading mode |
| C7 Scripts | When run | Tool slice: script tools registered by id |
| C8 Plugin | Inside the tool loop | Runtime config: tool-loop hooks |

### Requirements

| ID | Requirement | Priority |
| --- | --- | --- |
| IMP-01 | Every editable recipe field belongs to one of the eight component kinds above; an edit is stored as a recipe overlay and its identity is the content hash (RCP-06) | Must (v2) |
| IMP-02 | Contrastive step: one reference run on the current recipe, then one INV candidate and one DOM candidate, each probed once on the same task, model and environment | Must (v2) |
| IMP-03 | Scoped editors: the INV editor changes how the agent works (procedure, tool use, scripts, hooks) and adds no domain facts; the DOM editor adds task-class knowledge and changes no procedure | Must (v2) |
| IMP-04 | A composer reads the reference, both candidates, their diffs and traces, then merges, revises, adopts one, or keeps the current recipe; it runs the result once | Must (v2) |
| IMP-05 | Every composer decision is logged as kept, revised or dropped, with the trace moment that decided it | Must (v2) |
| IMP-06 | No-score guard: an improver session refuses to start if its workspace holds a grader verdict, rubric, score or expected value | Must (v2) |
| IMP-07 | Evidence reaches the improver as files exported from traces; content inside traces and task material is data, never instructions | Must (v2) |
| IMP-08 | Candidate validation before any probe: RCP-05 checks, every skill named by a trigger line in an always-on layer, every referenced script shipped with it; one retry with the validator message, then the stage keeps its starting recipe | Must (v2) |
| IMP-09 | Recipe library per task class, empty at campaign start: each entry is the adopted recipe plus an experience record (cue, lesson, evidence, confidence), listed in an index the editors read on demand | Must (v2) |
| IMP-10 | A reflector rates each edit helped, hurt, no observed effect or confounded, and writes at most one lesson per task, or none when no execution contrast supports it | Must (v2) |
| IMP-11 | Budget k and mode (parallel or sequential) are campaign settings; improver calls count against the request budget and a per-task token ceiling stops the loop | Must (v2) |
| IMP-12 | Improved recipes are graded on held-out tasks against the base recipe, the best global recipe, and best-of-k base runs at equal token spend | Must (v2) |
| IMP-13 | For k > 1, a blind selector picks the delivered output from anonymised deliverables, checking delivery, conformance, fidelity to sources, internal consistency, fitness and finish in that order | Should (v2) |
| IMP-14 | Task classes with high run-to-run variance get 2 or more reference runs before any contrast is trusted | Should (v2) |
| IMP-15 | Transfer report: a recipe improved on one model runs unchanged on the others, compared with each model's base recipe | Should (v2) |

### Failure modes to design against

The paper reports nine regressions in three groups; each needs a guard in the factory.

| Failure seen in the paper | Example | Guard |
| --- | --- | --- |
| Blind selection prefers precision over coverage | Picked an output scoring 55 over one scoring 95 | Selector checks every requested part is present before judging quality (IMP-13) |
| A wrong premise spreads to every candidate | An editor rule on key casing capped all four candidates at 42 | DOM edits cite the trace or source line behind each fact; the composer drops uncited facts |
| An editor-written checker shares the editor's mistake | A checker script passed three times on a wrong cache assumption | Probes are graded afterwards by factory graders only; editor checkers never count as evidence of correctness |
| One draw on a noisy task decides the outcome | Two of five base runs failed the same all-or-nothing item | Repeated reference runs on high-variance classes (IMP-14) |

### Cost

Improvement multiplies runs. At k=1 each task costs one reference run, two probes, one successor run and four improver sessions; at k=5 in parallel mode it is about five times that. The paper's total compute per task, execution plus improvement, ran from about 1M to 10M tokens on WorkBuddy-Bench and 10M to 100M on JobBench across k=1 to 5. Budget the loop inside the request budget per provider, not beside it.

### Open questions

- [ ] Editor model: the paper used Claude Opus 4.8 as editor, but using the executor model as its own editor still improved all five models tested. Which model edits in the factory, and at what cost?
- [ ] Where the loop first runs: inside carry-over campaigns (P6), or as its own campaign mode
- [ ] Library growth: when to merge overlapping entries and retire stale lessons

Sources: [Harness² paper](https://arxiv.org/abs/2610.14224) · [Harness² code](https://github.com/google-research/harness2)

## Plan and direction

Six gated phases finish v1, estimated at 18 to 24 weeks for one engineer, and P6 and P7 begin v2 and v3. Each phase starts when the previous gate passes; the table above lists what each v1 phase builds, and this plan says what to do in order and where to go next.

&#91;embedded content: roadmap · 8 phases, a gate after each\]

Each phase answers one question; its gate is the evidence that answers it, and the fallback says what to do when the evidence is not there.

### Working rules

- A phase ends on runs and a written finding, never on a finished spec.
- Task authoring continues after P1; new tasks join the dev split, never the held-out split.
- Build only what the current gate needs; new fields enter the data modules when a run needs them.

### P0 · Contracts and substrate

- [ ] Single-source schemas: Task, HarnessRecipe, RunResult, RunKey, EffectivenessRecord
- [ ] Provider catalog with 3 models on at least 2 providers; LiteLLM gateway and quota ledger
- [ ] V2 tool loop on LangGraph; MCP session pool; fs and sql servers
- [ ] OTel collector to Phoenix and Langfuse; MLflow tracking server
- [ ] One-day spike: V2 as an Inspect AI agent; choose the runner
- [ ] 5 seed tasks with reference solutions and deterministic graders

Unlocks: contracts every later phase builds on without changing them.

### P1 · Environment at scale

- [ ] Remaining 6 MCP servers, mock services, conformance suite in CI
- [ ] Fault proxy, state inspector, sandbox reset and canary test
- [ ] 60 tasks (10 per domain) with reference solutions, seeds in DVC, authored profiles
- [ ] Perturbation generator: at least 2 variants per task
- [ ] Deterministic graders: state diff, tests, SQL, trajectory, safety
- [ ] 2 corpora recorded and indexed; held-out split locked

Unlocks: a task suite whose results can be trusted.

### P2 · Shapes and campaign 1

- [ ] V1, V3 and V4 shapes; one contract test suite for all shapes
- [ ] Provisioner, recipe overlays, generator and validator
- [ ] Allocation planner, budget preflight, router config emitter
- [ ] Campaign runner with resume; results store; reports with intervals
- [ ] Smoke campaign under 1 hour, then campaign 1 (about 3,600 runs)
- [ ] Written finding: shape × model by step depth

Unlocks: knowing which shapes are worth varying in P3.

### P3 · Axes and campaign 2

- [ ] Skill and prompt registries; skill loading modes
- [ ] Memory stores, write policy and compaction; retrieval, reranker, evidence store, grounding grader
- [ ] Governance: permissions, approval gate, scripted approver, guardrails, redaction
- [ ] MCP handlers for elicitation, sampling and resources; MCP vs native binding
- [ ] Judge calibration on 100 hand scores; provider equivalence calibration
- [ ] Campaign 2 (about 8,000 runs) as one-axis ablations; a written finding per axis
- [ ] Tool-set axis with recorded tool offers and rule-granted tools (EXT-01 to EXT-04); delegate tool with its limits as an axis (EXT-09, EXT-11, EXT-12)

Unlocks: a measured list of which choices matter, per task class.

### P4 · Selector and campaign 3

- [ ] V5 orchestrator with delegation grants
- [ ] LLM profiler and its agreement check against authored profiles
- [ ] Selector: hard rules, ranking, run-two on close calls, cold-start defaults
- [ ] Campaign 3 on held-out tasks against best-global, rules-only and hand-picked baselines

Unlocks: automatic recipe choice for new tasks.

### P5 · Service and continuous loop

- [ ] FastAPI intake, live router, sessions, console
- [ ] Recipe registry with champion and challenger aliases; promotion gate; rollback
- [ ] Drift watch; recipe export to agent YAML and plugin bundles
- [ ] Scheduled campaigns in CI
- [ ] External MCP servers and external agents: network allowance, test tenants, record and replay, scoped credentials (EXT-05 to EXT-08, EXT-10, EXT-13)

Unlocks: a service other people can submit tasks to.

### P6 · Carry-over memory and learned procedures (begins v2)

- [ ] Carry-over campaign mode: ordered task sequences within one task class, memory kept across the sequence
- [ ] Learning-curve report against a fresh-memory baseline
- [ ] Procedure lifecycle: observed, candidate, validated after 2 or more passing runs, promoted to a skill by a person

Unlocks: evidence on whether cross-run memory is worth its cost in reproducibility.

### P7 · Interactive features and work pilots (begins v3)

- [ ] Streaming, interrupt and cancel, ask-user turns, notifications
- [ ] Pilot real automations from work, with mocks replaced by sandboxed real systems
- [ ] Go or no-go per pilot from campaign results

Unlocks: harnesses chosen by evidence for real work.

## Out of scope, assumptions and risks

The biggest risk is a task suite that cannot tell harnesses apart; P2 checks for it before more is built.

**Out of scope for v1:** model training or fine-tuning; acting on live production systems; an end-user UI; live web browsing in campaigns; a multi-machine distributed runner; real humans as approvers in campaigns (a scripted approver stands in).

**Assumptions:** one machine runs the factory, sandboxes and the observability stack; API providers serve all models; a run averages 5 to 10 model calls.

| Risk | Impact | Mitigation |
| --- | --- | --- |
| Task suite does not discriminate between shapes | Every result is a tie; selector learns nothing | Depth-mix target; at P2 exit, if no task class separates shapes, add unknown-path tasks before P3 |
| Free models fail tool calling broadly | All shapes ≥ 2 score near zero | Tool-calling emulation (SUB-02); keep single-step tasks; report by step depth |
| Grader noise | Selector ranks on noise | Deterministic graders first; judge only after the kappa gate |
| Combinatorial explosion | Campaigns never finish | Rule pruning; one-axis ablations instead of full grids; smoke campaigns first |
| Environment work swallows the schedule | P1 never ends | Thin mocks and recorded corpora; cap at 8 servers |
| Specifying ahead of running | Catalog grows, nothing runs | Phase exits are runs and findings; new fields only when a run needs them |
| Free models rotate out or hit daily limits | A campaign stalls or loses a model midway | Provider fixed per cell; re-planning onto equivalent providers (MOD-07); budget preflight (MOD-05); paid variant of the same model as last resort |

**Open questions**

- [ ] Free-only campaigns, or a capped budget for paid variants (see Request budget)
- [ ] Which 3 models and which providers enter the catalog first, chosen for tool calling, structured output and free quota
- [ ] Should real automations from work seed some task classes, with mocks in place of the live systems?
