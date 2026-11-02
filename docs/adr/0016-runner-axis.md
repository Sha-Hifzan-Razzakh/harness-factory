# ADR 0016: The runner axis: native vs framework runners

Status: proposed · 2026-11-02 · Roadmap: package #53 (requirements RUN-01 to RUN-09, FWK-01 to FWK-04)

## Context

Every score so far comes from the native runtime: hand-built LangGraph graphs whose model turns, tool calls,
retries and prompts are the factory's own (`shapes/common.py`). Teams usually deploy on a framework's ready-made
agent instead, which adds its own scaffolding: tool formatting, call parsing, error handling, history. Whether a
recipe's score carries over there had not been measured (`docs/design/test-matrix.md`, §1–2).

## Decision

1. **A recipe field `runtime`:** `native` (the default), `langgraph-prebuilt` or `llamaindex-agent`. It is
   optional and listed in `_HASH_OMIT_WHEN_NONE`, and `native` written out is treated as unset. So every existing
   recipe hash stays the same (`v2-base` is still `79abd03818943f65`). The runtime changes only what executes the
   run: prompts, tools, permissions, budget and grading are the recipe's and the task's, as for native runs
   (RUN-09).

2. **Only the V2 tool loop.** A framework's ready-made agent is a tool-calling loop, so a runtime other than native
   is refused (`hfx validate`) for:
   - other variants (`runtime_shape`): V1's single call, V3 stages and the V4 planner would be the factory's control
     flow running inside the framework;
   - `delegation` and `config.recover_malformed` (`runtime_harness_tools`): these are harness-side additions of the
     native loop, and a framework keeps its own error handling. For the same reason a recipe may not delegate to
     a runner recipe: delegate helpers run on the native runtime;
   - a missing framework package (`runner_installed`). This is checked when a run is provisioned, not by
     `hfx validate`: it is the environment's problem, not the recipe's. For example, the factory image is built
     without the extras.

   Matrix recipes: `v2-langgraph-prebuilt` and `v2-llamaindex-agent` (`base: v2-base` plus `runtime`).

3. **The framework's own agent (RUN-02, FWK-01).** Each runner is built from that framework's components, left at
   their defaults. The factory writes no agent loop for either one.

   | | `langgraph-prebuilt` | `llamaindex-agent` |
   |---|---|---|
   | Agent | `langchain.agents.create_agent` (LangChain 1.4.7 on LangGraph 1.2.18): a compiled `StateGraph` with a `model` node and a `tools` node, `langgraph.prebuilt.ToolNode` (langgraph-prebuilt 1.1.1). It replaces `langgraph.prebuilt.create_react_agent`, which LangGraph v1 deprecates. Defaults: no middleware, recursion limit 9 999 | `llama_index.core.agent.workflow.FunctionAgent` (llama-index-core 0.14.25, llama-index-workflows 2.26.1). Defaults: streaming on, parallel tool calls allowed, `ChatMemoryBuffer` sized from the model's context window, 20 iterations then a forced stop, no workflow timeout |
   | MCP tools | `langchain.mcp.as_langchain_tool` (beta namespace in LangChain 1.4). It replaces langchain-mcp-adapters, whose releases need `mcp<2` (0.3.2 pins it; 0.3.1 imports `mcp.server.fastmcp`, which is gone in MCP 2.x), so it can't be installed next to the factory's MCP 2.3 | `llama_index.tools.mcp.McpToolSpec` (llama-index-tools-mcp 0.6.0) |
   | Model client | `GatewayChatModel(BaseChatModel)` | `GatewayLLM(FunctionCallingLLM)` |

4. **Exactly two measurement points (RUN-03, FWK-02).** Shared code is in `runners/base.py`.
   - **(a) The model client.** Every call the framework makes to its model goes to `RunnerContext.model`, which is
     `shapes.common.model_turn`: the same turn the native shapes take. That means a BudgetGuard check first, then
     `ModelGateway.complete` (quota slot, timeout, retry of transient provider errors, `classify_provider_error`,
     tokens and cost, the `model_call` span), then step and token accounting.
     - The LangChain class formats tools with `convert_to_openai_tool`, converts requests with
       `convert_to_openai_messages(text_format="string")`, and parses replies with
       `parse_tool_call`/`make_invalid_tool_call`, as langchain-openai's chat model does.
     - The LlamaIndex class formats tools with `ToolMetadata.to_openai_tool` and converts messages the way
       llama-index-llms-openai does: tool calls in `additional_kwargs["tool_calls"]`, tool results by
       `tool_call_id`. It streams the gateway's single answer as one chunk.
   - **(b) The tool callables.** The framework's MCP adapter is given `ExecutorSession`, an MCP session facade.
     - `list_tools` returns, as MCP `Tool`s, exactly the tools the native runtime offers this run: the task
       allowance on the recipe's servers, with the servers' own descriptions and schemas and the names
       `server__tool`.
     - `call_tool` runs a budget check, then `ToolExecutor.execute` (allowance, permission policy, injected faults,
       violations, the `tool_call` span). It answers with an MCP `CallToolResult`, the type a `ClientSession`
       returns, so the adapter's own result conversion produces what the model sees.

     The adapter builds the tool objects (names, schemas, result formatting) and never talks to an MCP server
     itself. This uses the client surface each adapter documents or reads:
     - llama-index-tools-mcp asks for a duck-typed `ClientSession` with `list_tools` and `call_tool`;
     - `as_langchain_tool` reads a FastMCP client's context manager, `call_tool(..., raise_on_error=False)` and
       `server_info`, and accepts the MCP SDK's `CallToolResult`.

     LangChain's `MCPAdapter` wrapper isn't used: it only constructs a FastMCP client and calls the same
     `as_langchain_tool`.
   - Everything between the two points belongs to the framework: the loop, its prompts and scaffolding, tool
     formatting, call parsing, retries and history.

5. **What each framework adds** (recorded per RUN-09, not tuned):
   - **Prompt text: none from either one.** The recipe's system message is `system_prompt` (LangGraph: sent first,
     unchanged; LlamaIndex: prepended to every call). Its task message is the one input message.
   - **Tool schemas.** LangChain sends the MCP schema, with object arguments that have no properties marked
     `additionalProperties: true`. LlamaIndex sends the JSON schema of a pydantic model it builds from the MCP
     schema (a `title` and an empty `description` added to every field).
   - **Tool results.** LangChain sends the result's text blocks joined; an error result becomes a `ToolMessage`
     with status `error` and the server's text. LlamaIndex sends `str()` of the `CallToolResult` object
     (`meta=None content=[TextContent(type='text', text='...')] ... is_error=False`), which costs tokens on every
     call and puts the error flag inside that text. Both are framework defaults, so both are measured.
   - **Invalid calls.**
     - LangChain: a call to a tool that isn't offered gets a `ToolNode` error message, and malformed arguments
       become an `invalid_tool_call` that `create_agent` answers with an error `ToolMessage` before the next model
       call.
     - LlamaIndex: an exception in a tool becomes an error output.
   - **History.** LangGraph keeps everything. LlamaIndex trims to 75% of the model's context window
     (`caps.context_len` in the provider catalog).
   - **Iteration limits.** LangGraph's recursion limit is 9 999. LlamaIndex allows 20 iterations, below the step
     budget of some recipes.

6. **Identity (RUN-04, FWK-03).** When `runtime` is not native, the recipe hash also includes the runtime and the
   installed version of each framework package that shapes the agent:
   - `langgraph-prebuilt`: langchain, langchain-core, langgraph, langgraph-prebuilt;
   - `llamaindex-agent`: llama-index-core, llama-index-workflows, llama-index-tools-mcp.

   The list is in `runners/identity.py`; the versions are locked in `uv.lock`. A framework upgrade changes the
   runner recipe's hash, so it starts new cells and new RunKeys. Native recipes never read these versions. The
   runner is part of the cell through the recipe hash (task class × recipe × model × provider × env).

7. **How runs end** follows `shapes.common.finish`, the same mapping as native:
   - **Final answer** (a reply without tool calls): DONE, then graded.
   - **Budget limit** (checked before every model call and every tool call): the wrapper records the stop and raises
     `RunnerStop` into the framework. Whatever the framework does with the exception (LlamaIndex catches tool
     exceptions), the first recorded stop decides. The status is BUDGET_EXCEEDED, then graded with outcome × 0.5.
   - **Framework recursion or iteration limit:** a harness stop, recorded as `budget:framework recursion_limit` or
     `budget:framework max_iterations`.
   - **Provider infrastructure failure:** INCOMPLETE, via the gateway's classification.
   - **Malformed output** (the provider rejects a reply as malformed): `malformed_output`, as for native runs.
   - **Any other exception:** ERROR, through `run_one`.
   - The calls the framework made into each wrapper are recorded in `snapshot.runner`.

8. **Transfer check (RUN-05).** `results/transfer.py` and `hfx transfer run|list` (`cli/transfer.py`).
   - **The recipes.** The runner recipe and its native counterpart (its `base` by default) must differ only in
     `runtime`.
   - **Pairs** are runs with the same task version and task hash, seed, model, provider, served model id and
     `tools_hash`, clean and scored.
   - **The verdict** is the paired E0 difference judged with the ADR 0010 rule: `results.report.paired_delta`, then
     `results.calibration.judge`, factored out of `calibrate` without changing it. Below `min_pairs`:
     inconclusive; interval within ±margin: equivalent; interval excluding 0: different.
   - **Frozen** in a new `transfers` table (additive). The record lists the framework versions and the median token
     and model-call ratios.

9. **Offline (RUN-08, FWK-04).** The scripted `PlanBackend` drives both runners through the gateway, unchanged.
   CI installs both extras with `dev` and gates both matrix recipes on the visible tasks.

## Consequences

- The factory can now measure what a team would deploy. Offline, with `scripted/replay` on the 20 visible tasks,
  both runners pass 20/20 with the same model and tool calls as `v2-base`, and the transfer check comes out
  `equivalent` (20 pairs, all ties). Median tokens against `v2-base`: ×1.02 for LangGraph and ×1.22 for
  LlamaIndex, because of the repr tool results.
- **Limits:**
  - A call that the framework refuses before reaching a tool (`ToolNode`'s "not a valid tool") never reaches
    ToolExecutor. It has no span, so Q doesn't count it. Real providers usually reject such a call first (MODEL_PARSE,
    counted).
  - Retries inside the gateway (transient provider errors) are the factory's, as for native runs. Retry middleware is
    off in both frameworks by default.
  - `langchain.mcp` is a beta namespace, and its API may change; its version is part of the identity through
    `langchain`.
  - The runner report extras of RUN-07 (hidden-prompt tokens, extra calls, formatting-caused invalid calls, wall-time
    overhead) are not built. Token and call ratios are in the transfer record.
  - Runners are not yet a selector axis beyond their recipe (RUN-06).
- Other frameworks (#54–#56) add a module in `runners/`, a `RUNNER_PACKAGES` entry, an extra, and the same offline
  tests.

## Addendum: more runners (#56)

Three more runners on the same rules: one module each in `runners/`, a `RUNNER_PACKAGES` entry, an optional extra
(included in `dev`), a matrix recipe `base: v2-base` + `runtime`, and the offline checks in
`tests/test_runners_more.py`. All three pass 20/20 offline; each transfer check against `v2-base` is `equivalent`
(20 ties).

| | `langgraph-supervisor` | `pydantic-ai` | `openai-agents` |
|---|---|---|---|
| Agent | `langgraph_supervisor.create_supervisor` (0.0.31) over one worker built with `langchain.agents.create_agent`, named `worker`. The library builds the supervisor with LangGraph's prebuilt `create_react_agent`. Defaults: `output_mode="last_message"`, handoff messages on, no parallel handoffs | `pydantic_ai.Agent` (pydantic-ai-slim 2.64.0 on pydantic-graph 2.64.0), `Agent.run`. Defaults: `retries=1` (tools and output), `UsageLimits(request_limit=50)` | `agents.Agent` run by `agents.Runner.run` (openai-agents 0.20.0). Defaults: `max_turns=10`, no handoffs, guardrails or model retries |
| MCP tools | the worker's, through `langchain.mcp`, as `langgraph-prebuilt` | `pydantic_ai.mcp.MCPToolset` | the SDK's `agents.mcp.MCPServer` interface, through its `MCPUtil` |
| Model client | `GatewayChatModel`, one per agent, one `RunnerContext` | `GatewayModel(pydantic_ai.models.Model)` | `GatewayModel(agents.models.interface.Model)` |
| Identity packages | langgraph-supervisor, langchain, langchain-core, langgraph, langgraph-prebuilt | pydantic-ai-slim, pydantic-graph | openai-agents |

1. **The supervisor runs V2 recipes, not V5.** A V5 recipe's planner prompt, worker prompt, delegation child and
   replan rule are the native orchestrator's control flow; running them inside the supervisor would be the factory's
   loop under the framework's name (RUN-02). The supervisor pattern is the framework's own way of dividing the work,
   so it gets what a V2 recipe gives: task, system prompt, tools, permissions and budget. `runtime_shape` already
   refuses it elsewhere.
   - Both agents get the recipe's system message: it is the harness's one system message. It names the run's tools,
     which only the worker has. A real model may try to call them from the supervisor (the provider rejects that as
     MODEL_PARSE); recorded, not tuned.
   - The handoff tool (`transfer_to_worker`, "Ask agent 'worker' for help") is the framework's control flow: it moves
     control between graph nodes and touches nothing. So it is not a ToolExecutor call: no span, not counted by
     `max_tool_calls`. The model call that asks for it is a gateway call, counted by `max_steps`.
   - What it adds: two model calls per run (the handoff and the supervisor's closing answer after the worker's),
     the handoff messages ("Successfully transferred to worker", "Transferring back to supervisor", ...), and agent
     names on messages. Offline: calls ×1.67, tokens ×1.69 against `v2-base`.
   - The scripted `PlanBackend` gains one rule: offered a `transfer_to_*` tool and none of the next step's tool, it
     hands off while plan steps remain. No native shape offers such a tool, so native runs are unchanged.
2. **Pydantic AI: the toolset's client is replaced.** `MCPToolset` wraps a FastMCP `Client` and has no session
   parameter; FastMCP's client runs the protocol itself (handshake, protocol era, result validation), which a fake
   session can't answer reliably. The runner builds the toolset with a transport that never connects, then sets its
   `client` to `ExecutorClient`: the client surface the toolset's own code uses (context manager,
   `initialize_result`/`server_capabilities`, `list_tools`, `call_tool(..., raise_on_error=)`), over
   `ExecutorSession`. Replacing `client` is what the toolset does itself for a per-run copy (`for_run`). The tool
   definitions, argument validation against the schema, result mapping (structured content when every part is text)
   and error handling (`ModelRetry`) are the toolset's.
   - The model class converts messages the way Pydantic AI's `OpenAIChatModel` maps Chat Completions, for the parts
     a text agent with function tools produces; any other part raises (an ERROR, not a silent mismatch).
     `OpenAIChatModel` itself requires `openai>=3.31`, which LiteLLM (`openai<3`) excludes, so it is mirrored.
   - Its model profile is `LiteLLMProvider.model_profile` of the gateway's LiteLLM model string, what a team calling
     the same model through LiteLLM with Pydantic AI gets. It decides the tool-schema transformer
     (`OpenAIJsonSchemaTransformer`, which adds `additionalProperties: false`) and whether
     `Model.prepare_messages` adds a system message explaining `<system>` tags (about 60 tokens per call) where the
     profile doesn't say the API takes mid-conversation system prompts: not for OpenAI-family or unknown names, but
     for `groq/...` names, whose Groq family profile leaves it unset.
   - What it adds: a tool error, a refusal or invalid arguments come back as a retry prompt ("... Fix the errors and
     try again."); a second failure of the same tool, or a second unusable final answer, ends the run
     (`budget:framework max_retries`); the request limit is `budget:framework request_limit`. Offline tokens ×0.98
     (compact JSON results).
   - Arguments that fail the tool's schema are refused by the toolset before any call: like `ToolNode`'s refusals,
     they never reach ToolExecutor, so Q doesn't count them.
   - The first-run console banner is switched off (`pydantic_ai.BANNER_ENABLED`); it changes nothing the model sees.
3. **OpenAI Agents SDK: one changed default, tracing.** The SDK exports every run's trace to OpenAI's backend by
   default, which would send run data out of the sandbox. Each run gets `RunConfig(tracing_disabled=True)` (per run;
   the process-wide `set_tracing_disabled` would be module state). The model class uses the SDK's own Chat
   Completions `Converter` as its `OpenAIChatCompletionsModel` does. What it adds: tool results sent as a list of
   text parts, `strict: false` on every tool, `properties: {}` where a schema has none, "An error occurred while
   running the tool. Please try again. Error: ..." for a tool exception (a refused or failed ToolExecutor call is an
   error result, sent unchanged), and the turn limit (`budget:framework max_turns`). Offline tokens ×1.02.
   - openai-agents is capped at 0.20.x: 0.21+ requires `openai>=3`, and LiteLLM 1.106 requires `openai<3`.
     Resolving it moved `websockets` (used by langgraph-sdk and langsmith, not by the factory) from 17.3 to 16.1.1;
     no direct dependency changed.
4. **CrewAI is left out.** Every CrewAI release since 1.7 pins `mcp` below 2 (1.15.34, 1 Nov 2026: `mcp>=1.30,<2`)
   and `pydantic<2.13` (also in `crewai-core`; `crewai-tools[mcp]` pins `mcp<2` too); the factory locks MCP 2.3.0
   and pydantic 2.14.1. The only releases without those pins (≤1.6.1, Nov 2025) depend on `instructor`, which pins
   `rich<15`, so resolving them downgrades the factory's rich 15.0.0 to 14.3.4, and they were written against MCP
   1.x. No core dependency is downgraded for a runner, so CrewAI waits for an MCP 2 release.
5. **Limits.** The supervisor's recursion limit (LangGraph's default, 10 007) isn't reached by any test. Pydantic
   AI's `MCPToolset` reads FastMCP-client attributes that a later release may rename; its version is in the identity,
   and the runner's tests fail on such a change.
