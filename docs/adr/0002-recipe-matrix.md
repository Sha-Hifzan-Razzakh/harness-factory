# ADR 0002: Recipe matrix, one dimension at a time

**Status:** accepted (2026-10-29)

## Context

Phase 1 ran one recipe on one real model: 19 of 20 tasks passed. The three base recipes differ only in shape
(control flow), but the factory's purpose is to find which harness wins per task class across tools, skills,
prompts, config and memory. The recipe schema declared most of those fields, but before this change only tools
and prompts had any effect. `base:` was declared and ignored.

## Decision

1. **`base:` works.** A recipe can extend another: mappings merge key by key, and anything else is replaced. The
   recipe hash is taken of the resolved recipe, base id included. This is the recipe overlay of RCP-03 in the
   requirements; the v2 improvement loop stores its edits the same way (IMP-01), so a hand-written matrix recipe
   and an improver's edit are the same kind of object.
2. **Matrix recipes change one dimension.** They live in `catalog/recipes/matrix/`, are named
   `<base>-<change>`, and contain only `id`, `base` and the change. Any difference in results is then down to
   that change.
3. **A dimension is implemented once, in `shapes/common.py`,** so every shape gets it the same way. A new field is
   optional and listed in `_HASH_OMIT_WHEN_NONE`, so existing recipe hashes don't move.
4. **No free help.** Anything that gives the model another attempt (re-asking after a rejected reply, retrying a
   failed tool) still pays for the failed attempt: a step from the budget, and an invalid call in Q. A recipe
   can beat stopping, never a clean run.
5. **Every declared field either does something or is refused.** Fields not yet implemented (skills, memory,
   retrieval, operator prompt) are implemented one PR each. Until then a recipe that sets them should fail
   validation, which is the next step for those fields.

## Consequences

- **Q changed meaning slightly:** a reply the provider rejects as malformed is now an invalid call. Before, a
  run that ended on one kept Q from its tool calls alone (Phase 1's `code-01`: Q 1.0, O 0, so E₀ 0 either way).
  It only moves runs that had a rejected reply and partial credit.
- **Campaigns grow multiplicatively,** recipes × models × tasks. The matrix is run against the base it extends,
  not all pairs.
