"""The code and git servers, the git seed, the tests_pass and git_log_contains checks (ADR 0012): what they run,
what they refuse, how they are confined and limited, and that adding them moved no existing identity."""

import asyncio
import json
import os
import sys
import time

import pytest

from harness_factory import settings
from harness_factory.env.mcp.impl import proc
from harness_factory.env.sandbox import LocalSandbox
from harness_factory.env.state import StateInspector
from harness_factory.mcp.pool import MCPSessionPool
from harness_factory.provision.provisioner import provision
from harness_factory.runner.runner import plan, run_one
from harness_factory.substrate.errors import HarnessError
from harness_factory.substrate.gateway import ScriptedBackend
from harness_factory.tasks.model import Task, WeightedCheck

# The seed commit of catalog/seeds/pricing: a function of the seed files and proc.GIT_SEED only, so the same on
# every machine. Editing the seed changes it (and the hash of every task using it).
PRICING_SEED_COMMIT = "310fc5f560515c79288ae3c1d2b045580cf7b132"


@pytest.fixture
def pricing(factory):
    with LocalSandbox("code-test") as sb:
        sb.reset(factory.tasks.get("code-02"))
        yield sb


def _write(sb, rel, text):
    p = sb.workspace / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def _json(res):
    assert not res.is_error, res.text
    return json.loads(res.text)


# ------------------------------------------------------------------ code server

async def test_run_tests_reports_exit_code_counts_and_output(factory, pricing):
    async with MCPSessionPool(factory.declared, ["code"], pricing.env()) as pool:
        whole = _json(await pool.call("code.run_tests", {}))
        assert whole["exit_code"] == 1 and whole["counts"] == {"passed": 3, "failed": 2, "errors": 0, "skipped": 0}
        assert "test_exactly_ten_items_is_a_bulk_order" in whole["output"] and not whole["timed_out"]
        one = _json(await pool.call("code.run_tests", {"path": "tests/test_discount.py::test_small_orders_get_no_discount"}))
        assert one["exit_code"] == 0 and one["counts"]["passed"] == 1
        picked = _json(await pool.call("code.run_tests", {"path": "tests", "args": "-k whole -q"}))
        assert picked["exit_code"] == 0 and picked["counts"]["passed"] == 1
        none = _json(await pool.call("code.run_tests", {"path": "pricing"}))
        assert none["exit_code"] == 5                        # nothing collected


async def test_run_tests_leaves_no_trace_in_the_workspace(factory, pricing):
    insp = StateInspector(pricing.workspace, pricing.db_path)
    before = insp.capture()
    async with MCPSessionPool(factory.declared, ["code"], pricing.env()) as pool:
        await pool.call("code.run_tests", {})
    assert StateInspector.diff(before, insp.capture()).is_empty     # no __pycache__, no .pytest_cache


async def test_code_refuses_paths_and_options_that_reach_outside(factory, pricing):
    async with MCPSessionPool(factory.declared, ["code"], pricing.env()) as pool:
        for args in ({"path": "../state"}, {"path": "/etc"}, {"path": "nope.py"}):
            res = await pool.call("code.run_tests", args)
            assert res.is_error and ("escapes" in res.text or "relative" in res.text or "no such" in res.text)
        for opt in ("--rootdir=/", "--basetemp=/tmp/x", "-p pytester", "-c /dev/null", "--junitxml=../x.xml"):
            res = await pool.call("code.run_tests", {"args": opt})
            assert res.is_error and "not allowed" in res.text, opt
        assert (await pool.call("code.run_tests", {"args": "-k"})).is_error           # -k needs a value
        assert (await pool.call("code.run_python", {"path": "../state/task.sqlite"})).is_error
        notpy = await pool.call("code.run_python", {"path": "README.md"})
        assert notpy.is_error and "not a Python file" in notpy.text


async def test_run_python_runs_a_script_with_the_workspace_importable(factory, pricing):
    _write(pricing, "scripts/quote.py", "import sys\nfrom pricing import bulk_discount, vat\n"
                                        "print('discount', bulk_discount(float(sys.argv[1]), int(sys.argv[2])))\n"
                                        "print('vat', vat(100.0))\nsys.exit(3)\n")
    async with MCPSessionPool(factory.declared, ["code"], pricing.env()) as pool:
        out = _json(await pool.call("code.run_python", {"path": "scripts/quote.py", "args": "200 12"}))
    assert out["exit_code"] == 3 and "discount 10.0" in out["output"] and "vat 5.0" in out["output"]


async def test_code_runs_with_a_scrubbed_environment(factory, pricing):
    _write(pricing, "env.py", "import os\nprint(sorted(os.environ))\n")
    env = {**pricing.env(), "SECRET_TOKEN": "s3cret"}             # the server has it; the code must not
    async with MCPSessionPool(factory.declared, ["code"], env) as pool:
        out = _json(await pool.call("code.run_python", {"path": "env.py"}))["output"]
    import ast
    keys = set(ast.literal_eval(out.strip().splitlines()[-1]))
    assert not keys & {"SECRET_TOKEN", "HF_DB", "HF_WORKSPACE", "HF_GIT_DIR", "HF_SKILLS"}
    assert {"PATH", "HOME", "PYTHONDONTWRITEBYTECODE", "PYTEST_DISABLE_PLUGIN_AUTOLOAD"} <= keys


async def test_code_time_limit_kills_the_process_and_its_children(factory, pricing):
    _write(pricing, "hang.py", "import subprocess, sys, time\n"
                               "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
                               "open('child.pid', 'w').write(str(child.pid))\nprint('started', flush=True)\n"
                               "time.sleep(60)\n")
    env = {**pricing.env(), "HF_CODE_TIMEOUT_S": "1.5"}
    t0 = time.monotonic()
    async with MCPSessionPool(factory.declared, ["code"], env) as pool:
        out = _json(await pool.call("code.run_python", {"path": "hang.py"}))
    assert time.monotonic() - t0 < 20
    assert out["timed_out"] and out["exit_code"] is None and "time limit" in out["output"] and "started" in out["output"]
    if os.name == "posix":
        pid = int((pricing.workspace / "child.pid").read_text())
        for _ in range(50):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            await asyncio.sleep(0.1)
        else:
            pytest.fail("the script's child outlived the time limit")


async def test_code_output_is_capped(factory, pricing):
    _write(pricing, "loud.py", "print('FIRST')\nfor i in range(200_000):\n    print('x' * 50, i)\nprint('LAST')\n")
    async with MCPSessionPool(factory.declared, ["code"], pricing.env()) as pool:
        out = _json(await pool.call("code.run_python", {"path": "loud.py"}))["output"]
    assert len(out) < 7_000 and out.rstrip().endswith("LAST") and "cut" in out


def test_capped_buffer_keeps_head_and_tail():
    ran = proc.run([sys.executable, "-c", "print('H' * 10); print('m' * 100_000); print('TAIL')"], ".", 30,
                   proc.code_env(str(settings.VAR)))
    assert ran.exit_code == 0 and ran.output.startswith("HHHHHHHHHH") and ran.output.rstrip().endswith("TAIL")
    assert ran.dropped > 80_000 and len(ran.output) < proc.HEAD_BYTES + proc.TAIL_BYTES + 100


# ------------------------------------------------------------------ git seed

def test_git_seed_is_deterministic_and_outside_the_workspace(factory, tmp_path):
    task = factory.tasks.get("code-02")
    seeds = []
    for label in ("a", "b"):
        with LocalSandbox(f"git-{label}") as sb:
            sb.reset(task)
            seeds.append(sb.git_seed)
            assert not (sb.workspace / ".git").exists() and (sb.git_dir / "HEAD").is_file()
            config = (sb.git_dir / "config").read_text()
            assert "worktree" not in config                     # the work tree is passed per call (docker paths)
            sb.reset(task)                                       # a reset starts the repository over
            assert sb.git_seed == seeds[-1]
    assert seeds == [PRICING_SEED_COMMIT] * 2


def test_tasks_without_git_get_no_repository(factory):
    with LocalSandbox("nogit") as sb:
        sb.reset(factory.tasks.get("fs-01"))
        assert sb.git_seed is None and not sb.git_dir.exists()


def test_missing_git_is_a_sandbox_error(factory, monkeypatch):
    monkeypatch.setattr(proc.shutil, "which", lambda name: None)
    with LocalSandbox("nogitbin") as sb, pytest.raises(HarnessError, match="needs a git repository"):
        sb.reset(factory.tasks.get("code-02"))


# ------------------------------------------------------------------ git server

async def test_git_status_diff_commit_log(factory, pricing):
    async with MCPSessionPool(factory.declared, ["git"], pricing.env()) as pool:
        assert (await pool.call("git.status", {})).text.strip() == "## main"
        nothing = await pool.call("git.commit", {"message": "Fix nothing"})
        assert nothing.is_error and "nothing to commit" in nothing.text
        _write(pricing, "pricing/discount.py", (pricing.workspace / "pricing/discount.py").read_text()
               .replace("items > BULK", "items >= BULK"))
        _write(pricing, "notes.txt", "scratch\n")
        status = (await pool.call("git.status", {})).text
        assert " M pricing/discount.py" in status and "?? notes.txt" in status
        diff = (await pool.call("git.diff", {"path": "pricing"})).text
        assert "-    if items > BULK_MIN_ITEMS:" in diff and "+    if items >= BULK_MIN_ITEMS:" in diff
        assert (await pool.call("git.diff", {"staged": True})).text == "no changes"
        done = await pool.call("git.commit", {"message": "Fix the bulk threshold\n\nTen items is bulk."})
        assert not done.is_error and "on main: Fix the bulk threshold (2 files)" in done.text
        log = (await pool.call("git.log", {"n": 5})).text.splitlines()
        assert [line.split(" ", 2)[2] for line in log] == ["Fix the bulk threshold", "Initial commit"]
        assert log[0].split()[1] == "2026-01-01"                 # fixed clock: one minute after the seed
        assert (await pool.call("git.status", {})).text.strip() == "## main"
        escape = await pool.call("git.diff", {"path": "../state"})
        assert escape.is_error and "escapes" in escape.text
    assert not (pricing.workspace / ".git").exists()


async def test_git_commits_are_reproducible(factory):
    shas = []
    for label in ("a", "b"):
        with LocalSandbox(f"git-repro-{label}") as sb:
            sb.reset(factory.tasks.get("code-02"))
            _write(sb, "README.md", "changed\n")
            async with MCPSessionPool(factory.declared, ["git"], sb.env()) as pool:
                await pool.call("git.commit", {"message": "Edit the readme"})
            shas.append(proc.git_ok(["rev-parse", "HEAD"], git_dir=sb.git_dir, work_tree=sb.workspace).strip())
    assert shas[0] == shas[1] != PRICING_SEED_COMMIT


async def test_git_branch_and_checkout(factory, pricing):
    async with MCPSessionPool(factory.declared, ["git"], pricing.env()) as pool:
        made = await pool.call("git.branch", {"name": "fix/vat-rounding"})
        assert not made.is_error and "created branch fix/vat-rounding" in made.text
        assert (await pool.call("git.status", {})).text.strip() == "## main"      # branch doesn't switch
        assert not (await pool.call("git.checkout", {"name": "fix/vat-rounding"})).is_error
        assert (await pool.call("git.status", {})).text.strip() == "## fix/vat-rounding"
        for bad in ("-D", "--force", "a..b", "../x", "x.lock", "has space", ""):
            res = await pool.call("git.branch", {"name": bad})
            assert res.is_error and "invalid branch name" in res.text, bad
        missing = await pool.call("git.checkout", {"name": "nope"})
        assert missing.is_error and "no such branch" in missing.text
        assert (await pool.call("git.checkout", {"name": "HEAD~1"})).is_error        # branches only


async def test_git_ignores_hooks_and_commands_in_the_repository_config(factory, pricing):
    """Code in the sandbox can write the git directory. Hooks, an fsmonitor command and a pager set there must
    never run: the server's command-line config wins over the repository's."""
    marker = pricing.root / "pwned"
    hook = f"#!/bin/sh\necho hook > {marker}\n"
    for name in ("post-commit", "pre-commit", "commit-msg", "post-checkout"):
        (pricing.git_dir / "hooks" / name).write_text(hook)
        (pricing.git_dir / "hooks" / name).chmod(0o755)
    config = pricing.git_dir / "config"
    config.write_text(config.read_text() + f"[core]\n\tfsmonitor = \"sh -c 'echo fsmonitor > {marker}'\"\n"
                      f"\tpager = \"sh -c 'echo pager > {marker}'\"\n[log]\n\tshowSignature = true\n"
                      f"[gpg]\n\tprogram = /bin/false\n")
    async with MCPSessionPool(factory.declared, ["git"], pricing.env()) as pool:
        _write(pricing, "README.md", "changed\n")
        assert not (await pool.call("git.status", {})).is_error
        assert not (await pool.call("git.commit", {"message": "Edit"})).is_error
        assert not (await pool.call("git.log", {})).is_error
        assert not (await pool.call("git.branch", {"name": "side"})).is_error
        assert not (await pool.call("git.checkout", {"name": "side"})).is_error
    assert not marker.exists()


async def test_git_in_a_task_without_a_repository(factory):
    with LocalSandbox("git-none") as sb:
        sb.reset(factory.tasks.get("fs-01"))
        async with MCPSessionPool(factory.declared, ["git"], sb.env()) as pool:
            res = await pool.call("git.status", {})
    assert res.is_error and "not a git repository" in res.text


# ------------------------------------------------------------------ checks

def _check(**kw):
    return WeightedCheck(id="c", weight=1, **kw)


def test_new_check_kinds_validate_their_fields(factory):
    assert _check(kind="tests_pass").path is None                       # default: the whole workspace
    with pytest.raises(ValueError, match="not both"):
        _check(kind="tests_pass", path="tests", text="def test_x(): pass")
    with pytest.raises(ValueError, match="needs pattern"):
        _check(kind="git_log_contains")
    with pytest.raises(ValueError, match="bad pattern"):
        _check(kind="git_log_contains", pattern="(unclosed")
    with pytest.raises(ValueError, match="`ref` is for git checks"):
        _check(kind="file_absent", path="x", ref="main")
    data = factory.tasks.get("code-02").model_dump(mode="json")
    data["env_seed"]["git"] = False
    with pytest.raises(ValueError, match="need `env_seed.git: true`"):
        Task.model_validate(data)


def test_adding_code_and_git_moved_no_existing_identity(factory):
    d = factory.declared
    assert d.env_hash(factory.recipes["v2-base"].tools.mcp_servers, "openai/gpt-oss-120b") == "a55c2fb739f1"
    assert factory.recipes["v2-base"].content_hash() == "79abd03818943f65"
    from harness_factory.hashing import content_hash

    def before_this_pr(t):                    # the task hash formula as it was: no `git`, no `ref`
        data = t.model_dump(mode="json", exclude={"status", "split", "tags"})
        data["env_seed"].pop("git")
        for k in ("biz_state",):              # added by a later PR (#46), also omitted when unset
            if data["env_seed"].get(k) is None:
                data["env_seed"].pop(k, None)
        for c in data["oracle"]["checks"]:
            c.pop("ref")
        data["seed_hash"] = t.env_seed.data_hash(t.source_dir)
        return content_hash(data)

    for t in factory.tasks.all(include_held_out=True):
        if not t.env_seed.git:
            assert t.content_hash() == before_this_pr(t), t.id
    code = factory.tasks.get("code-02")
    flipped = code.model_copy(deep=True)
    flipped.env_seed.git = False
    assert flipped.content_hash() != code.content_hash()                # set, the repository is part of identity


async def _graded(factory, task_id, sb_setup=None):
    """Grade a sandbox after sb_setup changed it, with the task's own oracle (no model run)."""
    from harness_factory.grading.base import GradeContext
    from harness_factory.grading.state import StateDiffGrader
    from harness_factory.runner.spec import RunKey
    from harness_factory.shapes.result import RunResult
    task = factory.tasks.get(task_id)
    with LocalSandbox("grade") as sb:
        sb.reset(task)
        insp = StateInspector(sb.workspace, sb.db_path)
        before = insp.capture()
        if sb_setup:
            await sb_setup(sb)
        after = insp.capture()
        rk = RunKey(task_version=task.key(), task_hash=task.content_hash(), recipe_hash="x", canonical_model="m",
                    provider="p")
        ctx = GradeContext(task=task, result=RunResult(run_key=rk, output="", finished=True), before=before,
                           after=after, diff=StateInspector.diff(before, after), workspace=sb.workspace,
                           db_path=sb.db_path, spans=[], max_steps=10, sandbox=sb)
        grade = StateDiffGrader().grade(ctx)
    return {c.id: (c.passed, c.detail) for c in grade.checks}


async def test_tests_pass_ignores_a_conftest_and_config_the_agent_wrote(factory):
    """A conftest.py that reports every test as passed, or pytest options in the workspace, don't change the
    grade: the grader runs with --noconftest and an empty config of its own."""
    async def cheat(sb):
        _write(sb, "conftest.py", "import pytest\n\n@pytest.hookimpl(hookwrapper=True)\n"
                                  "def pytest_runtest_makereport(item, call):\n    out = yield\n"
                                  "    out.get_result().outcome = 'passed'\n")
        _write(sb, "pytest.ini", "[pytest]\naddopts = -k small\n")
        _write(sb, "pytest.py", "import sys\nsys.exit(0)\n")            # not importable as pytest: -P
    checks = await _graded(factory, "code-02", cheat)
    assert checks["tests"][0] is False and "1 failed" in checks["tests"][1]
    assert checks["hidden_tests"][0] is False


async def test_tests_pass_times_out(factory, monkeypatch):
    monkeypatch.setattr(proc, "GRADE_TIMEOUT_S", 1.5)
    async def hang(sb):
        _write(sb, "pricing/discount.py", "import time\ntime.sleep(60)\n")
    t0 = time.monotonic()
    checks = await _graded(factory, "code-02", hang)
    assert time.monotonic() - t0 < 30
    assert checks["tests"] == (False, "tests timed out after 1.5 s")


async def test_git_log_contains_follows_ref_path_and_negate(factory):
    async def work(sb):
        async with MCPSessionPool(factory.declared, ["git"], sb.env()) as pool:
            await pool.call("git.branch", {"name": "fix/vat-rounding"})
            await pool.call("git.checkout", {"name": "fix/vat-rounding"})
            _write(sb, "README.md", "unrelated\n")
            await pool.call("git.commit", {"message": "Touch the readme"})
    checks = await _graded(factory, "code-03", work)
    assert checks["on_branch"] == (False, "1 new commit(s) on fix/vat-rounding, 0 matching")   # not tax.py
    assert checks["main_untouched"][0] is True
    nothing = await _graded(factory, "code-03")
    assert nothing["on_branch"] == (False, "no ref 'fix/vat-rounding'")
    assert nothing["main_untouched"] == (True, "0 new commit(s) on main, 0 matching (negated)")


def test_probe_failure_is_the_environments_fault(factory):
    with LocalSandbox("probe") as sb:
        sb.reset(factory.tasks.get("code-02"))
        with pytest.raises(HarnessError, match="grading probe"):
            sb.probe({"op": "nope"}, 10)


# ------------------------------------------------------------------ runs, plans and the local-code opt-in

async def test_scripted_runs_solve_the_code_tasks_with_a_clean_trajectory(factory):
    for task_id in ("code-02", "code-03"):
        rec = await run_one(factory, factory.tasks.get(task_id), factory.recipes["v2-code"], "scripted/replay",
                            "scripted")
        assert rec.status.value == "SUCCESS", rec.grades
        assert rec.scores.outcome == 1.0 and rec.scores.trajectory == 1.0   # test -> fix -> test is not waste
        assert rec.task_class == "KNOWN_PATH|REVERSIBLE|R2"


async def test_rerunning_tests_with_nothing_changed_is_waste(factory):
    call = {"tool_calls": [{"name": "code__run_tests", "arguments": {"path": "tests/test_discount.py"}}]}
    rec = await run_one(factory, factory.tasks.get("code-02"), factory.recipes["v2-code"], "scripted/replay",
                        "scripted", backend=ScriptedBackend([call, call, {"content": "done"}]))
    traj = next(g for g in rec.grades if g["grader_id"] == "trajectory")
    assert traj["score"] == 0.5 and "wasted=1" in traj["detail"]


def test_campaigns_plan_only_recipes_that_reach_the_task_tools(factory):
    base = {s.task_id for s in plan(factory, "v2-base", "scripted", ["scripted/replay"])}
    code = {s.task_id for s in plan(factory, "v2-code", "scripted", ["scripted/replay"])}
    visible = {t.id for t in factory.tasks.all()}
    biz = {t.id for t in factory.tasks.all() if any(p.startswith("biz.") for p in t.allowed_tools)}
    assert {"code-02", "code-03"} <= code == visible - biz              # v2-code doesn't open biz (#46)
    assert base == visible - {"code-02", "code-03"} - biz                # every other task, as before


async def test_real_models_need_docker_or_an_opt_in_to_run_code_locally(factory, monkeypatch):
    monkeypatch.setattr(settings, "SANDBOX", "local")
    monkeypatch.delenv("HF_ALLOW_LOCAL_CODE", raising=False)
    kw = dict(declared=factory.declared, providers=factory.providers, ledger=factory.ledger)
    with pytest.raises(HarnessError, match="HF_ALLOW_LOCAL_CODE"):
        async with provision(factory.tasks.get("code-02"), factory.recipes["v2-code"], "gpt-oss-120b", "groq", **kw):
            pass
    with pytest.raises(HarnessError, match="HF_ALLOW_LOCAL_CODE"):              # a tests_pass check runs code too
        async with provision(factory.tasks.get("code-02"), factory.recipes["v2-base"], "gpt-oss-120b", "groq", **kw):
            pass
    monkeypatch.setenv("HF_ALLOW_LOCAL_CODE", "1")
    async with provision(factory.tasks.get("code-02"), factory.recipes["v2-code"], "gpt-oss-120b", "groq",
                         **kw) as p:
        assert p.sandbox.git_seed == PRICING_SEED_COMMIT


def test_server_modules_import_only_mcp_the_stdlib_and_each_other():
    """CLAUDE.md, "Isolation": the sandbox image has only mcp, pytest (run as a program) and these modules."""
    import ast
    from pathlib import Path

    from harness_factory.env.mcp.impl import proc as impl_proc
    impl = Path(impl_proc.__file__).parent
    for module in impl.glob("*.py"):
        for node in ast.walk(ast.parse(module.read_text())):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else \
                [node.module] if isinstance(node, ast.ImportFrom) and node.module else []
            for n in names:
                assert (n.split(".")[0] in sys.stdlib_module_names or n.split(".")[0] == "mcp"
                        or n.startswith("harness_factory.env.mcp.impl")), f"{module.name} imports {n}"
