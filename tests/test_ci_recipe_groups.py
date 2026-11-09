"""CI gates read their recipe lists from scripts/ci/recipes.py: every recipe must land in a gate."""

import importlib.util
from pathlib import Path

from harness_factory import settings
from harness_factory.recipes.model import load_recipes

SCRIPT = Path(__file__).parents[1] / "scripts" / "ci" / "recipes.py"


def _module():
    spec = importlib.util.spec_from_file_location("ci_recipes", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_every_recipe_is_in_exactly_one_ci_group():
    ci = _module()
    groups = ci.groups()
    grouped = [rid for ids in groups.values() for rid in ids]
    assert len(grouped) == len(set(grouped))
    assert set(grouped) | ci.UNGROUPED == set(load_recipes(settings.RECIPES_DIR))
    assert all(groups.values())


def test_groups_follow_shape_and_runtime():
    ci = _module()
    recipes = load_recipes(settings.RECIPES_DIR)
    groups = ci.groups()
    assert {"v3-staged", "v4-planner", "v5-orchestrator"} <= set(groups["shapes"])
    assert all(recipes[r].runner is not None for r in groups["runners"])
    assert all(recipes[r].runner is None and recipes[r].base for r in groups["matrix"] + groups["matrix-planned"])
    assert all(recipes[r].variant.value == "V2_TOOL_LOOP" for r in groups["matrix"])


def test_cli_prints_a_group_and_refuses_an_unknown_one(capsys):
    ci = _module()
    assert ci.main(["matrix"]) == 0
    assert "v2-recover" in capsys.readouterr().out.split(",")
    assert ci.main(["nope"]) == 2


def test_degree_recipes_have_their_own_group():
    """Recipes that open the audit server are gated on the degree tasks only, never in the generic gates (HF-2)."""
    ci = _module()
    recipes = load_recipes(settings.RECIPES_DIR)
    groups = ci.groups()
    assert {"v2-audit", "v2-audit-rag", "v3-audit"} <= set(groups["degree"])
    assert all(ci.DEGREE_SERVER in recipes[r].tools.mcp_servers for r in groups["degree"])
    generic = groups["shapes"] + groups["matrix"] + groups["matrix-planned"] + groups["runners"]
    assert not any(ci.DEGREE_SERVER in recipes[r].tools.mcp_servers for r in generic)
