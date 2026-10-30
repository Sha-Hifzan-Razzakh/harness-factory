"""Operational hardening (review findings O-02, O-03, O-05, O-06, L-01..L-04, L-08..L-10)."""

import asyncio
import json
import os
import subprocess
import sys
import textwrap
import threading

import pytest
from typer.testing import CliRunner

from harness_factory import settings
from harness_factory.env.faults import load_fault_profiles
from harness_factory.env.sandbox import LocalSandbox
from harness_factory.grading.state import _same
from harness_factory.mcp.pool import MCPSessionPool
from harness_factory.mlops.tracker import ExperimentTracker
from harness_factory.observability.tracer import Tracer
from harness_factory.providers.catalog import load_provider_catalog
from harness_factory.providers.quota import QuotaLedger
from harness_factory.recipes.model import ModelSlot
from harness_factory.runner.runner import (
    CampaignError,
    CampaignResult,
    RunSpec,
    plan,
    run_campaign,
    run_one,
)
from harness_factory.substrate.gateway import ModelGateway

BUCKET = "groq|gpt-oss-120b"


# ---- O-02: the MLflow mirror never turns a stored run into an error, and runs off the event loop -------------

class _DownTracker(ExperimentTracker):
    def __init__(self):
        super().__init__()
        self.enabled = True
        self.threads = []

    def log(self, rec, recipe):
        self.threads.append(threading.get_ident())
        raise ConnectionError("mlflow down")


async def test_a_mirror_failure_is_a_warning_not_a_run_error(factory, caplog):
    factory.tracker = _DownTracker()
    specs = plan(factory, "v2-base", "scripted", ["scripted/replay"], task_ids=["fs-01"])
    res = await run_campaign(factory, specs, 1)
    assert res.errors == [] and [r.status.value for r in res.records] == ["SUCCESS"]
    assert [r["status"] for r in factory.store.rows()] == ["SUCCESS"]
    assert factory.tracker.failures == 1
    assert "MLflow mirror failed" in caplog.text
    assert factory.tracker.threads and factory.tracker.threads[0] != threading.get_ident()   # not on the loop


# ---- O-03: a grader typo fails before any model call -----------------------------------------------------------

def _with_typo(factory, task_id="fs-01"):
    bad = factory.tasks.get(task_id).model_copy(update={"grader_ids": ["state_dif", "trajectory"]})
    factory.tasks._tasks[task_id] = bad
    return bad


async def test_unknown_grader_fails_before_any_model_call(factory):
    bad = _with_typo(factory)
    with pytest.raises(ValueError, match="unknown grader.*'state_dif'"):
        await run_one(factory, bad, factory.recipes["v2-base"], "scripted/replay", "scripted")
    assert factory.store.rows() == []
    assert not factory.ledger._totals                                   # no quota slot was taken
    with pytest.raises(ValueError, match="unknown grader"):
        plan(factory, "v2-base", "scripted", ["scripted/replay"], task_ids=["fs-01"])


def test_validate_reports_an_unknown_grader(factory, monkeypatch):
    from harness_factory.cli import main
    _with_typo(factory)
    monkeypatch.setattr(main, "_factory", lambda store=True: factory)
    out = CliRunner().invoke(main.app, ["validate"])
    assert out.exit_code == 1
    assert "fs-01@1: unknown grader(s) state_dif" in out.output


# ---- O-05: concurrent processes add up their usage instead of overwriting it ----------------------------------

def test_two_ledgers_on_one_file_see_each_others_usage(tmp_path):
    catalog = load_provider_catalog(settings.PROVIDERS_FILE)
    state = tmp_path / "quota.json"
    a = QuotaLedger(catalog, jitter_s=0, state_path=state)
    b = QuotaLedger(catalog, jitter_s=0, state_path=state)
    a.charge(BUCKET, tokens=100)
    b.charge(BUCKET, tokens=50)
    a.charge(BUCKET, tokens=1)
    day = json.loads(state.read_text())["providers"][BUCKET]["day"]
    assert (day["requests"], day["tokens"]) == (3, 151)
    lim = catalog.limits(BUCKET)
    assert a.remaining(BUCKET)["rpd"] == lim.rpd - 3


def test_concurrent_processes_never_lose_an_update(tmp_path):
    state = tmp_path / "quota.json"
    script = tmp_path / "charge.py"
    script.write_text(textwrap.dedent(f"""
        from pathlib import Path
        from harness_factory import settings
        from harness_factory.providers.catalog import load_provider_catalog
        from harness_factory.providers.quota import QuotaLedger
        led = QuotaLedger(load_provider_catalog(settings.PROVIDERS_FILE), jitter_s=0, state_path=Path({str(state)!r}))
        for _ in range(100):
            led.charge({BUCKET!r}, tokens=1)
    """))
    procs = [subprocess.Popen([sys.executable, str(script)]) for _ in range(3)]
    assert [p.wait(timeout=120) for p in procs] == [0, 0, 0]
    day = json.loads(state.read_text())["providers"][BUCKET]["day"]
    assert (day["requests"], day["tokens"]) == (300, 300)


# ---- O-06: a cancelled call gives back its token reservation --------------------------------------------------

class _Hangs:
    def __init__(self):
        self.started = asyncio.Event()

    async def complete(self, *a, **k):
        self.started.set()
        await asyncio.sleep(3600)


async def test_a_cancelled_call_releases_its_reservation(factory):
    backend = _Hangs()
    gw = ModelGateway(factory.providers, factory.ledger, Tracer("t"), "groq", backend=backend, retries=0)
    call = asyncio.create_task(gw.complete(ModelSlot(canonical_model="gpt-oss-120b"),
                                           [{"role": "user", "content": "x" * 4000}], []))
    await backend.started.wait()
    assert factory.ledger._minute[BUCKET].tokens > 0                    # reserved while the call is in flight
    call.cancel()
    with pytest.raises(asyncio.CancelledError):
        await call
    assert factory.ledger._minute[BUCKET].tokens == 0
    assert factory.ledger._day[BUCKET].tokens == 0
    saved = json.loads(factory.ledger.state_path.read_text())["providers"][BUCKET]
    assert saved["day"]["tokens"] == 0                                  # and the persisted ledger agrees


# ---- L-01: python -m has every command -------------------------------------------------------------------------

def test_python_dash_m_has_every_command():
    out = subprocess.run([sys.executable, "-m", "harness_factory.cli.main", "--help"], check=False,
                         capture_output=True, text=True, timeout=120, env={**os.environ, "COLUMNS": "200"})
    assert out.returncode == 0
    for cmd in ("select", "provision", "report", "smoke", "validate"):
        assert cmd in out.stdout


# ---- L-02 / L-03: fs and sql edge cases reach the model as ToolErrors ------------------------------------------

@pytest.fixture
def sandbox(factory):
    with LocalSandbox("ops-test") as sb:
        sb.reset(factory.tasks.get("mix-04"))
        yield sb


async def test_fs_edge_cases_are_tool_errors_with_a_reason(factory, sandbox):
    (sandbox.workspace / "bin.dat").write_bytes(b"\xff\xfe\x00bad")
    async with MCPSessionPool(factory.declared, ["fs"], sandbox.env()) as pool:
        cases = {
            ("fs.write_file", "."): "is a directory",
            ("fs.write_file", "data"): "is a directory",
            ("fs.write_file", "data/new_customers.csv/x.txt"): "parent of it is a file",
            ("fs.read_file", "bin.dat"): "not a UTF-8 text file",
        }
        for (tool, path), why in cases.items():
            args = {"path": path, "content": "x"} if tool == "fs.write_file" else {"path": path}
            res = await pool.call(tool, args)
            assert res.is_error and why in res.text, (tool, path, res.text)


async def test_sql_execute_allows_a_semicolon_inside_a_literal(factory, sandbox):
    async with MCPSessionPool(factory.declared, ["sql"], sandbox.env()) as pool:
        ok = await pool.call("sql.execute", {"sql": "UPDATE customers SET email = 'a;b@x.ae' WHERE id = 1;"})
        assert not ok.is_error and json.loads(ok.text) == {"rows_changed": 1}
        two = await pool.call("sql.execute", {"sql": "UPDATE customers SET city = 'X' WHERE id = 1; "
                                                     "DELETE FROM customers"})
        assert two.is_error and "exactly one statement" in two.text
        left = await pool.call("sql.query", {"sql": "SELECT email, city != 'X' FROM customers WHERE id = 1"})
        assert json.loads(left.text)["rows"] == [["a;b@x.ae", 1]]      # neither statement of the pair ran
        n = await pool.call("sql.query", {"sql": "SELECT COUNT(*) FROM customers"})
        assert json.loads(n.text)["rows"][0][0] > 0


# ---- L-04: remote_equals doesn't confuse booleans with numbers ------------------------------------------------

@pytest.mark.parametrize("actual, expected, same", [
    (True, "1", False), (False, "0", False), (1, "true", False), (0, "false", False),
    (True, "true", True), (1, "1", True), (1.0, "1", True), ("1", "1", True), (1, '"1"', False),
    ({"paid": True}, '{"paid": 1}', False), ({"paid": True}, '{"paid": true}', True),
    ([1, True], "[1, true]", True), ([1, 1], "[1, true]", False),
])
def test_remote_equals_is_strict_about_booleans(actual, expected, same):
    assert _same(actual, expected) is same


# ---- L-08: provision and select evaluate report failures in their exit code ----------------------------------

def _failing_campaign(*a, **k):
    async def go():
        return CampaignResult(records=[], errors=[CampaignError(
            spec=RunSpec(task_id="fs-01", recipe_id="v2-base", canonical_model="scripted/replay", provider="scripted"),
            error="boom")], concurrency=1, wall_s=0.0, quota_wait_s={})
    return go()


def test_provision_exits_non_zero_when_its_run_fails(factory, monkeypatch, tmp_path):
    from harness_factory.cli import main
    from harness_factory.runner import runner
    monkeypatch.setattr(settings, "VAR", tmp_path / "var")              # decision log under tmp_path
    monkeypatch.setattr(main, "_factory", lambda store=True: factory)
    monkeypatch.setattr(runner, "run_campaign", _failing_campaign)
    out = CliRunner().invoke(main.app, ["provision", "--task", "fs-01", "--model", "scripted/replay",
                                        "--provider", "scripted"])
    assert out.exit_code == 1 and "boom" in out.output


def test_select_evaluate_reports_failed_runs(factory, monkeypatch):
    from harness_factory.cli import main
    from harness_factory.runner import runner
    monkeypatch.setattr(main, "_factory", lambda store=True: factory)
    monkeypatch.setattr(runner, "run_campaign", _failing_campaign)
    out = CliRunner().invoke(main.app, ["select", "evaluate", "--model", "scripted/replay",
                                        "--provider", "scripted"])
    assert out.exit_code == 1
    assert "fs-01 with v2-base: boom" in out.output and "failed" in out.output


# ---- L-09: tests keep their traces under tmp_path ---------------------------------------------------------------

async def test_a_test_run_writes_its_trace_under_tmp_path(factory, tmp_path):
    rec = await run_one(factory, factory.tasks.get("fs-01"), factory.recipes["v2-base"], "scripted/replay",
                        "scripted")
    assert str(tmp_path) in rec.trace_path and str(tmp_path) in rec.transcript_path
    assert str(tmp_path) in str(settings.QUOTA_STATE) and str(tmp_path) in settings.RESULTS_URL


# ---- L-10: garbled reads cover every read tool, search included -------------------------------------------------

def test_garbled_reads_cover_the_search_tools():
    profile = load_fault_profiles(settings.FAULTS_DIR)["garbled-reads"]
    for rule in profile.rules:
        assert {"search.find", "search.grep"} <= set(rule.tools)


def test_authoring_snapshot_and_validate_agree_on_an_unknown_grader(tmp_path):
    """The authoring snapshot (child process) flags the same unknown grader as `hfx validate`."""
    import shutil

    import yaml

    from harness_factory.authoring.service import AuthoringService
    root = tmp_path / "repo"
    root.mkdir()
    for name in ("catalog", "prompts"):
        shutil.copytree(settings.ROOT / name, root / name)
    shutil.copy(settings.ROOT / "pyproject.toml", root / "pyproject.toml")
    task_yaml = root / "catalog/tasks/fs-01/task.yaml"
    data = yaml.safe_load(task_yaml.read_text())
    data["grader_ids"] = ["state_dif", "trajectory"]
    task_yaml.write_text(yaml.safe_dump(data, sort_keys=False))
    errors = AuthoringService(root).errors()
    assert any("fs-01" in str(e) and "state_dif" in str(e) for e in errors), errors
    env = {**os.environ, "HF_ROOT": str(root), "HF_VAR": str(root / "var"),
           "HF_RESULTS_URL": f"sqlite:///{root / 'var/results.db'}", "HF_QUOTA_STATE": str(root / "var/quota.json")}
    out = subprocess.run([sys.executable, "-c", "from harness_factory.cli.main import app; app()", "validate"],
                         env=env, capture_output=True, text=True, timeout=300, check=False)
    assert out.returncode == 1 and "state_dif" in out.stdout
