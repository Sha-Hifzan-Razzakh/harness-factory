# Designing the test matrix: points not to forget

Read this before planning a campaign or adding a dimension to the matrix. It lists what the current numbers do and
don't cover, and the design decisions already agreed for closing the gaps. `benchmark.example.yaml` next to this file
shows a whole benchmark in one readable file, with every line marked `[today]` or `[planned]`.

## 1. Recipes are measured on the factory's own runtime

Every score so far comes from the native runtime. The shapes are LangGraph `StateGraph`s, but model turns, tool
calls, retries and prompts are the factory's own (`shapes/common.py`, `ModelGateway`, `ToolExecutor`). Production
agents are often built on a framework (LangGraph's ready-made agent, LlamaIndex agents, …), which adds its own
system prompt, tool formatting, tool-call parsing, retries and history handling. Whether a recipe's ranking holds
there has **not been measured**.

What carries over from a recipe, and how safely:

| Recipe part | Carries over? |
|---|---|
| Shape (V2 loop, V3 stages, V4 planner, V5 orchestrator) | Yes: a hand-built LangGraph graph has the same control flow |
| Tool selection, permissions per side effect, budgets | Yes: plain settings any framework can enforce |
| Delegation, planning settings, workflow stages | Yes, with wiring: sub-agents and plan-and-execute exist in the frameworks |
| Prompts and skills | Only if copied exactly; a framework's ready-made agent adds its own prompt |
| How tools are shown to the model | Risky: schema formatting and call parsing differ per framework |
| Retries, history trimming, parallel tool calls, memory | Risky: framework defaults change cost and sometimes success |

So "V3 beats V2 on unknown-path tasks for this model" is a design claim that should mostly transfer. "V3 scores 0.89"
is a claim about the native runtime.

**Until runners are measured:** build production agents the way the shapes are built (your own graph, the recipe's
prompts, tool list and limits), not on a ready-made agent with its own prompt.

## 2. The runner axis (planned)

The framework that runs a harness becomes a matrix axis, like the model:

- **Recipe option** `runtime: native | langgraph-prebuilt | llamaindex-agent | …`. A framework runtime builds the
  harness with that framework's own agent, prompts and tool handling: what you would deploy.
- **Same measurement points.** The framework gets a chat-model wrapper that sends every call through
  `ModelGateway` and tool wrappers that call `ToolExecutor`. Quota pacing, token and cost counting, permissions,
  injected faults and traces stay the factory's, so scores stay comparable. A runner whose model calls can't be
  routed through the gateway (e.g. a hosted agent service) can't be benchmarked like this; at most it is an agent
  tool with self-reported usage (EXT-10, weaker evidence).
- **Identity.** The runner and its exact framework version (pinned in `uv.lock`) go into the run's identity, so a
  framework upgrade starts new cells, and shows whether the upgrade changed agent behaviour.
- **Offline.** The scripted models must drive every runner through the wrappers, so CI checks runners without keys.

Two questions it answers:

1. **Does a recipe carry over to a runner?** Same recipe, tasks, seeds and model; only the runner differs. Judge the
   paired E0 difference with the provider-calibration rule (ADR 0010: equivalent within ±margin, different when the
   95% interval excludes 0, else inconclusive).
2. **Which runner is best for a task class?** The runner is part of the cell (task class × recipe × model ×
   runner), and the selector can pick runner and recipe together.

What a runner benchmark should report, besides E: tokens spent on the framework's hidden prompts and tool
formatting, extra model calls from framework retries, invalid tool calls caused by tool formatting, robustness under
injected faults (framework error handling), and wall-time overhead.

Fairness rules: same tasks, tool permissions and per-run budget for every runner. Framework defaults are what you
deploy, so they are left as they are and recorded. Tuning them per runner is a separate experiment.

## 3. Matrix checklist

| Axis | Values today | Keep in mind |
|---|---|---|
| Tasks | DEV + TEST (`hfx smoke`); HELD_OUT only for selector evaluation | Judge **skills** on batch 3 (`--tag batch-3`, written by an author who never saw them); batches 1–2 give an upper bound |
| Recipes | 5 shapes + matrix recipes (one dimension each) | Compare shapes with paired runs; `hfx report` pairs only matrix recipes with their base today |
| Models | `catalog/providers.yaml` | A model is measured with a runner and a provider; keep cells split until calibrated |
| Providers | per cell until calibrated | Pool only on a saved `equivalent` verdict (`hfx calibration`, ADR 0010); cost stays per provider |
| Runners | `native` only | Planned axis (section 2); until then, results describe the native runtime |
| Seeds | `--seeds N` | More seeds tighten the intervals; the verdicts need about 20 pairs |
| Faults | `none` + profiles | Faulted runs feed only R, never E means or cost medians |
| Budget, days | `--budget-usd`, `--deadline-days` | Free tiers spread a big plan over days; `--resume` after the 00:00 UTC reset |
| Cost baseline | live, or frozen with `hfx baseline save` | Freeze before comparing across campaigns |

## 4. Open items

- **`hfx bench <file>`** (planned): run a benchmark described like `benchmark.example.yaml` instead of long CLI
  flags, and record the file with the campaign.
- **Pairing any two recipes** in the report (e.g. shapes against each other), not only matrix recipe against base.
- **Selector on pooled cells** once providers are calibrated (ADR 0010, follow-up).
- **Allocation planner** (P2): which provider serves each cell. A claim commit for it exists on branch
  `feat/allocation-planner`; check the session ledger before starting it.
