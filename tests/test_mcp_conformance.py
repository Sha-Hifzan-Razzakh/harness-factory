"""MCP conformance (MCP-11) and tool-server health before a run (ENV-06).

Every server in catalog/tools.yaml passes the suite (a server added later is picked up by itself, and fails until
it has a fixture); a stand-in server breaks each rule in turn and the suite names what is wrong; an unhealthy server
makes provisioning an ERROR quickly, and nothing it started is left behind."""

import asyncio
import dataclasses
import os
import subprocess
import time
from pathlib import Path

import pytest
import yaml

from harness_factory import settings
from harness_factory.env.mcp.servers import MCPServerSpec, SideEffect, ToolSpec, load_tool_catalog
from harness_factory.env.sandbox import LocalSandbox
from harness_factory.mcp import conformance as conf
from harness_factory.mcp.pool import MCPSessionPool
from harness_factory.recipes.model import ToolSlice
from harness_factory.runner.runner import run_one
from harness_factory.substrate.errors import ErrorKind, HarnessError

STANDINS = Path(__file__).parent / "standins"
DECLARED = load_tool_catalog(settings.TOOLS_FILE)


# ------------------------------------------------------------------ every declared server

@pytest.mark.parametrize("server", [s.id for s in DECLARED.servers])
async def test_every_declared_server_conforms(server):
    report = await conf.check_server(DECLARED, server)
    if report.skipped:
        pytest.skip(f"{server}: {report.skipped}")
    assert [f.rule for f in report.findings] == list(conf.RULES)
    assert report.ok, "\n".join(f"{f.rule}: {f.detail}" for f in report.failures())
    assert report.sandbox == settings.SANDBOX


def test_every_declared_server_has_a_fixture_and_none_is_stale():
    for s in DECLARED.servers:
        fx, _ = conf.load_fixture(s.id)
        assert conf.coverage_problems(s, fx) == [], s.id
    assert conf.stale_fixtures(DECLARED) == []


async def test_a_declared_server_without_a_fixture_fails_loudly(tmp_path):
    report = await conf.check_server(DECLARED, "fs", fixtures_dir=tmp_path)
    assert not report.ok and report.skipped is None
    fx = report.finding("fixture")
    assert not fx.passed and "no conformance fixture" in fx.detail and "every declared server needs one" in fx.detail
    assert all(not f.passed and "not checked" in f.detail for f in report.findings if f.rule != "fixture")
    (tmp_path / "gone.yaml").write_text("server: gone\n")
    assert conf.stale_fixtures(DECLARED, tmp_path) == [tmp_path / "gone.yaml"]


async def test_a_fixture_must_call_every_declared_tool(tmp_path):
    fx = yaml.safe_load(conf.fixture_path("fs").read_text())
    fx["calls"] = [c for c in fx["calls"] if c["tool"] != "delete_file"] + [{"tool": "nope"}]
    fx["seed"] = {"files": str(settings.CATALOG / "conformance/seed/files")}
    (tmp_path / "fs.yaml").write_text(yaml.safe_dump(fx))
    report = await conf.check_server(DECLARED, "fs", fixtures_dir=tmp_path)
    detail = report.finding("fixture").detail
    assert "no happy-path call for fs.delete_file" in detail and "calls undeclared tool 'nope'" in detail


def test_the_cli_reports_every_server(monkeypatch):
    from typer.testing import CliRunner

    from harness_factory.cli.main import app
    out = CliRunner().invoke(app, ["conformance", "--server", "fs", "--server", "skills"])
    assert out.exit_code == 0, out.output
    assert "fs" in out.output and "skills" in out.output and "2 server(s) conform" in out.output


# ------------------------------------------------------------------ a stand-in that breaks each rule

def standin_spec(**hashes) -> MCPServerSpec:
    return MCPServerSpec(id="standin", module="conform_standin_server",
                         tools=[ToolSpec(name=n, side_effect=SideEffect.READ, risk="R0", timeout_s=5, **hashes.get(n, {}))
                                for n in ("echo", "stamp")])


@pytest.fixture
def standin(tmp_path, monkeypatch):
    """The stand-in declared as tools.yaml would after `hfx tools freeze` on the well-behaved server, and a fixture
    whose `mode` writes the BROKEN file that tells the server which rule to break."""
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join([str(STANDINS), os.environ.get("PYTHONPATH", "")]))

    async def freeze():
        with LocalSandbox("standin-freeze") as sb:
            probe = DECLARED.model_copy(update={"servers": [standin_spec()]})
            async with MCPSessionPool(probe, ["standin"], sb.env(), sb.launch) as pool:
                return {r.split(".")[1]: {"schema_hash": pool.catalog.schema_hash(r),
                                          "description_hash": pool.catalog.description_hash(r)}
                        for r in pool.catalog.tools}

    declared = DECLARED.model_copy(update={"servers": [*DECLARED.servers, standin_spec(**asyncio.run(freeze()))]})

    def run(mode: str = ""):
        fixture = {"server": "standin", "edits": {"BROKEN": mode} if mode else {},
                   "calls": [{"tool": "echo", "args": {"text": "hi"}, "expect": {"contains": ["hi"]}},
                             {"tool": "stamp", "args": {"n": 2}, "expect": {"contains": ["stamp 2"]}}],
                   "refusals": [{"tool": "echo", "args": {"text": "forbidden"}, "contains": ["refuses"]}]}
        (tmp_path / "standin.yaml").write_text(yaml.safe_dump(fixture))
        return asyncio.run(conf.check_server(declared, "standin", fixtures_dir=tmp_path, sandbox_kind="local"))

    return run


def test_the_well_behaved_standin_passes_every_rule(standin):
    report = standin()
    assert report.ok, [(f.rule, f.detail) for f in report.failures()]


BROKEN = {
    "initialize": "the server names itself 'someone-else', but tools.yaml declares it as 'standin'",
    "list_tools": "standin.undeclared: undeclared",
    "tool_names": "'bad name!'",
    "input_schema": "echo input schema is not a valid JSON Schema",
    "call": 'echo({"text": "hi"}) failed: internal failure',
    "unknown_tool": "calling an unknown tool succeeded",
    "invalid_args": 'echo({}) succeeded',
    "refusal": "the reason is masked (raise ToolError",
    "usable_after_error": "after the errors, echo fails: the server is wedged",
    "shutdown": "the server did not exit when its stdin closed",
}


@pytest.mark.parametrize("rule", list(BROKEN))
def test_a_broken_server_fails_the_rule_it_breaks_with_a_clear_message(standin, rule):
    report = standin(rule)
    f = report.finding(rule)
    assert not report.ok and not f.passed, f"{rule} passed on a server that breaks it"
    assert BROKEN[rule] in f.detail, f.detail
    assert set(BROKEN) == set(conf.RULES) - {"fixture"}           # every rule has a breaking stand-in


# ------------------------------------------------------------------ ENV-06: health before the run

def _children(marker: str) -> list[str]:
    out = subprocess.run(["ps", "-o", "args=", "--ppid", str(os.getpid())], capture_output=True, text=True,
                         check=False).stdout
    return [line for line in out.splitlines() if marker in line]


def _with_server(factory, module: str):
    spec = MCPServerSpec(id="ext", module=module, tools=[ToolSpec(name="look", side_effect=SideEffect.READ, risk="R0",
                                                                  schema_hash="0" * 16, description_hash="0" * 16)])
    declared = factory.declared.model_copy(update={"servers": [*factory.declared.servers, spec]})
    base = factory.recipes["v2-base"]
    recipe = base.model_copy(update={"id": "v2-ext", "tools": ToolSlice(tool_ids=[*base.tools.tool_ids, "ext.*"],
                                                                        mcp_servers=[*base.tools.mcp_servers, "ext"])})
    return dataclasses.replace(factory, declared=declared), recipe


@pytest.mark.parametrize("module, why", [("hang_server", "no answer within 1 s"),
                                         ("crash_server", "is unhealthy: ")])
async def test_an_unhealthy_server_makes_provisioning_an_error_quickly_and_cleans_up(
        factory, monkeypatch, own_sandboxes_dir, module, why):
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join([str(STANDINS), os.environ.get("PYTHONPATH", "")]))
    monkeypatch.setenv("HF_MCP_HEALTH_TIMEOUT_S", "1")
    f, recipe = _with_server(factory, module)
    t0 = time.monotonic()
    with pytest.raises(HarnessError) as e:
        await run_one(f, factory.tasks.get("fs-01"), recipe, "scripted/replay", "scripted")
    assert time.monotonic() - t0 < 15
    assert e.value.kind == ErrorKind.PROVISION
    assert "MCP server 'ext' is unhealthy" in e.value.message and why in e.value.message
    assert "the run did not start" in e.value.message
    assert f.store is None or not f.store.rows()                     # nothing scored, nothing stored
    assert not list(own_sandboxes_dir.iterdir())                       # the sandbox is gone
    for _ in range(60):
        if not _children(module):
            break
        await asyncio.sleep(0.05)
    assert _children(module) == []                                     # and so is every server it started


async def test_cancelling_during_the_health_probe_leaves_nothing_running(factory, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join([str(STANDINS), os.environ.get("PYTHONPATH", "")]))
    f, _ = _with_server(factory, "hang_server")
    with LocalSandbox("health-cancel") as sb:
        pool = MCPSessionPool(f.declared, ["fs", "ext"], sb.env(), sb.launch)
        job = asyncio.ensure_future(pool.__aenter__())
        await asyncio.sleep(1.0)                                       # fs is up, ext is hanging in its probe
        job.cancel()
        with pytest.raises(asyncio.CancelledError):
            await job
    for _ in range(80):
        if not _children("hang_server") and not _children("fs_server"):
            break
        await asyncio.sleep(0.05)
    assert _children("hang_server") == [] and _children("fs_server") == []


async def test_a_healthy_run_records_each_servers_health_and_nothing_else_changes(factory):
    rec = await run_one(factory, factory.tasks.get("fs-01"), factory.recipes["v2-base"], "scripted/replay",
                        "scripted")
    assert rec.status.value == "SUCCESS"
    snap = next(g for g in rec.grades if g["grader_id"] == "state_diff")["snapshot"]
    assert set(snap["mcp_health"]) == set(factory.recipes["v2-base"].tools.mcp_servers)
    for h in snap["mcp_health"].values():
        assert h["status"] == "ok" and h["probe"] == "ping" and h["ready_s"] >= 0


# ------------------------------------------------------------------ a remote server (the in-repo stand-in)

def _kb(tmp_path, monkeypatch):
    import socket
    import sys
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    token = "kb-conformance-token-31c9"          # a test credential, only for the stand-in server
    proc = subprocess.Popen([sys.executable, str(Path(__file__).parent / "remote_kb_server.py"), str(port), token])
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.1)
    monkeypatch.setenv("HF_TEST_KB_TOKEN", token)
    fixture = {"server": "kb", "calls": [{"tool": "lookup", "args": {"term": "net revenue"},
                                          "expect": {"contains": ["refunds"]}}],
               "refusals": [{"tool": "lookup", "args": {"term": "nope"}, "contains": ["no glossary entry"]}]}
    (tmp_path / "kb.yaml").write_text(yaml.safe_dump(fixture))
    return proc, f"http://127.0.0.1:{port}/mcp"


async def test_a_remote_server_is_checked_live_only_with_opt_in(tmp_path, monkeypatch):
    proc, url = _kb(tmp_path, monkeypatch)
    try:
        spec = MCPServerSpec(id="kb", transport="streamable_http", url=url, version="1.0", auth_env="HF_TEST_KB_TOKEN",
                             tools=[ToolSpec(name="lookup", side_effect=SideEffect.READ, risk="R0", timeout_s=10)])
        probe = DECLARED.model_copy(update={"servers": [spec]})
        async with MCPSessionPool(probe, ["kb"], {}) as pool:
            hashes = {"schema_hash": pool.catalog.schema_hash("kb.lookup"),
                      "description_hash": pool.catalog.description_hash("kb.lookup")}
        frozen = spec.model_copy(update={"tools": [spec.tools[0].model_copy(update=hashes)]})
        declared = DECLARED.model_copy(update={"servers": [*DECLARED.servers, frozen]})
        monkeypatch.delenv("HF_ALLOW_REMOTE", raising=False)
        skipped = await conf.check_server(declared, "kb", fixtures_dir=tmp_path)
        assert skipped.skipped and "HF_ALLOW_REMOTE=1" in skipped.skipped and not skipped.ok
        monkeypatch.setenv("HF_ALLOW_REMOTE", "1")
        report = await conf.check_server(declared, "kb", fixtures_dir=tmp_path)
        assert report.ok, [(f.rule, f.detail) for f in report.failures()]
        assert report.transport == "streamable_http" and report.sandbox is None
    finally:
        proc.kill()
        proc.wait()
