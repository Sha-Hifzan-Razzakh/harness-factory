"""Campaign reach (#110): a declared substitute (a tool granted by one task tool alone) counts as reaching it, pairs a
campaign leaves out are printed with the tools they miss, and a reduced tool set can be planned on purpose.

The add-on here mirrors the DegreeAudit lab: an `eng` server the tasks allow, an `eng_guided` server whose tools are
granted by `eng`'s one for one (same tools, other descriptions), a coarse recipe with one `eng` tool only, and the
built-in v2-base, which opens no engine at all."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

from harness_factory import addons
from harness_factory.provision.provisioner import missing_task_tools, reaches, substitutes
from harness_factory.runner import bench as b

SERVER = '''
from mcp.server.mcpserver import MCPServer

app = MCPServer("{name}")


@app.tool()
def evaluate(student: str) -> str:
    """Evaluate."""
    return student


if __name__ == "__main__":
    app.run()
'''

ENGINE = ["validate", "evaluate", "explain"]
FROZEN = {"schema_hash": "0" * 16, "description_hash": "0" * 16}     # as `hfx tools freeze` would leave them


def make_engine_addon(root: Path) -> Path:
    pkg = root / "src" / "engaddon"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    for name in ("eng", "eng_guided"):
        (pkg / f"{name}_server.py").write_text(SERVER.format(name=name))
    (root / "addon.yaml").write_text(yaml.safe_dump({
        "id": "eng-addon", "python_path": ["src"], "libraries": [],
        "servers": {"engaddon.eng_server": [], "engaddon.eng_guided_server": []}}))
    cat = root / "catalog"
    (cat / "tools.yaml").parent.mkdir(parents=True)
    (cat / "tools.yaml").write_text(yaml.safe_dump({"servers": [
        {"id": "eng", "transport": "stdio", "module": "engaddon.eng_server",
         "tools": [{"name": t, "side_effect": "READ", "risk": "R0", "timeout_s": 10, **FROZEN} for t in ENGINE]},
        {"id": "eng_guided", "transport": "stdio", "module": "engaddon.eng_guided_server",
         "tools": [{"name": t, "side_effect": "READ", "risk": "R0", "timeout_s": 10, "granted_by": [f"eng.{t}"],
                    **FROZEN} for t in ENGINE]}]}))
    for tid in ("eng-01", "eng-02"):
        (cat / "tasks" / tid).mkdir(parents=True)
        (cat / "tasks" / tid / "task.yaml").write_text(yaml.safe_dump({
            "id": tid, "domain": "data", "instruction": "Evaluate the student and write out.txt.",
            "allowed_tools": ["fs.read_file", "fs.write_file", *[f"eng.{t}" for t in ENGINE]],
            "oracle": {"checks": [{"id": "a", "kind": "file_contains", "path": "out.txt", "text": "ok", "weight": 1}]}}))
    (cat / "recipes").mkdir()
    recipes = {
        "v2-eng": {"tool_ids": ["fs.read_file", "fs.write_file", "eng.*"], "mcp_servers": ["fs", "eng"]},
        "v2-eng-guided": {"tool_ids": ["fs.read_file", "fs.write_file", "eng_guided.*"],
                          "mcp_servers": ["fs", "eng_guided"]},
        "v2-eng-coarse": {"tool_ids": ["fs.read_file", "fs.write_file", "eng.evaluate"], "mcp_servers": ["fs", "eng"]},
    }
    for rid, tools in recipes.items():
        (cat / "recipes" / f"{rid.replace('-', '_')}.yaml").write_text(
            yaml.safe_dump({"id": rid, "version": 1, "base": "v2-base", "tools": tools}))
    return root


@pytest.fixture
def eng(tmp_path, monkeypatch):
    """A Factory loaded with the engine add-on (HF_ADDONS for this test only)."""
    root = make_engine_addon(tmp_path / "eng")
    monkeypatch.setenv(addons.ENV, os.pathsep.join([str(root)]))
    addons._cached.cache_clear()
    from harness_factory.runner.runner import Factory
    yield Factory.load(store=False)
    addons._cached.cache_clear()


RECIPES = ["v2-eng", "v2-eng-guided", "v2-eng-coarse", "v2-base"]


# ------------------------------------------------------------------ substitutes

def test_a_tool_granted_by_one_tool_with_its_side_effect_stands_in_for_it(factory):
    d = factory.declared
    # search.find and fsx.tree are granted by fs.list_dir alone and are READ like it
    assert substitutes({"search.find", "fsx.tree"}, d) == {"fs.list_dir": ["fsx.tree", "search.find"]}
    # granted by several tools: does less than each of them, so stands in for none
    assert substitutes({"search.grep", "fsx.edit", "fsx.move"}, d) == {}
    # granted_by: [] (harness material) stands in for nothing
    assert substitutes({"skills.load"}, d) == {}


def test_the_guided_recipe_reaches_the_engine_tasks_through_its_substitutes(eng):
    d, task = eng.declared, eng.tasks.get("eng-01")
    assert missing_task_tools(eng.recipes["v2-eng"], task, d) == []
    assert missing_task_tools(eng.recipes["v2-eng-guided"], task, d) == []
    assert reaches(eng.recipes["v2-eng-guided"], task, d)
    # a reduced tool set is still a reduced tool set: no substitute covers what the coarse recipe leaves out
    assert missing_task_tools(eng.recipes["v2-eng-coarse"], task, d) == ["eng.explain", "eng.validate"]
    assert missing_task_tools(eng.recipes["v2-base"], task, d) == [f"eng.{t}" for t in sorted(ENGINE)]


def test_the_selector_prunes_by_the_same_rule(eng):
    from harness_factory.provision.provisioner import task_class
    from harness_factory.select.selector import hard_rules, load_selector_config
    task = eng.tasks.get("eng-01")
    cls = task_class(task, eng.declared)
    config = load_selector_config()
    config.depth_shapes.setdefault(cls.split("|")[0], []).append(eng.recipes["v2-eng"].variant)

    def pruned(rid):
        return hard_rules(eng.recipes[rid], cls, "scripted/replay", "scripted", eng, config, task=task)
    assert pruned("v2-eng") is None and pruned("v2-eng-guided") is None
    assert pruned("v2-eng-coarse").rule == "reaches_task_tools"
    assert "eng.explain" in pruned("v2-eng-coarse").detail


# ------------------------------------------------------------------ plans

def test_plans_include_substitutes_and_reduced_tool_sets_only_on_request(eng):
    from harness_factory.runner.runner import plan, skipped_pairs
    tasks = ["eng-01", "eng-02"]
    specs = plan(eng, RECIPES, "scripted", ["scripted/replay"], task_ids=tasks)
    assert {(s.task_id, s.recipe_id) for s in specs} == {(t, r) for t in tasks for r in ("v2-eng", "v2-eng-guided")}
    left = skipped_pairs(eng, RECIPES, [eng.tasks.get(t) for t in tasks])
    assert [(t, r) for t, r, _ in left] == [("eng-01", "v2-eng-coarse"), ("eng-01", "v2-base"),
                                           ("eng-02", "v2-eng-coarse"), ("eng-02", "v2-base")]
    assert left[0][2] == ["eng.explain", "eng.validate"]

    opted = plan(eng, RECIPES, "scripted", ["scripted/replay"], task_ids=tasks,
                 unreachable_ok=["v2-eng-coarse", "v2-base"])
    assert len(opted) == 8 and {s.recipe_id for s in opted} == set(RECIPES)
    # the opt-in plans a pair; it changes nothing about the runs it plans
    assert [s for s in opted if s.recipe_id in ("v2-eng", "v2-eng-guided")] == specs


def test_describe_pairs_groups_by_recipe():
    from harness_factory.runner.runner import describe_pairs
    pairs = [("t1", "r", ["s.a"]), ("t2", "r", ["s.a", "s.b"]), ("t1", "q", ["x.y"])]
    assert describe_pairs(pairs) == ["r on 2 task(s) (t1, t2): can't call s.a, s.b", "q on 1 task(s) (t1): can't call x.y"]


# ------------------------------------------------------------------ bench file

def bench_file(tmp_path: Path, **extra) -> Path:
    data = {"name": "eng-reach", "tasks": {"splits": ["DEV", "TEST"], "ids": ["eng-01", "eng-02"]},
            "recipes": RECIPES, "models": [{"model": "scripted/replay", "provider": "scripted"}],
            "judge": {"compare": {"base": "v2-eng", "against": ["v2-eng-guided", "v2-eng-coarse", "v2-base"]}},
            **extra}
    path = tmp_path / "eng.bench.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    return path


def test_plan_unreachable_must_name_listed_recipes(tmp_path):
    with pytest.raises(b.BenchFileError, match="plan_unreachable names recipe.s. not in `recipes`: v4-planner"):
        b.load(bench_file(tmp_path, plan_unreachable=["v4-planner"]))
    with pytest.raises(b.BenchFileError, match="listed more than once: v2-base"):
        b.load(bench_file(tmp_path, plan_unreachable=["v2-base", "v2-base"]))


def test_the_bench_lists_what_it_leaves_out_and_what_it_runs_reduced(eng, tmp_path):
    spec, _ = b.load(bench_file(tmp_path))
    assert len(b.build_plan(eng, spec)) == 4
    left, reduced = b.reach(eng, spec)
    assert {r for _, r, _ in left} == {"v2-eng-coarse", "v2-base"} and reduced == []
    section = b.reach_section(left, reduced)
    assert section.startswith("## Reach") and "Left out 4 task x recipe pair(s)" in section
    assert "- v2-eng-coarse on 2 task(s) (eng-01, eng-02): can't call eng.explain, eng.validate" in section

    spec, _ = b.load(bench_file(tmp_path, plan_unreachable=["v2-eng-coarse"]))
    assert len(b.build_plan(eng, spec)) == 6
    left, reduced = b.reach(eng, spec)
    assert {r for _, r, _ in left} == {"v2-base"} and {r for _, r, _ in reduced} == {"v2-eng-coarse"}
    assert "Planned 2 pair(s) on a reduced tool set" in b.reach_section(left, reduced)
    assert b.reach_section([], []) == ""


def test_bench_dry_run_prints_the_left_out_pairs(eng, monkeypatch, tmp_path):
    from typer.testing import CliRunner

    from harness_factory import settings
    from harness_factory.cli import main as cli
    monkeypatch.setattr(cli, "_factory", lambda store=True: eng)
    monkeypatch.setattr(cli.console, "width", 200)
    monkeypatch.setattr(settings, "VAR", tmp_path / "var")
    out = CliRunner().invoke(cli.app, ["bench", str(bench_file(tmp_path, plan_unreachable=["v2-eng-coarse"])),
                                       "--dry-run"])
    assert out.exit_code == 0, out.output
    text = " ".join(out.output.split())
    assert "= 6 runs" in text
    assert "left out 2 task x recipe pair(s)" in text and "v2-base on 2 task(s) (eng-01, eng-02)" in text
    assert "planned 2 pair(s) on a reduced tool set (plan_unreachable)" in text
