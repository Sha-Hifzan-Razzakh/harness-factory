"""Recipe ids for the CI gates, read from the catalog, so a new recipe is gated without editing ci.yml.

    uv run --no-sync python scripts/ci/recipes.py <group>        # prints the group's ids, comma-separated
    uv run --no-sync python scripts/ci/recipes.py <group> i/n    # only every n-th id from the i-th (parallel CI jobs)

Groups (every recipe but v1-base and v2-base is in exactly one; tests/test_ci_recipe_groups.py checks it):

    shapes          base recipes of V3, V4 and V5 (their caps can't fit the `long` tasks' references)
    matrix          matrix recipes (`base: ...`) on the native V2 loop: run on every task
    matrix-planned  matrix recipes on a V3, V4 or V5 shape: run without `long` tasks, like their base
    runners         matrix recipes on a framework runner (`runtime: ...`, ADR 0016): run without `long` tasks,
                    because a framework keeps its own untrimmed history and can overflow the context on them

v1-base and v2-base are gated by the P0 gate (`hfx smoke` with its default recipe) and the shape tests.
"""

from __future__ import annotations

import sys

from harness_factory import settings
from harness_factory.recipes.model import HarnessRecipe, Variant, load_recipes

PLANNED = {Variant.V3_WORKFLOW_GRAPH, Variant.V4_PLANNER, Variant.V5_ORCHESTRATOR}
UNGROUPED = {"v1-base", "v2-base"}


def group_of(recipe: HarnessRecipe) -> str | None:
    if recipe.id in UNGROUPED:
        return None
    if recipe.base is None:
        return "shapes" if recipe.variant in PLANNED else None
    if recipe.runner is not None:
        return "runners"
    return "matrix-planned" if recipe.variant in PLANNED else "matrix"


def groups() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {"shapes": [], "matrix": [], "matrix-planned": [], "runners": []}
    for rid, recipe in sorted(load_recipes(settings.RECIPES_DIR).items()):
        g = group_of(recipe)
        if g is not None:
            out[g].append(rid)
    return out


def part(ids: list[str], spec: str) -> list[str]:
    """The i-th of n slices ("i/n", 1-based), round-robin, so the parts together are the whole group."""
    i, n = (int(x) for x in spec.split("/"))
    if not 1 <= i <= n:
        raise ValueError(f"part {spec!r}: expected i/n with 1 <= i <= n")
    return ids[i - 1::n]


def main(argv: list[str]) -> int:
    all_groups = groups()
    if len(argv) not in (1, 2) or argv[0] not in all_groups:
        print(f"usage: recipes.py {{{'|'.join(all_groups)}}} [i/n]", file=sys.stderr)
        return 2
    ids = all_groups[argv[0]]
    if len(argv) == 2:
        try:
            ids = part(ids, argv[1])
        except ValueError as e:
            print(e, file=sys.stderr)
            return 2
    if not ids:                     # an empty --recipe would silently fall back to v2-base
        print(f"no recipe in group {argv[0]!r}", file=sys.stderr)
        return 1
    print(",".join(ids))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
