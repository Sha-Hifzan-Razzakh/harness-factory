"""Test tenants (EXT-07): remote tools that change state, in a tenant the factory resets before each run and grades
after it.

A stand-in order desk (tests/remote_tenant_server.py) runs on 127.0.0.1 and keeps one state per HF-Tenant header."""

import asyncio
import dataclasses
import json
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

from harness_factory.env.mcp.servers import MCPServerSpec, SideEffect, TenantSpec, ToolSpec
from harness_factory.mcp.pool import MCPSessionPool
from harness_factory.recipes.model import ToolSlice
from harness_factory.runner.runner import run_one
from harness_factory.substrate.gateway import ScriptedBackend
from harness_factory.tasks.model import Task

SEED = {"orders": {"ord-7": {"status": "paid", "amount": 120}, "ord-8": {"status": "paid", "amount": 80}}}
REFUND7 = {"tool_calls": [{"name": "orders__refund_order", "arguments": {"order_id": "ord-7"}}]}
REFUND8 = {"tool_calls": [{"name": "orders__refund_order", "arguments": {"order_id": "ord-8"}}]}
GET7 = {"tool_calls": [{"name": "orders__get_order", "arguments": {"order_id": "ord-7"}}]}
DONE = {"content": "Refunded ord-7."}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def desk():
    port = free_port()
    proc = subprocess.Popen([sys.executable, str(Path(__file__).parent / "remote_tenant_server.py"), str(port)])
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.1)
    else:
        proc.kill()
        pytest.fail("stand-in tenant server did not start")
    yield {"url": f"http://127.0.0.1:{port}/mcp", "port": port, "proc": proc}
    proc.kill()
    proc.wait()


def tenants_held(port: int) -> int:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))       # localhost: never via a proxy
    return json.loads(opener.open(f"http://127.0.0.1:{port}/tenants", timeout=5).read())["tenants"]


def desk_spec(url: str, hashes: dict | None = None) -> MCPServerSpec:
    hashes = hashes or {}
    return MCPServerSpec(id="orders", transport="streamable_http", url=url, version="1.0", tenant=TenantSpec(), tools=[
        ToolSpec(name="get_order", side_effect=SideEffect.READ, risk="R0", timeout_s=10,
                 schema_hash=hashes.get("orders.get_order")),
        ToolSpec(name="refund_order", side_effect=SideEffect.REVERSIBLE, risk="R1", timeout_s=10,
                 schema_hash=hashes.get("orders.refund_order"))])


def refund_task() -> Task:
    task = Task.model_validate({
        "id": "orders-01", "domain": "api", "instruction": "Refund order ord-7. Leave every other order as it is.",
        "env_seed": {"mock_api_state": {"orders": SEED}}, "allowed_tools": ["orders.*"], "risk": "R1",
        "oracle": {"checks": [
            {"id": "refunded", "kind": "remote_equals", "weight": 1.0, "path": "orders:/orders/ord-7/status",
             "text": "refunded"},
            {"id": "others_untouched", "kind": "remote_equals", "weight": 0, "critical": True,
             "path": "orders:/orders/ord-8", "text": json.dumps(SEED["orders"]["ord-8"])}]}})
    task.source_dir = Path(__file__).parent                # no seed files: the remote tenant is the whole world
    return task


async def setup(factory, url, monkeypatch):
    monkeypatch.setenv("HF_ALLOW_REMOTE", "1")
    probe = factory.declared.model_copy(update={"servers": [desk_spec(url)]})
    async with MCPSessionPool(probe, ["orders"], {}) as pool:                      # discovery only: no tenant
        hashes = {r: pool.catalog.schema_hash(r) for r in ("orders.get_order", "orders.refund_order")}
        assert sorted(pool.catalog.tools) == ["orders.get_order", "orders.refund_order"]   # admin tools hidden
    declared = factory.declared.model_copy(update={"servers": [*factory.declared.servers, desk_spec(url, hashes)]})
    f = dataclasses.replace(factory, declared=declared)
    recipe = factory.recipes["v2-base"].model_copy(update={
        "id": "v2-orders", "tools": ToolSlice(tool_ids=["fs.*", "orders.*"], mcp_servers=["fs", "orders"])})
    return f, recipe, refund_task()


def run(f, recipe, task, script):
    return run_one(f, task, recipe, "scripted/replay", "scripted", backend=ScriptedBackend(script))


def tool_texts(rec):
    t = json.loads(Path(rec.transcript_path).read_text())
    return [m["content"] for m in t["messages"] if m["role"] == "tool"]


# ------------------------------------------------------------------ catalog rules

def test_remote_tools_that_change_state_need_a_tenant_and_admin_tools_stay_undeclared():
    refund = ToolSpec(name="refund_order", side_effect=SideEffect.REVERSIBLE, risk="R1")
    with pytest.raises(ValueError, match="need a test tenant"):
        MCPServerSpec(id="x", transport="streamable_http", url="https://x.example/mcp", version="1", tools=[refund])
    MCPServerSpec(id="x", transport="streamable_http", url="https://x.example/mcp", version="1", tenant=TenantSpec(),
                  tools=[refund])
    with pytest.raises(ValueError, match="never declared as tools: _tenant_reset"):
        MCPServerSpec(id="x", transport="streamable_http", url="https://x.example/mcp", version="1",
                      tenant=TenantSpec(), tools=[ToolSpec(name="_tenant_reset", side_effect=SideEffect.READ,
                                                           risk="R0")])
    with pytest.raises(ValueError, match="`tenant` is for remote servers"):
        MCPServerSpec(id="x", module="x", tenant=TenantSpec())


def test_a_remote_check_names_a_server_and_a_json_pointer():
    with pytest.raises(ValueError, match="'<server>:<JSON pointer>'"):
        Task.model_validate({**refund_task().model_dump(), "oracle": {"checks": [
            {"id": "c", "kind": "remote_equals", "weight": 1.0, "path": "orders/ord-7", "text": "x"}]}})


# ------------------------------------------------------------------ live runs

async def test_a_run_that_changes_remote_state_is_graded_on_its_tenant(factory, desk, monkeypatch):
    f, recipe, task = await setup(factory, desk["url"], monkeypatch)
    rec = await run(f, recipe, task, [REFUND7, DONE])
    assert rec.status.value == "SUCCESS" and rec.scores.outcome == 1.0
    t = json.loads(Path(rec.transcript_path).read_text())
    assert t["snapshot"]["tenant"].startswith("hf-") and t["snapshot"]["tenant_servers"] == ["orders"]
    assert t["snapshot"]["tenant_state"]["orders"]["orders"]["ord-7"]["status"] == "refunded"
    assert t["snapshot"]["tenant"] not in tool_texts(rec)[0]                       # the model never sees the id
    assert tenants_held(desk["port"]) == 0                                        # dropped when the run closed


async def test_concurrent_runs_get_their_own_tenant_and_a_do_nothing_run_scores_zero(factory, desk, monkeypatch):
    f, recipe, task = await setup(factory, desk["url"], monkeypatch)
    worker, idle = await asyncio.gather(run(f, recipe, task, [REFUND7, DONE]),
                                        run(f, recipe, task, [{"content": "Done."}]))
    assert worker.status.value == "SUCCESS"
    assert idle.scores.outcome == 0.0 and idle.status.value == "FAIL"             # saw the seed, not the other run


async def test_touching_what_the_task_says_to_leave_alone_fails_the_guard(factory, desk, monkeypatch):
    f, recipe, task = await setup(factory, desk["url"], monkeypatch)
    rec = await run(f, recipe, task, [REFUND7, REFUND8, DONE])
    assert rec.status.value == "FAIL"
    assert any(v.kind == "critical_check_failed" and v.detail == "others_untouched" for v in rec.violations)


async def test_each_run_starts_from_the_seed(factory, desk, monkeypatch):
    f, recipe, task = await setup(factory, desk["url"], monkeypatch)
    for _ in range(2):                                     # the second run can refund again: its tenant is fresh
        rec = await run(f, recipe, task, [REFUND7, DONE])
        assert rec.status.value == "SUCCESS" and tool_texts(rec) == ["ord-7 refunded"]


class KillsServerAfterFirstCall(ScriptedBackend):
    def __init__(self, turns, proc):
        super().__init__(turns)
        self.proc = proc

    async def complete(self, *args, **kwargs):
        if self.i == 1:
            self.proc.kill()
            self.proc.wait()
        return await super().complete(*args, **kwargs)


async def test_a_tenant_that_cant_be_read_after_the_run_makes_it_incomplete(factory, desk, monkeypatch):
    f, recipe, task = await setup(factory, desk["url"], monkeypatch)
    rec = await run_one(f, task, recipe, "scripted/replay", "scripted",
                        backend=KillsServerAfterFirstCall([REFUND7, DONE], desk["proc"]))
    assert rec.status.value == "INCOMPLETE" and rec.scores.outcome == 0.0         # the environment, not the harness
    assert any("test tenant of 'orders'" in (g.get("detail") or "") for g in rec.grades if g["grader_id"] == "run_error")


# ------------------------------------------------------------------ record and replay

@pytest.fixture
def recorded(factory, desk, monkeypatch, tmp_path):
    async def make(script):
        monkeypatch.setenv("HF_REMOTE_RECORDINGS", str(tmp_path / "rec"))
        f, recipe, task = await setup(factory, desk["url"], monkeypatch)
        monkeypatch.setenv("HF_REMOTE_MODE", "record")
        live = await run(f, recipe, task, script)
        desk["proc"].kill()
        desk["proc"].wait()
        monkeypatch.delenv("HF_ALLOW_REMOTE")
        monkeypatch.setenv("HF_REMOTE_MODE", "replay")
        return f, recipe, task, live
    return make


async def test_a_recorded_run_replays_with_repeats_answered_in_order(recorded):
    script = [GET7, REFUND7, GET7, DONE]
    f, recipe, task, live = await recorded(script)
    rep = await run(f, recipe, task, script)
    assert tool_texts(rep) == tool_texts(live) == ["ord-7: paid, 120 AED", "ord-7 refunded", "ord-7: refunded, 120 AED"]
    assert rep.status.value == live.status.value == "SUCCESS" and rep.run_key == live.run_key


async def test_a_replay_that_changes_state_differently_is_not_graded_on_the_recorded_end(recorded):
    f, recipe, task, _ = await recorded([REFUND7, DONE])
    lazy = await run(f, recipe, task, [{"content": "Refunded ord-7."}])          # claims it, never refunds
    assert lazy.status.value == "INCOMPLETE" and lazy.scores.outcome == 0.0       # no credit from the recording
    assert any("replay_diverged" in (g.get("detail") or "") for g in lazy.grades if g["grader_id"] == "run_error")
