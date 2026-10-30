"""Other agents as tools (EXT-10): an agent behind a remote MCP server is a tool with a declared risk level, and the
usage it reports counts toward the run's token budget and cost.

A stand-in agent (tests/remote_agent_server.py) runs on 127.0.0.1 and reports canned usage; no model is called."""

import dataclasses
import json
import socket
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from harness_factory.env.mcp.servers import USAGE_META, AgentTool, MCPServerSpec, SideEffect, ToolSpec
from harness_factory.mcp.client import ToolResult
from harness_factory.mcp.pool import MCPSessionPool
from harness_factory.recipes.model import ToolSlice
from harness_factory.runner.runner import run_one
from harness_factory.substrate.gateway import ScriptedBackend
from harness_factory.substrate.tools import ToolExecutor

AGENT = AgentTool(max_calls=2, price_in=0.5, price_out=1.5)          # reference rates, per million tokens
QUESTION = "What counts as net revenue?"
ASK = {"tool_calls": [{"name": "analyst__ask", "arguments": {"question": QUESTION}}]}
UNMETERED = {"tool_calls": [{"name": "analyst__ask_unmetered", "arguments": {"question": QUESTION}}]}
ANSWER = "Net revenue is paid order value minus refunds; test accounts (@example.test) are excluded."
AGENT_TOKENS = 1200 + 80                                              # what the stand-in reports per call


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def agent_server():
    port = free_port()
    proc = subprocess.Popen([sys.executable, str(Path(__file__).parent / "remote_agent_server.py"), str(port)])
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.1)
    else:
        proc.kill()
        pytest.fail("stand-in agent server did not start")
    yield {"url": f"http://127.0.0.1:{port}/mcp", "proc": proc}
    proc.kill()
    proc.wait()


def agent_spec(url: str, hashes: dict | None = None, agent: AgentTool = AGENT) -> MCPServerSpec:
    hashes = hashes or {}
    return MCPServerSpec(id="analyst", transport="streamable_http", url=url, version="1.0", tools=[
        ToolSpec(name=n, side_effect=SideEffect.READ, risk="R1", timeout_s=10, agent=agent,
                 **hashes.get(f"analyst.{n}", {})) for n in ("ask", "ask_unmetered")])


async def setup(factory, url, monkeypatch, **config):
    """The factory with the agent declared (schema hashes discovered, as `hfx tools freeze` would), a v2 recipe that
    opens it, and a task that allows it. config overrides the recipe's runtime limits."""
    monkeypatch.setenv("HF_ALLOW_REMOTE", "1")
    probe = factory.declared.model_copy(update={"servers": [agent_spec(url)]})
    async with MCPSessionPool(probe, ["analyst"], {}) as pool:
        hashes = {r: {"schema_hash": pool.catalog.schema_hash(r), "description_hash": pool.catalog.description_hash(r)}
                  for r in ("analyst.ask", "analyst.ask_unmetered")}
    declared = factory.declared.model_copy(update={"servers": [*factory.declared.servers, agent_spec(url, hashes)]})
    f = dataclasses.replace(factory, declared=declared)
    base = factory.recipes["v2-base"]
    recipe = base.model_copy(update={"id": "v2-analyst", "config": base.config.model_copy(update=config),
                                     "tools": ToolSlice(tool_ids=["fs.*", "sql.*", "analyst.*"],
                                                        mcp_servers=["fs", "sql", "analyst"])})
    task = factory.tasks.get("sql-03")
    task = task.model_copy(update={"allowed_tools": [*task.allowed_tools, "analyst.ask", "analyst.ask_unmetered"]})
    return f, recipe, task


def calls(rec, name):
    spans = [json.loads(line) for line in Path(rec.trace_path).read_text().splitlines()]
    return [s for s in spans if s["kind"] == "tool_call" and s["name"] == name]


def tool_texts(rec):
    t = json.loads(Path(rec.transcript_path).read_text())
    return [m["content"] for m in t["messages"] if m["role"] == "tool"]


# ------------------------------------------------------------------ catalog rules and identity

def test_an_agent_tool_needs_a_remote_server():
    with pytest.raises(ValueError, match="agent tools need a remote server"):
        MCPServerSpec(id="x", module="x", tools=[ToolSpec(name="ask", side_effect=SideEffect.READ, risk="R1",
                                                          agent=AgentTool())])


def test_the_agent_field_keeps_existing_env_hashes_and_its_prices_are_not_hashed(factory):
    plain = ToolSpec(name="read_file", side_effect=SideEffect.READ, risk="R0", schema_hash="abc")
    assert plain.hashed() == plain.model_dump(mode="json", exclude={"granted_by", "agent", "description_hash",
                                                                    "escalation"})            # as before EXT-10
    d = factory.declared
    h = lambda agent: d.model_copy(update={"servers": [*d.servers, agent_spec("https://a.example/mcp", agent=agent)]}
                                   ).env_hash(["fs", "sql", "analyst"], "m")
    assert h(AGENT) == h(AGENT.model_copy(update={"price_in": 9.0}))                       # prices: not identity
    assert h(AGENT) != h(AGENT.model_copy(update={"max_calls": 5}))                        # the limit is


def test_usage_is_read_from_the_answers_meta_and_priced_at_reference_rates_when_usd_is_missing():
    span = lambda: SimpleNamespace(attributes={}, error=None, tokens_in=0, tokens_out=0)
    meter = lambda result, s: ToolExecutor._meter("analyst.ask", AGENT, result, s)
    s = span()
    meter(ToolResult(text="ok", meta={USAGE_META: {"tokens_in": 2_000_000, "tokens_out": 1_000_000}}), s)
    assert (s.tokens_in, s.tokens_out, s.attributes["usd"]) == (2_000_000, 1_000_000, 2.5)    # 2x0.5 + 1x1.5
    s = span()
    meter(ToolResult(text="ok", meta={USAGE_META: {"tokens_in": 10, "tokens_out": 5, "usd": 0.25, "model": "m"}}), s)
    assert s.attributes["usd"] == 0.25 and s.attributes["agent_model"] == "m"              # reported cost wins
    for bad in (None, {}, {USAGE_META: {"tokens_in": 10}}, {USAGE_META: {"tokens_in": -1, "tokens_out": 0}}):
        s = span()
        out = meter(ToolResult(text="secret answer", meta=bad), s)
        assert out.infra == "agent_usage_missing" and "secret answer" not in out.text      # withheld, not free
    s = span()
    out = meter(ToolResult(text="ask a question", is_error=True), s)
    assert out.text == "ask a question" and not s.attributes                              # a refusal costs nothing


# ------------------------------------------------------------------ live runs

async def test_an_agents_usage_counts_toward_the_runs_cost(factory, agent_server, monkeypatch):
    f, recipe, task = await setup(factory, agent_server["url"], monkeypatch)
    rec = await run_one(f, task, recipe, "scripted/replay", "scripted",
                        backend=ScriptedBackend([ASK, {"content": "Harbor Logistics"}]))
    (call,) = calls(rec, "analyst.ask")
    assert call["status"] == "ok" and call["tokens_in"] == 1200 and call["tokens_out"] == 80
    assert call["attributes"]["agent_model"] == "stand-in/analyst-1"
    assert tool_texts(rec) == [ANSWER]
    c = rec.cost
    assert (c.agent_calls, c.agent_tokens) == (1, AGENT_TOKENS)
    assert c.agent_usd == pytest.approx(AGENT.usd(1200, 80))
    assert c.tokens_in + c.tokens_out >= AGENT_TOKENS + 1                                  # inside the run's own total
    assert c.usd == pytest.approx(c.agent_usd)                                             # scripted model: free


async def test_an_agents_tokens_come_out_of_the_runs_token_budget(factory, agent_server, monkeypatch):
    f, recipe, task = await setup(factory, agent_server["url"], monkeypatch, max_tokens=1000)
    rec = await run_one(f, task, recipe, "scripted/replay", "scripted",
                        backend=ScriptedBackend([ASK, {"content": "Harbor Logistics"}]))
    assert rec.cost.model_calls == 1                                                       # no room for a second step
    assert any("max_tokens" in v.detail for v in rec.violations if v.kind == "budget_exceeded")


async def test_calls_past_max_calls_are_refused_and_count_as_invalid(factory, agent_server, monkeypatch):
    f, recipe, task = await setup(factory, agent_server["url"], monkeypatch)
    rec = await run_one(f, task, recipe, "scripted/replay", "scripted",
                        backend=ScriptedBackend([ASK, ASK, ASK, {"content": "Harbor Logistics"}]))
    assert [s["status"] for s in calls(rec, "analyst.ask")] == ["ok", "ok", "error"]      # max_calls: 2
    assert "limit reached" in tool_texts(rec)[2]
    assert rec.cost.agent_calls == 2 and rec.scores.trajectory < 1.0


async def test_an_answer_without_usage_is_withheld_as_the_environments_failure(factory, agent_server, monkeypatch):
    f, recipe, task = await setup(factory, agent_server["url"], monkeypatch)
    rec = await run_one(f, task, recipe, "scripted/replay", "scripted",
                        backend=ScriptedBackend([UNMETERED, {"content": "Harbor Logistics"}]))
    (call,) = calls(rec, "analyst.ask_unmetered")
    assert call["status"] == "error" and call["attributes"]["infra"] == "agent_usage_missing"
    assert ANSWER not in tool_texts(rec)[0]
    assert rec.scores.trajectory == 1.0 and rec.cost.agent_calls == 0                      # not the model's fault


async def test_a_recorded_agent_replays_with_the_same_usage(factory, agent_server, monkeypatch, tmp_path):
    monkeypatch.setenv("HF_REMOTE_RECORDINGS", str(tmp_path / "rec"))
    f, recipe, task = await setup(factory, agent_server["url"], monkeypatch)
    script = [ASK, {"content": "Harbor Logistics"}]
    monkeypatch.setenv("HF_REMOTE_MODE", "record")
    live = await run_one(f, task, recipe, "scripted/replay", "scripted", backend=ScriptedBackend(script))
    agent_server["proc"].kill()
    agent_server["proc"].wait()
    monkeypatch.delenv("HF_ALLOW_REMOTE")
    monkeypatch.setenv("HF_REMOTE_MODE", "replay")
    rep = await run_one(f, task, recipe, "scripted/replay", "scripted", backend=ScriptedBackend(script))
    assert tool_texts(rep) == tool_texts(live) == [ANSWER]
    assert (rep.cost.agent_tokens, rep.cost.agent_usd) == (live.cost.agent_tokens, live.cost.agent_usd)
    assert rep.run_key == live.run_key
