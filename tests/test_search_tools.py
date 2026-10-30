"""The search server (tools dimension): what it finds, what it refuses, who gets it, and that adding it moved no
existing identity."""

import pytest

from harness_factory.env.mcp.servers import SideEffect
from harness_factory.env.sandbox import LocalSandbox
from harness_factory.mcp.pool import MCPSessionPool
from harness_factory.provision.provisioner import allowed_tools, task_class
from harness_factory.runner.runner import run_one
from harness_factory.substrate.gateway import ScriptedBackend


@pytest.fixture
def service(factory):
    with LocalSandbox("search-test") as sb:
        sb.reset(factory.tasks.get("code-01"))                  # the service seed
        yield sb


async def test_grep_and_find_over_the_workspace(factory, service):
    async with MCPSessionPool(factory.declared, ["search"], service.env()) as pool:
        hits = (await pool.call("search.grep", {"pattern": r"2\.3\.1"})).text
        for where in ("pyproject.toml:3:", "service/__init__.py:3:", "requirements.txt:3:", "CHANGELOG.md:"):
            assert where in hits                                # every mention, with file and line
        only_toml = (await pool.call("search.grep", {"pattern": "version", "glob": "*.toml"})).text
        assert "pyproject.toml" in only_toml and "__init__" not in only_toml
        exact = (await pool.call("search.grep", {"pattern": "CHANGELOG", "glob": "*.md"})).text
        loose = (await pool.call("search.grep", {"pattern": "CHANGELOG", "glob": "*.md", "ignore_case": True})).text
        assert "CHANGELOG.md:1: # Changelog" not in exact and "CHANGELOG.md:1: # Changelog" in loose
        inis = (await pool.call("search.find", {"pattern": "*.ini"})).text
        assert all(f"service/config/{e}.ini" in inis for e in ("base", "dev", "staging", "prod"))


async def test_search_refuses_what_fs_would_refuse(factory, service):
    async with MCPSessionPool(factory.declared, ["search"], service.env()) as pool:
        escape = await pool.call("search.grep", {"pattern": "x", "path": "../state"})
        assert escape.is_error and "escapes" in escape.text
        bad = await pool.call("search.grep", {"pattern": "(unclosed"})
        assert bad.is_error and "invalid regular expression" in bad.text   # ToolError: the model sees why


def test_search_is_granted_by_the_tools_it_adds_nothing_to(factory):
    d, search = factory.declared, factory.recipes["v2-search"]
    code, mix, sql = (factory.tasks.get(t) for t in ("code-01", "mix-01", "sql-01"))
    assert {"search.find", "search.grep"} <= allowed_tools(search, code, d)     # list_dir + read_file
    assert "search.find" in allowed_tools(search, mix, d)                       # list_dir only: no grep
    assert "search.grep" not in allowed_tools(search, mix, d)
    assert not {r for r in allowed_tools(search, sql, d) if r.startswith("search.")}
    assert not {r for r in allowed_tools(factory.recipes["v2-base"], code, d) if r.startswith("search.")}
    assert task_class(code, d) == "UNKNOWN_PATH|REVERSIBLE|R1"                  # classes don't move
    writer = d.model_copy(deep=True)                                            # granted_by never grants a write
    spec = writer.spec("search.grep")
    spec.side_effect = SideEffect.REVERSIBLE
    assert "search.grep" not in allowed_tools(search, code, writer)


def test_adding_search_moved_no_existing_identity(factory):
    d = factory.declared
    # Phase 1's 87cf105d96ea, reset once by #42 (tool descriptions and sql.execute's escalation joined env_hash)
    assert d.env_hash(factory.recipes["v2-base"].tools.mcp_servers, "openai/gpt-oss-120b") == "a55c2fb739f1"
    assert factory.recipes["v2-base"].content_hash() == "79abd03818943f65"


async def test_a_model_can_search_in_v2_search_but_not_in_v2_base(factory):
    svc = factory.tasks.get("code-01").source_dir / "../../seeds/service/files"
    py, init = ((svc / p).resolve().read_text() for p in ("pyproject.toml", "service/__init__.py"))
    script = [
        {"tool_calls": [{"name": "search__grep", "arguments": {"pattern": r"2\.3\.1"}}]},
        {"tool_calls": [{"name": "fs__write_file", "arguments": {"path": "pyproject.toml",
                                                                 "content": py.replace("2.3.1", "2.4.0")}}]},
        {"tool_calls": [{"name": "fs__write_file", "arguments": {"path": "service/__init__.py",
                                                                 "content": init.replace("2.3.1", "2.4.0")}}]},
        {"content": "Bumped to 2.4.0 in pyproject.toml and service/__init__.py; shop-common is a dependency."},
    ]
    task = factory.tasks.get("code-01")
    rec = await run_one(factory, task, factory.recipes["v2-search"], "scripted/replay", "scripted",
                        backend=ScriptedBackend(script))
    assert rec.status.value == "SUCCESS" and rec.scores.trajectory == 1.0
    base = await run_one(factory, task, factory.recipes["v2-base"], "scripted/replay", "scripted",
                         backend=ScriptedBackend(script))
    assert any(v.kind == "tool_not_allowed" and "search.grep" in v.detail for v in base.violations)
