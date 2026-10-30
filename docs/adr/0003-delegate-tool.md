# ADR 0003: Delegation as a recipe dimension (agent as a tool)

Status: accepted · 2026-10-30 · Requirements: EXT-09, EXT-11, EXT-12 (and SHP-05 for V5 later)

## Context

"One agent" versus "an agent that can hand work to a helper" is a harness choice the factory should measure like
any other: change it in one recipe, run it next to its base, read the paired verdict. The requirements had sub-agents
only inside the V5 orchestrator shape. EXT-09 asks for a delegate tool in any shape; EXT-11 sets its limits.

## Decision

1. **A recipe field, not a shape.** `delegation: {recipe, max_calls, max_depth}` is an optional field (omitted from
   the hash when unset, so no existing hash changes). Every shape offers the tool through `shapes/common.offered_tools`,
   so V1, V2 and V3 get it the same way; a V3 stage offers it only if its tool patterns allow `delegate.run`.
2. **Harness-side, not an MCP server.** The helper needs the model gateway and the quota ledger, which never enter the
   sandbox. `shapes/delegate.py` runs the child recipe's shape in-process, on the parent's MCP sessions (same files,
   database and servers), through the parent's gateway and tracer.
3. **Nothing is free.** The helper's model calls are spans of the same run, so its tokens and cost are the run's
   (C). Its budget is its own config capped by what the parent has left, and what it uses is charged to the parent,
   so delegating never buys extra steps, tool calls or tokens. The delegate call itself is a tool call, an error
   delegate call (limit reached, bad arguments) counts as invalid in Q.
4. **The grant only narrows.** The helper's tools are its own slice intersected with the parent's allowed tools, its
   permission per side effect is the stricter of the two, and it may open no MCP server its parent doesn't
   (`delegate_servers` validation rule).
5. **Limits are the root's.** At most `max_calls` delegate calls per run across the whole tree, nested at most
   `max_depth` deep (the last level is not offered the tool). A recipe on the call chain is refused at run time, and
   `load_recipes` refuses delegation cycles, self-delegation included.
6. **Identity includes the helper.** The child's content hash is part of the delegating recipe's hash: editing the
   helper is a new experiment for every recipe that delegates to it.
7. **The helper's stop is not the parent's overrun.** A helper that hits its budget or stage cap is recorded as
   `delegate_stopped` (not in `OVERRUN_KINDS`); the parent sees "stopped early" and may carry on. The helper's tool
   violations are kept, prefixed `delegate <n>:`.
8. **Observable.** A `delegate.run` tool span carries the helper's recipe, depth, status and usage; a `handoff` span
   wraps its work; its tool calls carry scope `delegate:<n>` (so the trajectory grader judges its reads in its own
   scope); its conversation is in the transcript under `snapshot.delegations`.

## Consequences

- `v2-delegate` (helper: `v2-base`) is in the matrix and CI's matrix gate. Offline it passes 20/20 at about 37% more
  tokens than `v2-base`, so its E is lower; whether delegation pays is a Phase 2 question.
- The scripted `delegate` style hands the whole task over when offered the tool, which exercises the path without a
  real model. Parent and helper share one scripted backend, as they share one gateway.
- External agents (EXT-10) and remote MCP servers (EXT-05 to EXT-08) are not covered here: both need network access
  from the sandbox, which this change does not open.
- V5 can reuse this: its sub-agents are child recipes with a narrowing grant, the same machinery.
