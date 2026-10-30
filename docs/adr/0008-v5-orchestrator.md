# ADR 0008: V5 orchestrator shape

Status: accepted · 2026-10-30 · Roadmap: P1 shapes (the last one); builds on ADR 0003 (delegation) and ADR 0007 (V4)

## Context

V4 keeps one executor conversation across steps. Every step sees everything earlier steps saw, and the context
grows with the plan. An orchestrator hands each step to a worker that starts fresh, so contexts stay small and steps
stay separate. The cost is that a worker only knows what earlier workers reported, not what they saw. Which
trade-off wins depends on the task and the model, which is what the factory measures. The parts already existed:
V4's planner and the delegate machinery.

## Decision

1. **V4's planner, delegate steps.** V5 uses `shapes.v4.Planner` for the plan and replans (same prompts, parsing
   and limits). Each step is `delegate.run_delegate` with the rendered `planning.worker_prompt` as the sub-task: the
   overall task (raw instruction), the one-line reports of earlier workers, and this worker's part. The orchestrator
   calls no tools and offers none.
2. **Workers are delegations.** The worker is the recipe's `delegation` child. So, from ADR 0003:
   - workers share the sandbox, the MCP sessions and the gateway;
   - a worker's grant is its slice intersected with the orchestrator's allowance, with the stricter permission
     mode;
   - its budget is its own config capped by what the run has left, and charged back to the run;
   - a worker's stop is `delegate_stopped`, not the orchestrator's overrun;
   - each worker is a `delegate.run` tool span (scope `step:<n>`), and its conversation is in
     `snapshot.delegations`.
3. **Replan on a block or a stop.** A worker that replies `BLOCKED: ...`, stops early or fails sends the work done
   so far and the reason to the planner, at most `max_replans` times. Without replans left, the run goes on. The
   output is the last worker's answer.
4. **The planner sees the workers' tools.** The plan prompt lists the tools a worker will have (allowance ∩ worker
   slice), not the orchestrator's.
5. **Validation.** V5 needs `planning` with `worker_prompt` and needs `delegation`. `delegation.max_calls` must
   cover `max_plan_steps × (1 + max_replans)` steps (`workers_fit_plan`). `worker_prompt` is refused outside V5.
   The orchestrator's `config` is the whole team's budget, because workers are charged to it. There is no
   worst-case rule like V4's: workers are capped by what is left, so `max_steps` is the real limit.
   `step_max_steps` doesn't apply to V5.
6. **Identity.** `planning.worker_prompt` is optional and dropped from the hash when unset, so V4 and every other
   recipe keep their hashes. The worker template is in the recipe hash (`prompt_refs`), and the worker recipe's hash
   is part of the orchestrator's (delegation, ADR 0003).
7. **Scripted support.** `PlanBackend` treats a worker's "Your part: ..." like a V4 "Step i of n: ...": one plan
   step, then a close. The step boundary is now the prompt's text, not a count, because each worker's conversation
   holds one such prompt.

## Consequences

- `v5-orchestrator` (workers: `v2-base`, up to 8 steps, 1 replan, 16 worker calls; team budget 40 steps, 40 tool
  calls, 200k tokens) passes 20/20 offline at about 46k tokens: more than V2 (28k), less than V4 (81k) for the same
  plan, because worker contexts stay short.
- A worker can't see what an earlier worker read, only its report. Tasks where a later step needs an earlier step's
  details (exact file contents, say) will show that cost with real models.
- All five shapes on the roadmap (V1 to V5) are now built.
