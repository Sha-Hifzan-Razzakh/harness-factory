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
