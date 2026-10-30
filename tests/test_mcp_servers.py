import json

import pytest

from harness_factory.env.sandbox import LocalSandbox
from harness_factory.mcp.pool import MCPSessionPool
from harness_factory.substrate.errors import HarnessError


@pytest.fixture
def sandbox(factory):
    with LocalSandbox("mcp-test") as sb:
        sb.reset(factory.tasks.get("fs-01"))
        yield sb


async def test_discovery_matches_declared(factory, sandbox):
    async with MCPSessionPool(factory.declared, ["fs", "sql"], sandbox.env()) as pool:
        assert set(pool.catalog.tools) == set(factory.declared.all_refs())
        assert pool.clients["fs"].protocol_version


async def test_fs_confined_to_workspace(factory, sandbox):
    async with MCPSessionPool(factory.declared, ["fs"], sandbox.env()) as pool:
        res = await pool.call("fs.read_file", {"path": "../state/task.sqlite"})
        assert res.is_error and "escapes" in res.text
        ok = await pool.call("fs.read_file", {"path": "config/app.ini"})
        assert not ok.is_error and "port = 8080" in ok.text


async def test_sql_query_is_read_only(factory, sandbox):
    async with MCPSessionPool(factory.declared, ["sql"], sandbox.env()) as pool:
        assert (await pool.call("sql.query", {"sql": "UPDATE invoices SET status='paid'"})).is_error
        assert (await pool.call("sql.execute", {"sql": "UPDATE invoices SET status='paid'; DELETE FROM invoices"})).is_error
        res = await pool.call("sql.query", {"sql": "SELECT COUNT(*) AS n FROM customers"})
        assert not res.is_error and json.loads(res.text) == {"columns": ["n"], "rows": [[5]]}
        bad = await pool.call("sql.query", {"sql": "SELECT nope FROM customers"})
        assert bad.is_error and "no such column" in bad.text


async def test_undeclared_tool_fails_provisioning(factory, sandbox):
    declared = factory.declared.model_copy(deep=True)
    declared.server("fs").tools = [t for t in declared.server("fs").tools if t.name != "delete_file"]
    with pytest.raises(HarnessError, match="drift"):
        async with MCPSessionPool(declared, ["fs"], sandbox.env()):
            pass


def test_tool_servers_do_not_inherit_api_keys(monkeypatch):
    from harness_factory.mcp.client import process_env
    monkeypatch.setenv("GROQ_API_KEY", "secret")
    monkeypatch.setenv("OPENROUTER_API_KEY", "secret")
    env = process_env({"HF_WORKSPACE": "/w"})
    assert "GROQ_API_KEY" not in env and "OPENROUTER_API_KEY" not in env
    assert env["HF_WORKSPACE"] == "/w" and "PATH" in env


def _child_servers() -> list[str]:
    import os
    import subprocess
    out = subprocess.run(["ps", "-o", "args=", "--ppid", str(os.getpid())], capture_output=True, text=True,
                         check=False).stdout
    return [line for line in out.splitlines() if "env.mcp.impl" in line]


async def test_cancelling_while_sessions_open_leaves_no_server_running(factory, sandbox):
    """Ctrl-C mid-campaign lands while a pool is still opening sessions: no tool server may outlive it."""
    import asyncio

    pool = MCPSessionPool(factory.declared, ["fs", "sql"], sandbox.env())
    job = asyncio.ensure_future(pool.__aenter__())
    await asyncio.sleep(0.3)                                     # first server up, second still starting
    job.cancel()
    with pytest.raises(asyncio.CancelledError):
        await job
    for _ in range(40):
        if not _child_servers():
            break
        await asyncio.sleep(0.05)
    assert _child_servers() == []
