# ADR 0007: V4 planner shape

Status: accepted · 2026-10-30 · Roadmap: P1 shapes (V4 planner; V5 orchestrator later)

## Context

V2 lets the model decide each next call. V3 fixes the stages in the recipe. A planner shape sits in between: the
model writes its own plan once, before acting, and the harness holds it to that plan one step at a time. On long
known-path tasks, that is the usual fix for a model that loses track or stops early. Whether it helps is exactly the
kind of question the factory exists to answer, so it has to be a shape that differs from V2 and V3 only in control
flow.

## Decision

1. **Plan, then steps.** The planner slot gets the task and the offered tools (names and descriptions) and calls
   nothing. It replies with numbered lines. Each line becomes a step: a user message (`prompts/plan/step.j2`, which
   shows the plan with the first step) followed by a bounded tool loop of the executor (`step_max_steps` model
   calls). The executor keeps one conversation across steps, so later steps see what earlier ones found. The output
   is the last step's reply.
2. **Replanning is bounded.** A step that replies `BLOCKED: ...` goes back to the planner with what was done and why
   it is blocked (`prompts/plan/replan.j2`). The new plan replaces the steps not yet run, at most `max_replans`
   times per run. Without replans left, the run moves on to the next step.
3. **Shared machinery.** Planner and executor turns use `model_turn`, steps use `run_tool_calls`, and the end uses
   `finish`, so budgets, malformed-reply recovery, the delegate tool and the stop-to-status mapping are exactly V2's.
   A step that hits its cap is ended and recorded (`step_cap`), as a V3 stage cap is.
4. **Lenient parsing, recorded.** "1.", "2)", "Step 3:" all count. A plan longer than `max_plan_steps` keeps its
   first steps and merges the tail into the last one, so no planned work is dropped. A plan with no numbered lines
   becomes the single step "Complete the task." and is recorded as `plan_unparsed` (not critical).
5. **One model per run.** The planner slot takes the run's model; a recipe that pins one is refused
   (`planner_model_unpinned`), because only the executor's model is in the RunKey. A planner on a different model is
   a later campaign axis, not a recipe setting.
6. **Identity and validation.** `planning` is optional and in `_HASH_OMIT_WHEN_NONE`, so no existing recipe hash
   changes. Its prompt templates are in the recipe hash (`prompt_refs`). It is required for V4 and refused
   elsewhere. Its worst case, `(1 + max_replans) × (1 + max_plan_steps × step_max_steps)`, must fit `max_steps`.
7. **Observable.** `handoff` spans for plan, replan and each `step:<n>`; tool calls carry scope `step:<n>`;
   `snapshot.plans`, `snapshot.plan_trail`, `snapshot.replans` and `snapshot.planner_prompt_hash`.

## Consequences

- `v4-planner` allows 8 plan steps (the longest reference plan, ops-04, has 8 calls) of 3 calls each and one replan.
  Its `max_steps` is 50 (the worst case), with tool calls and tokens capped as in `v3-staged`. Offline it passes
  20/20 at about 81k tokens against V2's 28k, so its E is lower. The scripted model never loses track, so offline
  planning can only cost.
- The scripted models plan from the reference when called in the planner slot and run one plan step per V4 step
  (`tasks/reference.py`), so every scripted style exercises V4 without a real model.
- V5 reuses the planner (`shapes.v4.Planner`): an orchestrator is a planner whose steps are delegate calls (ADR 0008).

## Later: structured plans (PR #51)

`planning.structured: true` replaces the numbered lines with JSON (`prompts/plan/planner_structured.j2`): steps with
`id`, `instruction`, `tools` (hints, from the tools the planner was shown), `depends_on` and `done_when`, validated
with Pydantic (`shapes/plans.py`). Nothing is repaired silently: a reply that isn't valid JSON, has too many steps,
a duplicate id, an unknown tool or dependency, or a cycle, is a malformed output. Its model call is marked
`MODEL_PARSE` (an invalid call in Q; its tokens still count in C), it is recorded as `plan_invalid`, and the planner
is asked again with the reason (`plan/fix_structured.j2`), at most `planning.plan_retries` times per run (default
1). Then the run stops as `malformed_output`, a harness stop. JSON mode (`response_format: json_object`) is sent when
the model's caps declare `structured_output` and the backend takes it (LiteLLM); otherwise the prompt alone asks for
JSON, which is how the scripted models get it. V4 runs the steps one after another in dependency order (the first
ready step in the planner's order); the step text leads with its id (`[s2] ...`). The worst-case rule counts the
re-asks, so `v4-structured` sets `max_steps: 51`. The fields are omitted from the hash when unset (and `false` /
`1` / the default are stored as unset), so no existing hash changed.
