# ADR 0015: Context management (token accounting, output spill, compaction)

Status: accepted · 2026-11-02 · Requirements: SUB-06, SUB-07, MEM-05, RCP-05 (context part)

## Context

Before this change the conversation grew until the provider refused it, which ended the run as `context_overflow`.
`caps.context_len` in `providers.yaml` was never read. A tool result longer than 16,000 characters was cut there and
the rest was lost. Tasks with data bigger than the context (planned for #52) need the harness to see how full the
context is and to do something about it, and the choice of what to do is a harness dimension worth measuring.

## Decision

1. **Estimate every prompt, offline, the same way for every model.** Before every model call
   (`shapes/common.model_turn`), the prompt is estimated as the UTF-8 bytes of the messages and tool schemas (compact
   JSON, sorted keys) ÷ 3, plus 4 tokens per message (`substrate/context.estimate_tokens`). No tokenizer is loaded:
   - tiktoken works offline here (LiteLLM bundles `cl100k_base` and points `TIKTOKEN_CACHE_DIR` at it), but that is
     OpenAI's vocabulary, not that of the Llama, Qwen and gpt-oss models the factory runs;
   - loading it takes about 2.5 s and ties the numbers to a LiteLLM release;
   - three bytes per token over-counts prose and JSON. Measured with cl100k: a median of 4.0 characters per token
     over this repository's source, task and seed files, and 3.6 for JSON tool messages. It under-counts only dense
     numbers (down to about 2.0). So the estimate errs towards compacting early.
   Each model_call span records `est_tokens_in` and `context_len` next to the provider's own `tokens_in`, so the
   ratio can be checked per model from traces and the divisor recalibrated later. The estimate is deterministic: the
   same messages give the same number on every platform.
2. **A prompt that can't fit stops the run before the call.** The window is the served model's `context_len`; the
   limit is `context_len − max_output_tokens` of the calling slot (room for a full reply). Over the limit,
   `model_turn` stops with `error:BUDGET`, which `finish()` already maps to `context_overflow`. It is a harness stop:
   graded on the environment, outcome × `OVERRUN_OUTCOME_FACTOR`, status at most PARTIAL, the same in every shape
   because every shape calls the model only through `model_turn`. A provider overflow that gets past the estimate
   still ends the same way (classified in `gateway.classify_provider_error`, unchanged), so a low estimate costs one
   wasted call, never a different status. A high estimate can stop a prompt the provider would have taken; that is
   the price of not spending a call to find out, and it applies only above `context_len − max_output_tokens`.
3. **Compaction is an optional recipe field, `config.compaction: {at, keep_last_turns, prompt}`.** It is in
   `_HASH_OMIT_WHEN_NONE`, so no existing recipe hash changes, and its prompt source joins the hash when set. When
   the estimate passes `at × context_len` (never above the limit), the conversation is split into
   - head: the system message(s) and the task message, kept verbatim;
   - region: the older turns, replaced by one user message that starts with a fixed prefix and holds the summary;
   - tail: the last `keep_last_turns` assistant turns with their tool results, and any instruction directly before
     them, kept verbatim.
   The cut is always before an assistant or user message, so a tool call is never separated from its result; a
   region whose calls and results don't pair up is not compacted. If the latest instruction (a V3 stage or V4 step
   prompt) falls in the region and the tail has none, it is kept after the summary. At most one summary per turn;
   if the prompt still doesn't fit, the run stops as in 2. The behaviour lives in `shapes/compaction.py` and is
   called from `model_turn`, so V1–V5 (and delegated helpers, under their own recipe) compact the same way.
4. **The summary is never free.** It is a model call through the gateway, so it takes a quota slot, is classified
   and retried like any call, and its tokens and price land in C. It uses a step of the run's budget (and of a V3
   stage or V4 step cap) and its tokens count toward `max_tokens`. It runs on the run's model in a `compactor`
   slot: the recipe's own `model_slots.compactor` if it declares one (left unpinned, like the planner), else the
   calling slot's settings. It is traced as a `compaction` span (`est_tokens_before`, `est_tokens_after`,
   `tokens_dropped`, `replaced_messages`, a preview) that holds its `model_call` span (`purpose: compaction`). The
   replaced messages are kept in `snapshot.compactions`, so the transcript still has the whole conversation.
5. **Oversized output spills to the state folder.** A tool result over 16,000 characters is written in full to
   `<sandbox>/state/spill/spill-<n>.txt` (next to the database, never in the workspace: the model's file tools can't
   reach it and the graders' diff never sees it). The model gets the first 6,000 and last 2,000 characters and a
   note with the handle and page count. Handles are numbered in order, so a replayed run gets the same ones.
6. **Spills are read with a harness-side tool, `spill__read(handle, page)`** (8,000-character pages), handled in
   `ToolExecutor` like any call: traced as a `spill.read` tool span, charged to the tool-call budget, an unknown
   handle or page is an invalid call in Q, a repeated page is a wasted read. It is offered in `model_turn` (not in
   `offered_tools`, which V2 and V4 evaluate once per run) to any turn that offers tools, from the first spill on, so
   every shape gets it identically and runs that never spill see exactly the tools they saw before. One store per
   run, shared by delegated helpers. The area is capped at 4,000,000 characters and 64 spills per run; past the
   cap an output is cut at 16,000 characters as before, with a note that says so.
7. **Validation (RCP-05, context part).** `validate_recipe` refuses a slot whose `max_output_tokens` leaves no room
   in the model's `context_len` (`context_fits_reply`), a compaction whose `keep_last_turns` replies alone would fill
   the context (`compaction_fits_context`), a missing compaction prompt and a pinned compactor model. At
   provisioning it checks the run's model; `hfx validate` (and the authoring snapshot, which must agree with it)
   now passes the provider catalog and checks every active served model.

## Consequences

- `v2-compact` (v2-base + `compaction: {at: 0.75, keep_last_turns: 2}`) is in the matrix and the CI matrix gate. The
  visible tasks never reach 75% of a 32k context with the scripted models, so offline it scores like `v2-base`;
  tests shrink the scripted model's `context_len` on their own catalog to force compaction in V2, V3 and V4 and the
  pre-call stop in all five shapes. The scripted backends answer a compactor-slot request with a fixed summary
  built from the request, without moving their script.
- Hash impact: none on existing recipes or env hashes (`hfx validate` output is unchanged apart from the new
  recipe). The legacy `config.compaction_at` field stays in the hash and stays refused when changed; it is
  superseded by `config.compaction.at`.
- Behaviour without a hash change: a tool output over 16,000 characters is now shown as head + tail + handle instead
  of its first 16,000 characters, and `spill__read` is offered after it. No task in the catalog produces such an
  output (no spill in the smoke traces of any shape), so no stored result is affected; tasks that do are new.
- The pre-call stop applies to every recipe. With the catalog's limits (≥ 32k context, ≤ 2k replies, ≤ 200k token
  budgets) it fires only for prompts near the window, which the provider would have refused anyway.
- Not done here: per-model calibration of the divisor from recorded `est_tokens_in`/`tokens_in` pairs; compacting by
  dropping or truncating old tool results without a model call; memory stores, which build on this (#78).
