# ADR 0005: Other agents as tools

Status: accepted · 2026-10-30 · Requirements: EXT-10 (builds on EXT-05 to EXT-08, ADR 0004)

## Context

Delegation (ADR 0003) hands work to a helper the factory runs itself, so its model calls are spans of the run and
are counted like the parent's. An agent another team runs is different: it sits behind its own MCP server, calls its
own model, and the factory sees only its answer. Treated as an ordinary remote tool, its work would be free: a
harness that hands the hard part to an outside agent would look cheap and effective, and every comparison against a
harness that does the work itself would be unfair. EXT-10 asks for such agents as tools with a declared risk level,
whose usage counts toward the run's budget.

## Decision

1. **A tool, declared as an agent.** A tool spec takes an optional `agent: {max_calls, price_in, price_out}`. It is
   still a tool: risk, side effect, permission policy, faults, timeouts and record/replay apply unchanged. No new
   recipe dimension: whether a recipe may use an agent is its tool slice, and whether a task may is its allowance.
2. **Remote only.** An agent calls a model, which nothing inside the sandbox can reach, so the catalog refuses agent
   tools on stdio servers. With ADR 0004 that also makes them READ-only until test tenants exist.
3. **Usage is part of the answer.** The agent reports `_meta["harness-factory/usage"] = {tokens_in, tokens_out,
   usd?, model?}` on its result (the MCP `_meta` field; the key uses a prefix of our own, as the spec asks). The
   tokens go on the tool call's span, so `BudgetGuard.charge` counts them toward `max_tokens` and `CostLedger` adds
   them to the run's tokens and USD. A missing `usd` is priced at the declared reference rates, as free models are
   priced at a reference rate. `agent_calls`, `agent_tokens` and `agent_usd` on the ledger say how much was the
   agent's; the stored totals already include it, so C and E need no change and no store column is added.
4. **No usage, no answer.** A successful answer without valid usage is withheld from the model and marked
   `infra: agent_usage_missing`: the environment failed, so it is excluded from Q, and the work is not free. An error
   without usage (a `ToolError` raised before any work) charges nothing and is passed on as a normal tool error.
5. **Bounded per run.** At most `max_calls` calls per agent tool per run, delegated helpers included (they share the
   counter). Calls past it are refused before reaching the agent and count as invalid in Q, as delegate calls past
   their limit do. Faulted calls never reach the agent and are not counted.
6. **Identity.** `max_calls` is part of `env_hash` (it changes what a harness can do); prices are not (CLAUDE.md:
   prices are not hashed). Tools without `agent:` dump exactly as before, so no existing env hash changes. The
   agent's own model id is pinned by the server's version, which is already in `env_hash`.
7. **Observable.** The span carries the tokens (exported as `gen_ai.usage.*`), `usd`, `agent_model` and the call's
   ordinal; the recording keeps `_meta`, so replay reproduces the cost exactly.

## Consequences

- An agent's self-reported usage is trusted. A dishonest or buggy agent can under-report; the reference rates and a
  declared `max_calls` limit the damage, and a recorded run keeps the report for audit.
- The agent is not told the run's remaining budget, so one call can overspend it; the overspend is recorded and the
  run stops at the next gate, as a model call that overspends does.
- No agent server is in the catalog: there is no shared one to point at. The tests use a stand-in
  (`tests/remote_agent_server.py`) with canned answers and usage.
