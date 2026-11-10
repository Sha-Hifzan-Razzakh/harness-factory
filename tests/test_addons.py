"""Catalog add-ons (ADR 0021): catalog folders outside the repo, named in HF_ADDONS. A toy add-on here has one
server (it reads the task's read-only reference file), a task, a recipe and a conformance fixture. With HF_ADDONS
unset nothing changes; with it set, everything joins without replacing anything, and clashes are refused."""

from __future__ import annotations

import json
import os
import stat
import textwrap
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from harness_factory import addons, settings
from harness_factory.env.mcp import deps
from harness_factory.env.mcp.servers import load_tool_catalog
from harness_factory.env.sandbox import REF_FILE, DockerSandbox, LocalSandbox
from harness_factory.mcp import conformance
from harness_factory.mcp.pool import MCPSessionPool
from harness_factory.recipes.model import load_recipes
from harness_factory.substrate.errors import HarnessError
from harness_factory.tasks.model import load_task
from harness_factory.tasks.registry import TaskRegistry

SERVER = '''
import json, os
from pathlib import Path
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

app = MCPServer("toy", instructions="Looks values up in the run's reference data. Read-only.")


@app.tool()
def lookup(key: str) -> dict:
    """The value stored under key in the reference data."""
    path = os.environ.get("HF_REFERENCE", "")
    if not Path(path).is_file():
        raise ToolError("no reference data is mounted")
    data = json.loads(Path(path).read_text())
    if key not in data:
        raise ToolError(f"UNKNOWN_KEY: {key}")
    return {"key": key, "value": data[key]}


if __name__ == "__main__":
    app.run()
'''

MODULE = "toyaddon.server"
REFERENCE = {"alpha": 1, "beta": "two"}


def make_addon(root: Path, *, servers: dict | None = None, libraries: list | None = None, tools: list | None = None,
               addon_id: str = "toy-addon") -> Path:
    (root / "src" / "toyaddon").mkdir(parents=True)
    (root / "src" / "toyaddon" / "__init__.py").write_text("")
    (root / "src" / "toyaddon" / "server.py").write_text(SERVER)
    (root / "addon.yaml").write_text(yaml.safe_dump({
        "id": addon_id, "python_path": ["src"], "libraries": libraries or [],
        "servers": servers if servers is not None else {MODULE: []}}))
    cat = root / "catalog"
    (cat / "tasks" / "toy-01" / "seed").mkdir(parents=True)
    (cat / "tasks" / "toy-01" / "seed" / "reference.json").write_text(json.dumps(REFERENCE))
    (cat / "tasks" / "toy-01" / "task.yaml").write_text(yaml.safe_dump({
        "id": "toy-01", "domain": "data", "instruction": "Report the value stored under alpha.",
        "env_seed": {"reference": "seed/reference.json"}, "allowed_tools": ["toy.lookup"],
        "oracle": {"checks": [{"id": "a", "kind": "output_regex", "pattern": r"\b1\b", "weight": 1}]}}))
    (cat / "recipes").mkdir()
    (cat / "recipes" / "toy.yaml").write_text(yaml.safe_dump({"id": "v2-toy", "version": 1, "base": "v2-base",
                                                              "tools": {"mcp_servers": ["toy"]}}))
    (cat / "tools.yaml").write_text(yaml.safe_dump({"servers": tools if tools is not None else [
        {"id": "toy", "transport": "stdio", "module": MODULE,
         "tools": [{"name": "lookup", "side_effect": "READ", "risk": "R0", "timeout_s": 10}]}]}))
    (cat / "conformance").mkdir()
    (cat / "conformance" / "toy.yaml").write_text(textwrap.dedent("""\
        server: toy
        seed: {reference: ../tasks/toy-01/seed/reference.json}
        calls:
        - {tool: lookup, args: {key: alpha}, expect: {contains: ['"value": 1'], keys: [key, value]}}
        refusals:
        - {tool: lookup, args: {key: gamma}, contains: [UNKNOWN_KEY]}
        """))
    return root


@pytest.fixture
def use(monkeypatch):
    """Point HF_ADDONS at these folders for this test only."""
    def _use(*roots: Path) -> None:
        monkeypatch.setenv(addons.ENV, os.pathsep.join(map(str, roots)))
        addons._cached.cache_clear()
    yield _use
    addons._cached.cache_clear()


@pytest.fixture
def toy(tmp_path, use):
    root = make_addon(tmp_path / "toy")
    use(root)
    return root


async def freeze_toy(root: Path) -> None:
    """Write the toy server's discovered hashes into its tools.yaml, as `hfx tools freeze` would."""
    declared = load_tool_catalog(settings.TOOLS_FILE)
    with LocalSandbox("addon-freeze") as sb:
        async with MCPSessionPool(declared, ["toy"], sb.env(), sb.launch) as pool:
            hashes = {"schema_hash": pool.catalog.schema_hash("toy.lookup"),
                      "description_hash": pool.catalog.description_hash("toy.lookup")}
    f = root / "catalog" / "tools.yaml"
    data = yaml.safe_load(f.read_text())
    data["servers"][0]["tools"][0].update(hashes)
    f.write_text(yaml.safe_dump(data))


# ------------------------------------------------------------------ unset: nothing changes

def test_without_addons_the_catalog_is_only_the_repo(monkeypatch):
    monkeypatch.delenv(addons.ENV, raising=False)
    addons._cached.cache_clear()
    assert addons.active() == ()
    raw = yaml.safe_load(settings.TOOLS_FILE.read_text())
    assert [s.id for s in load_tool_catalog(settings.TOOLS_FILE).servers] == [s["id"] for s in raw["servers"]]
    assert len(TaskRegistry()) == len(list(settings.TASKS_DIR.glob("*/task.yaml")))


# ------------------------------------------------------------------ set: everything joins

def test_an_addon_adds_its_servers_tasks_and_recipes(toy):
    declared = load_tool_catalog(settings.TOOLS_FILE)
    assert declared.server("toy").module == MODULE
    assert "toy-01" in {t.id for t in TaskRegistry().all()}
    recipes = load_recipes(settings.RECIPES_DIR)
    assert recipes["v2-toy"].tools.mcp_servers == ["toy"]            # extends a built-in recipe with `base:`
    assert recipes["v2-toy"].variant == recipes["v2-base"].variant


def test_an_explicit_root_or_file_sees_no_addons(toy, tmp_path):
    other = tmp_path / "tools.yaml"
    other.write_text(settings.TOOLS_FILE.read_text())
    assert "toy" not in {s.id for s in load_tool_catalog(other).servers}
    assert "toy" not in {s.id for s in load_tool_catalog(settings.TOOLS_FILE, addons=False).servers}
    assert "toy-01" not in {t.id for t in TaskRegistry(settings.TASKS_DIR).all()}


def test_the_addon_task_seeds_its_reference_read_only(toy):
    task = TaskRegistry().get("toy-01")
    with LocalSandbox("addon-ref") as sb:
        sb.reset(task)
        mounted = sb.ref_dir / REF_FILE
        assert sb.env()["HF_REFERENCE"] == str(mounted) and not sb.ref_dir.is_relative_to(sb.workspace)
        assert json.loads(mounted.read_text()) == REFERENCE
        if os.name == "posix":
            assert stat.S_IMODE(mounted.stat().st_mode) == 0o444
        before = sb.ref_dir.stat().st_ino
        sb.reset(task)                              # the same folder, refilled: a Docker bind mount stays valid
        assert sb.ref_dir.stat().st_ino == before and json.loads(mounted.read_text()) == REFERENCE


async def test_an_addon_server_runs_from_its_own_folder(toy):
    declared = load_tool_catalog(settings.TOOLS_FILE)
    with LocalSandbox("addon-run") as sb:
        sb.reset(TaskRegistry().get("toy-01"))
        for launcher in (sb.launch, None):             # through the sandbox, and the client's own launch
            async with MCPSessionPool(declared, ["toy"], sb.env(), launcher) as pool:
                ok = await pool.call("toy.lookup", {"key": "beta"})
                no = await pool.call("toy.lookup", {"key": "gamma"})
            assert json.loads(ok.text) == {"key": "beta", "value": "two"}
            assert no.is_error and "UNKNOWN_KEY: gamma" in no.text


def test_the_server_path_is_added_only_for_addon_servers(toy, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", "/elsewhere")
    env = addons.server_env(MODULE)
    assert env["PYTHONPATH"].split(os.pathsep) == [str((toy / "src").resolve()), "/elsewhere"]
    assert addons.server_env("harness_factory.env.mcp.impl.sql_server") == {}


async def test_an_addon_server_passes_conformance_from_its_own_fixture(toy):
    await freeze_toy(toy)
    declared = load_tool_catalog(settings.TOOLS_FILE)
    assert conformance.fixture_path("toy") == toy / "catalog" / "conformance" / "toy.yaml"
    report = await conformance.check_server(declared, "toy", sandbox_kind="local")
    assert not report.failures(), report.failures()


def test_a_fixture_for_an_undeclared_addon_server_is_stale(toy):
    (toy / "catalog" / "conformance" / "gone.yaml").write_text("server: gone\n")
    stale = conformance.stale_fixtures(load_tool_catalog(settings.TOOLS_FILE))
    assert stale == [toy / "catalog" / "conformance" / "gone.yaml"]


def test_docker_refuses_addon_servers(toy, tmp_path):
    sb = DockerSandbox.__new__(DockerSandbox)
    sb.label, sb.image, sb.container = "x", "img", "hf-x"
    sb.workspace, sb.db_path = tmp_path / "workspace", tmp_path / "state" / "task.sqlite"
    spec = load_tool_catalog(settings.TOOLS_FILE).server("toy")
    with pytest.raises(HarnessError, match="local sandbox only"):
        sb.launch(spec)
    assert f"{tmp_path / 'state' / 'reference'}:/state/reference:ro" in sb.run_args()


def test_tools_freeze_writes_the_addon_hashes_into_the_addon_file(toy):
    from harness_factory.cli.main import app
    builtin, mtime = settings.TOOLS_FILE.read_text(), settings.TOOLS_FILE.stat().st_mtime_ns
    first = CliRunner().invoke(app, ["tools", "freeze", "--check"])
    assert first.exit_code == 1 and "toy.lookup" in first.output             # never frozen: stale
    done = CliRunner().invoke(app, ["tools", "freeze"])
    assert done.exit_code == 0, done.output
    assert settings.TOOLS_FILE.read_text() == builtin                         # the repo's file is untouched,
    assert settings.TOOLS_FILE.stat().st_mtime_ns == mtime                    # not even rewritten
    [tool] = yaml.safe_load((toy / "catalog" / "tools.yaml").read_text())["servers"][0]["tools"]
    assert tool["schema_hash"] and tool["description_hash"]
    assert CliRunner().invoke(app, ["tools", "freeze", "--check"]).exit_code == 0


# ------------------------------------------------------------------ prompts

def add_stage_recipe(root: Path, prompt: str = "stage/toy_lookup") -> None:
    (root / "prompts" / "stage").mkdir(parents=True, exist_ok=True)
    (root / "prompts" / "stage" / "toy_lookup.j2").write_text("Look the value up with {{ tools | join(', ') }}.")
    (root / "catalog" / "recipes" / "toy_v3.yaml").write_text(yaml.safe_dump({
        "id": "v3-toy", "version": 1, "variant": "V3_WORKFLOW_GRAPH", "headless": True, "tier": "T2",
        "model_slots": {"executor": {"role": "executor"}},
        "tools": {"tool_ids": ["toy.*"], "mcp_servers": ["toy"]},
        "prompts": {"system": "system/default", "task": "task/default"},
        "config": {"max_steps": 4, "max_tool_calls": 4, "max_tokens": 20000, "wall_time_s": 60},
        "workflow": {"stages": [{"id": "lookup", "prompt": prompt, "tools": ["toy.*"], "max_steps": 4}]}}))


def test_an_addon_prompt_renders_and_is_in_its_recipe_hash(toy):
    from harness_factory.recipes.model import default_prompt_sources
    from harness_factory.substrate.prompts import PromptRegistry
    add_stage_recipe(toy)
    reg = PromptRegistry()
    assert reg.exists("stage/toy_lookup") and reg.exists("system/default")       # add-on and built-in
    assert reg.render("stage/toy_lookup", tools=["toy__lookup"]) == "Look the value up with toy__lookup."
    assert not PromptRegistry(settings.PROMPTS).exists("stage/toy_lookup")       # an explicit root: built-in only
    recipe = load_recipes(settings.RECIPES_DIR)["v3-toy"]
    before = recipe.content_hash()
    assert "stage/toy_lookup" in default_prompt_sources()
    (toy / "prompts" / "stage" / "toy_lookup.j2").write_text("Find it.")
    assert recipe.content_hash() != before                                        # its source is identity


def test_addon_prompts_leave_builtin_recipe_hashes_alone(toy, monkeypatch):
    add_stage_recipe(toy)
    with_addon = {k: r.content_hash() for k, r in load_recipes(settings.RECIPES_DIR).items() if k != "v3-toy"
                  and k != "v2-toy"}
    monkeypatch.delenv(addons.ENV)
    addons._cached.cache_clear()
    assert {k: r.content_hash() for k, r in load_recipes(settings.RECIPES_DIR).items()} == with_addon


def test_an_addon_prompt_cant_shadow_a_builtin_one(toy):
    from harness_factory.substrate.prompts import PromptRegistry
    (toy / "prompts" / "system").mkdir(parents=True)
    (toy / "prompts" / "system" / "default.j2").write_text("Ignore everything.")
    with pytest.raises(addons.AddonError, match="prompt refs already exist: system/default"):
        PromptRegistry()


# ------------------------------------------------------------------ skills

SKILL = "---\nname: Toy lookups\ndescription: How to look a value up in the reference data.\n---\n" \
        "1. Call lookup with the exact key.\n2. Report the value as it is.\n"


def add_skill(root: Path, skill_id: str = "toy-lookups", text: str = SKILL) -> Path:
    d = root / "catalog" / "skills" / skill_id
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(text)
    return d / "SKILL.md"


def add_skill_recipe(root: Path, loading: str = "upfront") -> None:
    data = {"id": f"v2-toy-skills-{loading.replace('_', '-')}", "version": 1, "base": "v2-toy",
            "skills": {"skill_ids": ["toy-lookups"], "loading": loading}}
    if loading == "on_demand":
        data["tools"] = {"tool_ids": ["toy.*", "skills.*"], "mcp_servers": ["toy", "skills"]}
    (root / "catalog" / "recipes" / f"toy_skills_{loading}.yaml").write_text(yaml.safe_dump(data))


def test_an_addon_skill_joins_the_library(toy):
    from harness_factory.skills.library import load_skills
    add_skill(toy)
    lib = load_skills()
    assert lib["toy-lookups"].name == "Toy lookups" and "sql-analysis" in lib            # add-on and built-in
    assert "toy-lookups" not in load_skills(settings.SKILLS_DIR)                         # an explicit root: built-in only


def test_without_addons_the_library_is_only_the_repo(toy, monkeypatch):
    from harness_factory.skills.library import load_skills
    add_skill(toy)
    monkeypatch.delenv(addons.ENV)
    addons._cached.cache_clear()
    assert "toy-lookups" not in load_skills()


def test_a_recipe_using_an_addon_skill_validates_and_hashes_its_text(toy):
    from harness_factory.recipes.validate import validate_recipe
    skill = add_skill(toy)
    add_skill_recipe(toy)
    recipe = load_recipes(settings.RECIPES_DIR)["v2-toy-skills-upfront"]
    issues = validate_recipe(recipe, load_tool_catalog(settings.TOOLS_FILE))
    assert not [i for i in issues if i.rule.startswith("skill")]                       # (the toy isn't frozen)
    before = recipe.content_hash()
    skill.write_text(SKILL.replace("exact key", "key as given"))
    assert recipe.content_hash() != before                                               # its text is identity


def test_addon_skills_leave_builtin_recipe_hashes_alone(toy, monkeypatch):
    add_skill(toy)
    add_skill_recipe(toy)
    mine = {"v2-toy", "v2-toy-skills-upfront"}
    with_addon = {k: r.content_hash() for k, r in load_recipes(settings.RECIPES_DIR).items() if k not in mine}
    monkeypatch.delenv(addons.ENV)
    addons._cached.cache_clear()
    assert {k: r.content_hash() for k, r in load_recipes(settings.RECIPES_DIR).items()} == with_addon


def test_an_addon_skill_reaches_the_model_upfront(toy):
    from harness_factory.substrate.prompts import PromptAssembler
    add_skill(toy)
    add_skill_recipe(toy)
    recipe = load_recipes(settings.RECIPES_DIR)["v2-toy-skills-upfront"]
    system = PromptAssembler().assemble(recipe, load_task(toy / "catalog" / "tasks" / "toy-01"), ["toy__lookup"])[0]
    assert "1. Call lookup with the exact key." in system["content"]


def test_an_addon_skill_is_installed_for_on_demand_loading(toy):
    from harness_factory.provision.provisioner import install_skills
    add_skill(toy)
    add_skill_recipe(toy, "on_demand")
    recipe = load_recipes(settings.RECIPES_DIR)["v2-toy-skills-on-demand"]
    with LocalSandbox("addon-skills") as sb:
        install_skills(sb, recipe)
        assert (sb.skills_dir / "toy-lookups" / "SKILL.md").read_text() == SKILL


def test_an_addon_skill_cant_shadow_a_builtin_one(toy):
    from harness_factory.skills.library import load_skills
    add_skill(toy, "sql-analysis", SKILL)
    with pytest.raises(addons.AddonError, match="skill ids already exist: sql-analysis"):
        load_skills()


def test_two_addons_cant_share_a_skill_id(tmp_path, use):
    from harness_factory.skills.library import load_skills
    a = make_addon(tmp_path / "a", addon_id="addon-a", servers={}, tools=[])
    b = make_addon(tmp_path / "b", addon_id="addon-b", servers={}, tools=[])
    for root in (a, b):
        (root / "catalog" / "tasks" / "toy-01" / "task.yaml").unlink()
        (root / "catalog" / "recipes" / "toy.yaml").unlink()
        add_skill(root)
    use(a, b)
    with pytest.raises(addons.AddonError, match="skill ids already exist: toy-lookups"):
        load_skills()


def test_the_snapshot_sees_addon_skills_with_the_factorys_hashes(toy):
    from harness_factory.authoring.snapshot import take
    skill = add_skill(toy)
    add_skill_recipe(toy)
    snap = take()
    assert snap["skills"]["toy-lookups"]["path"] == str(skill)
    recipe = load_recipes(settings.RECIPES_DIR)["v2-toy-skills-upfront"]
    assert snap["recipes"]["v2-toy-skills-upfront"]["hash"] == recipe.content_hash()
    assert not [e for e in snap["errors"] if e["kind"] == "skill"]


# ------------------------------------------------------------------ libraries

GIT = "git+https://example.invalid/lib.git@" + "a" * 40


def test_an_addon_library_joins_the_allow_list_for_its_servers_only(tmp_path, use):
    use(make_addon(tmp_path / "toy", servers={MODULE: ["toy-lib"]},
                   libraries=[{"dist": "toy-lib", "version": "0.1.0", "imports": "toylib", "source": GIT}]))
    assert deps.requirements(MODULE) == [f"toy-lib @ {GIT}"]
    assert deps.library("toy-lib").commit == "a" * 40
    assert f"toy-lib @ {GIT}" not in deps.image_requirements()                # the image carries built-ins only
    [problem] = deps.problems(("toy-lib",))
    assert "toy-lib is not installed" in problem
    with pytest.raises(HarnessError, match="Install them as its add-on says"):
        deps.require_server(MODULE, "toy")


def test_the_addon_library_pin_is_in_the_env_hash_of_its_server_only(tmp_path, use):
    use(make_addon(tmp_path / "toy", servers={MODULE: ["toy-lib"]},
                   libraries=[{"dist": "toy-lib", "version": "0.1.0", "imports": "toylib", "source": GIT}]))
    declared = load_tool_catalog(settings.TOOLS_FILE)
    with_toy, sql_only = declared.env_hash(["sql", "toy"], "m"), declared.env_hash(["sql"], "m")
    use(make_addon(tmp_path / "toy2", servers={MODULE: ["toy-lib"]},
                   libraries=[{"dist": "toy-lib", "version": "0.1.0", "imports": "toylib",
                               "source": GIT[:-40] + "b" * 40}]))
    declared = load_tool_catalog(settings.TOOLS_FILE)
    assert declared.env_hash(["sql", "toy"], "m") != with_toy
    assert declared.env_hash(["sql"], "m") == sql_only


def test_a_library_from_another_commit_is_refused(monkeypatch):
    lib = deps.Library("pypdf", deps.ALLOWED["pypdf"].version, "pypdf", source=GIT)
    monkeypatch.setitem(deps.ALLOWED, "pypdf", lib)
    monkeypatch.setattr(deps, "_installed_commit", lambda dist: "0" * 40)
    [problem] = deps.problems(("pypdf",))
    assert "not from the pinned commit aaaaaaaaaaaa" in problem
    monkeypatch.setattr(deps, "_installed_commit", lambda dist: "a" * 40)
    assert deps.problems(("pypdf",)) == []


# ------------------------------------------------------------------ an add-on only adds

def test_an_addon_cant_redeclare_a_builtin_server(tmp_path, use):
    use(make_addon(tmp_path / "toy", tools=[{"id": "sql", "transport": "stdio", "module": MODULE, "tools": []}]))
    with pytest.raises(addons.AddonError, match="'sql' is already declared"):
        load_tool_catalog(settings.TOOLS_FILE)


def test_an_addon_server_must_be_listed_in_addon_yaml(tmp_path, use):
    use(make_addon(tmp_path / "toy", servers={}))
    with pytest.raises(addons.AddonError, match="doesn't list under `servers`"):
        load_tool_catalog(settings.TOOLS_FILE)


def test_an_addon_cant_run_a_factory_module(tmp_path):
    root = make_addon(tmp_path / "toy", servers={"harness_factory.env.mcp.impl.sql_server": []})
    with pytest.raises(addons.AddonError, match="factory's own package"):
        addons.load_addon(root)


def test_an_addon_cant_change_a_builtin_pin(tmp_path, use):
    use(make_addon(tmp_path / "toy", servers={MODULE: ["pypdf"]},
                   libraries=[{"dist": "pypdf", "version": "1.0.0", "imports": "pypdf"}]))
    with pytest.raises(addons.AddonError, match="already pins"):
        load_tool_catalog(settings.TOOLS_FILE)


def test_an_addon_task_id_cant_repeat_a_builtin_one(tmp_path, use):
    root = make_addon(tmp_path / "toy")
    builtin = next(iter(TaskRegistry(settings.TASKS_DIR).all())).id
    data = yaml.safe_load((root / "catalog/tasks/toy-01/task.yaml").read_text())
    (root / "catalog/tasks/toy-01/task.yaml").write_text(yaml.safe_dump({**data, "id": builtin}))
    use(root)
    with pytest.raises(ValueError, match=f"duplicate task id {builtin}"):
        TaskRegistry()


def test_two_addons_cant_share_an_id_or_a_server(tmp_path):
    a, b = make_addon(tmp_path / "a"), make_addon(tmp_path / "b")
    with pytest.raises(addons.AddonError, match="listed twice"):
        addons.load(os.pathsep.join(map(str, (a, b))))
    c = make_addon(tmp_path / "c", addon_id="other")
    with pytest.raises(addons.AddonError, match="both declare toyaddon.server"):
        addons.load(os.pathsep.join(map(str, (a, c))))


def test_a_folder_without_addon_yaml_or_with_a_path_outside_is_refused(tmp_path):
    with pytest.raises(addons.AddonError, match="no addon.yaml"):
        addons.load(str(tmp_path))
    root = make_addon(tmp_path / "toy")
    (root / "addon.yaml").write_text(yaml.safe_dump({"id": "toy", "python_path": [".."]}))
    with pytest.raises(addons.AddonError, match="not a folder inside the add-on"):
        addons.load_addon(root)


# ------------------------------------------------------------------ the reference seed and identity

def test_the_reference_file_is_part_of_the_task_hash(toy):
    t = toy / "catalog" / "tasks" / "toy-01"
    before = load_task(t).content_hash()
    (t / "seed" / "reference.json").write_text(json.dumps({**REFERENCE, "alpha": 2}))
    assert load_task(t).content_hash() != before


def test_a_missing_reference_file_is_a_setup_error(toy):
    t = toy / "catalog" / "tasks" / "toy-01"
    (t / "seed" / "reference.json").unlink()
    with LocalSandbox("addon-missing") as sb, pytest.raises(HarnessError, match="is not a file"):
        sb.reset(load_task(t))


def test_a_task_without_a_reference_mounts_an_empty_folder():
    with LocalSandbox("addon-none") as sb:
        sb.reset(TaskRegistry(settings.TASKS_DIR).all()[0])
        assert not list(sb.ref_dir.iterdir())
