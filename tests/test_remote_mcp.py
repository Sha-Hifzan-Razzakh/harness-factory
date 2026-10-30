"""Remote MCP servers (MCP-03, EXT-05, EXT-07, EXT-13): an existing server over streamable HTTP.

A stand-in server (tests/remote_kb_server.py) runs on 127.0.0.1 behind a bearer token. The factory's MCP client
connects from the factory process; the sandbox keeps no network."""

import dataclasses
import json
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from harness_factory.env.mcp.servers import MCPServerSpec, SideEffect, ToolSpec
from harness_factory.mcp.pool import MCPSessionPool
from harness_factory.recipes.model import ToolSlice
from harness_factory.runner.runner import run_one
from harness_factory.substrate.errors import HarnessError
from harness_factory.substrate.gateway import ScriptedBackend

TOKEN = "kb-test-token-7f3a"            # a test credential, only for the stand-in server
LOOKUP = {"tool_calls": [{"name": "kb__lookup", "arguments": {"term": "net revenue"}}]}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def kb_server():
    port = free_port()
    proc = subprocess.Popen([sys.executable, str(Path(__file__).parent / "remote_kb_server.py"), str(port), TOKEN])
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.1)
    else:
        proc.kill()
        pytest.fail("stand-in remote server did not start")
    yield {"url": f"http://127.0.0.1:{port}/mcp", "proc": proc}
    proc.kill()
    proc.wait()


def kb_spec(url: str, version: str = "1.0", schema_hash: str | None = None, auth_env: str = "HF_TEST_KB_TOKEN"):
    return MCPServerSpec(id="kb", transport="streamable_http", url=url, version=version, auth_env=auth_env,
                         tools=[ToolSpec(name="lookup", side_effect=SideEffect.READ, risk="R0", timeout_s=10,
                                         schema_hash=schema_hash)])


async def live_setup(factory, url):
    """The factory with `kb` declared (schema hash discovered, as `hfx tools freeze` would), a v2 recipe that opens
    it, and a task that allows it."""
    probe = factory.declared.model_copy(update={"servers": [kb_spec(url)]})
    async with MCPSessionPool(probe, ["kb"], {}) as pool:
        h = pool.catalog.schema_hash("kb.lookup")
    declared = factory.declared.model_copy(update={"servers": [*factory.declared.servers, kb_spec(url, schema_hash=h)]})
    f = dataclasses.replace(factory, declared=declared)
    base = factory.recipes["v2-base"]
    recipe = base.model_copy(update={"id": "v2-kb", "tools": ToolSlice(tool_ids=["fs.*", "sql.*", "kb.*"],
                                                                       mcp_servers=["fs", "sql", "kb"])})
    task = factory.tasks.get("sql-03")
    task = task.model_copy(update={"allowed_tools": [*task.allowed_tools, "kb.lookup"]})
    return f, recipe, task


# ------------------------------------------------------------------ catalog rules

def test_a_remote_server_needs_a_url_a_pinned_version_and_read_only_tools():
    with pytest.raises(ValueError, match="needs `url` and a pinned `version`"):
        MCPServerSpec(id="x", transport="streamable_http", url="https://x.example/mcp")
    with pytest.raises(ValueError, match="must be READ until test tenants exist"):
        MCPServerSpec(id="x", transport="streamable_http", url="https://x.example/mcp", version="2",
                      tools=[ToolSpec(name="send", side_effect=SideEffect.REVERSIBLE, risk="R1")])
    with pytest.raises(ValueError, match="stdio server needs `module`"):
        MCPServerSpec(id="x")


def test_remote_url_and_version_are_part_of_env_hash_and_local_hashes_stay(factory):
    d = factory.declared
    plus = d.model_copy(update={"servers": [*d.servers, kb_spec("https://kb.example/mcp")]})
    assert plus.env_hash(["fs", "sql"], "m") == d.env_hash(["fs", "sql"], "m")         # not opened: unchanged
    v1 = plus.env_hash(["fs", "sql", "kb"], "m")
    v2 = d.model_copy(update={"servers": [*d.servers, kb_spec("https://kb.example/mcp", version="1.1")]})
    assert v1 != v2.env_hash(["fs", "sql", "kb"], "m")                                  # new version, new cells


# ------------------------------------------------------------------ live runs

async def test_remote_calls_are_opt_in(factory, kb_server, monkeypatch):
    monkeypatch.setenv("HF_TEST_KB_TOKEN", TOKEN)
    monkeypatch.setenv("HF_ALLOW_REMOTE", "1")
    f, recipe, task = await live_setup(factory, kb_server["url"])
    monkeypatch.delenv("HF_ALLOW_REMOTE")
    with pytest.raises(HarnessError, match="opt-in"):
        await run_one(f, task, recipe, "scripted/replay", "scripted", backend=ScriptedBackend([LOOKUP]))


async def test_a_live_run_calls_the_remote_server_and_the_token_stays_out_of_records(factory, kb_server, monkeypatch):
    monkeypatch.setenv("HF_TEST_KB_TOKEN", TOKEN)
    monkeypatch.setenv("HF_ALLOW_REMOTE", "1")
    f, recipe, task = await live_setup(factory, kb_server["url"])
    rec = await run_one(f, task, recipe, "scripted/replay", "scripted",
                        backend=ScriptedBackend([LOOKUP, {"content": "Harbor Logistics"}]))
    t = json.loads(Path(rec.transcript_path).read_text())
    assert t["snapshot"]["mcp_transports"]["kb"] == "STREAMABLE_HTTP"
    assert t["snapshot"]["remote_servers"] == {"kb": f"{kb_server['url']}@1.0"}        # marked as a live run
    tool_msgs = [m for m in t["messages"] if m["role"] == "tool"]
    assert tool_msgs[0]["content"] == "Paid order value minus refunds."
    assert rec.run_key.env_hash != f.declared.env_hash(["fs", "sql"], "replay")
    for path in (rec.trace_path, rec.transcript_path):
        assert TOKEN not in Path(path).read_text()                                    # EXT-13


async def test_a_missing_or_wrong_token_fails_provisioning_with_the_reason(factory, kb_server, monkeypatch):
    monkeypatch.setenv("HF_TEST_KB_TOKEN", TOKEN)
    monkeypatch.setenv("HF_ALLOW_REMOTE", "1")
    f, recipe, task = await live_setup(factory, kb_server["url"])
    monkeypatch.delenv("HF_TEST_KB_TOKEN")
    with pytest.raises(HarnessError, match="needs HF_TEST_KB_TOKEN"):
        await run_one(f, task, recipe, "scripted/replay", "scripted", backend=ScriptedBackend([LOOKUP]))
    monkeypatch.setenv("HF_TEST_KB_TOKEN", "wrong")
    with pytest.raises(HarnessError, match="remote MCP server 'kb'"):
        await run_one(f, task, recipe, "scripted/replay", "scripted", backend=ScriptedBackend([LOOKUP]))


class KillsServerFirst(ScriptedBackend):
    """The remote server goes away between provisioning and the first tool call."""

    def __init__(self, turns, proc):
        super().__init__(turns)
        self.proc = proc

    async def complete(self, *args, **kwargs):
        if self.i == 0:
            self.proc.kill()
            self.proc.wait()
        return await super().complete(*args, **kwargs)


async def test_a_remote_server_failing_mid_run_is_the_environment_not_the_model(factory, kb_server, monkeypatch):
    monkeypatch.setenv("HF_TEST_KB_TOKEN", TOKEN)
    monkeypatch.setenv("HF_ALLOW_REMOTE", "1")
    f, recipe, task = await live_setup(factory, kb_server["url"])
    rec = await run_one(f, task, recipe, "scripted/replay", "scripted",
                        backend=KillsServerFirst([LOOKUP, {"content": "Harbor Logistics"}], kb_server["proc"]))
    spans = [json.loads(line) for line in Path(rec.trace_path).read_text().splitlines()]
    (call,) = [s for s in spans if s["name"] == "kb.lookup"]
    assert call["status"] == "error" and "infra" in call["attributes"]
    assert rec.scores.trajectory == 1.0                                               # not counted as invalid


async def test_cancelling_while_a_remote_session_opens_leaves_no_task_behind(monkeypatch):
    import asyncio

    from harness_factory.mcp.client import MCPClient

    async def silent(reader, writer):                         # accepts, never answers: the handshake hangs
        await asyncio.sleep(3600)

    server = await asyncio.start_server(silent, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    monkeypatch.setenv("HF_TEST_KB_TOKEN", TOKEN)
    before = set(asyncio.all_tasks())
    client = MCPClient(kb_spec(f"http://127.0.0.1:{port}/mcp"), {})
    opening = asyncio.ensure_future(client.__aenter__())
    for _ in range(100):                                      # wait until the owner task is mid-handshake
        if client._owner is not None:
            break
        await asyncio.sleep(0.01)
    await asyncio.sleep(0.05)
    assert not opening.done()
    opening.cancel()
    with pytest.raises(asyncio.CancelledError):
        await opening
    assert client._owner is not None and client._owner.done()
    leftover = {t for t in asyncio.all_tasks() - before if not t.done() and t is not asyncio.current_task()}
    server.close()
    for t in leftover:                                        # only the silent server's own handler may remain
        assert t.get_coro().__qualname__.startswith("test_cancelling"), t
        t.cancel()


def test_tools_freeze_skips_remote_servers_without_the_opt_in(tmp_path, monkeypatch):
    import yaml
    from typer.testing import CliRunner

    from harness_factory import settings
    from harness_factory.cli import main as cli
    data = yaml.safe_load(settings.TOOLS_FILE.read_text())
    data["servers"].append({"id": "kb", "transport": "streamable_http", "url": "https://kb.example/mcp",
                            "version": "1.0", "tools": [{"name": "lookup", "side_effect": "READ", "risk": "R0",
                                                         "schema_hash": "keep-me"}]})
    tools = tmp_path / "tools.yaml"
    tools.write_text(yaml.safe_dump(data, sort_keys=False))
    monkeypatch.setattr(settings, "TOOLS_FILE", tools)
    monkeypatch.delenv("HF_ALLOW_REMOTE", raising=False)
    out = CliRunner().invoke(cli.app, ["tools", "freeze", "--check"])
    assert out.exit_code == 0, out.output
    assert "skipped remote server(s) kb" in out.output and "up to date" in out.output


# ------------------------------------------------------------------ record and replay (EXT-08)

def tool_texts(rec):
    t = json.loads(Path(rec.transcript_path).read_text())
    return t, [m["content"] for m in t["messages"] if m["role"] == "tool"]


@pytest.fixture
def recorded(factory, kb_server, monkeypatch, tmp_path):
    """One live run in record mode, then the server is gone and the token unset: only the recording is left."""
    async def make(script):
        monkeypatch.setenv("HF_TEST_KB_TOKEN", TOKEN)
        monkeypatch.setenv("HF_ALLOW_REMOTE", "1")
        monkeypatch.setenv("HF_REMOTE_RECORDINGS", str(tmp_path / "rec"))
        f, recipe, task = await live_setup(factory, kb_server["url"])
        monkeypatch.setenv("HF_REMOTE_MODE", "record")
        live = await run_one(f, task, recipe, "scripted/replay", "scripted", backend=ScriptedBackend(script))
        kb_server["proc"].kill()
        kb_server["proc"].wait()
        monkeypatch.delenv("HF_ALLOW_REMOTE")
        monkeypatch.delenv("HF_TEST_KB_TOKEN")
        monkeypatch.setenv("HF_REMOTE_MODE", "replay")
        return f, recipe, task, live
    return make


async def test_a_recorded_run_replays_offline_with_the_same_answers(recorded, tmp_path):
    script = [LOOKUP, {"content": "Harbor Logistics"}]
    f, recipe, task, live = await recorded(script)
    rep = await run_one(f, task, recipe, "scripted/replay", "scripted", backend=ScriptedBackend(script))
    (t_live, live_tools), (t_rep, rep_tools) = tool_texts(live), tool_texts(rep)
    assert rep_tools == live_tools == ["Paid order value minus refunds."]
    assert t_live["snapshot"]["remote_mode"] == "record" and t_rep["snapshot"]["remote_mode"] == "replay"
    assert t_rep["snapshot"]["mcp_transports"]["kb"] == "REPLAY"                      # a replayed run is marked
    assert rep.run_key == live.run_key                                                # same server version
    for p in (tmp_path / "rec").rglob("*.json"):
        assert TOKEN not in p.read_text()                                             # answers only, never headers


async def test_a_call_missing_from_the_recording_is_the_environment_not_the_model(recorded):
    f, recipe, task, _ = await recorded([LOOKUP, {"content": "ok"}])
    other = {"tool_calls": [{"name": "kb__lookup", "arguments": {"term": "test account"}}]}
    rep = await run_one(f, task, recipe, "scripted/replay", "scripted", backend=ScriptedBackend([other, {"content": "ok"}]))
    spans = [json.loads(line) for line in Path(rep.trace_path).read_text().splitlines()]
    (call,) = [s for s in spans if s["name"] == "kb.lookup"]
    assert call["status"] == "error" and call["attributes"]["infra"] == "replay_miss"
    assert rep.scores.trajectory == 1.0


async def test_replay_needs_a_recording_of_that_server_version(recorded):
    f, recipe, task, _ = await recorded([LOOKUP, {"content": "ok"}])
    kb = next(s for s in f.declared.servers if s.id == "kb")
    newer = f.declared.model_copy(update={"servers": [s if s.id != "kb" else kb.model_copy(update={"version": "1.1"})
                                                      for s in f.declared.servers]})
    with pytest.raises(HarnessError, match="no recording of remote MCP server 'kb'.*HF_REMOTE_MODE=record"):
        await run_one(dataclasses.replace(f, declared=newer), task, recipe, "scripted/replay", "scripted",
                      backend=ScriptedBackend([LOOKUP]))


async def test_recording_is_a_live_call_so_it_needs_the_opt_in(factory, kb_server, monkeypatch, tmp_path):
    monkeypatch.setenv("HF_TEST_KB_TOKEN", TOKEN)
    monkeypatch.setenv("HF_ALLOW_REMOTE", "1")
    f, recipe, task = await live_setup(factory, kb_server["url"])
    monkeypatch.delenv("HF_ALLOW_REMOTE")
    monkeypatch.setenv("HF_REMOTE_MODE", "record")
    with pytest.raises(HarnessError, match="opt-in"):
        await run_one(f, task, recipe, "scripted/replay", "scripted", backend=ScriptedBackend([LOOKUP]))
    monkeypatch.setenv("HF_REMOTE_MODE", "sometimes")
    with pytest.raises(ValueError, match="HF_REMOTE_MODE must be"):
        await run_one(f, task, recipe, "scripted/replay", "scripted", backend=ScriptedBackend([LOOKUP]))
