"""LiteLLM path without network: litellm.acompletion is replaced by a fake that records its kwargs."""

from types import SimpleNamespace

import litellm
import pytest

from harness_factory.runner.runner import run_one


def _resp(content=None, tool_calls=None):
    msg = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(message=msg)],
                           usage=SimpleNamespace(prompt_tokens=100, completion_tokens=20))


def _call(id_, name, args):
    return SimpleNamespace(id=id_, function=SimpleNamespace(name=name, arguments=args))


@pytest.fixture
def fake_litellm(monkeypatch):
    calls = []
    script = [
        _resp(tool_calls=[_call("c1", "fs__write_file", '{"path": "notes/todo.txt", "content": "buy milk"}')]),
        _resp(content="Created notes/todo.txt."),
    ]

    async def fake(**kwargs):
        calls.append(kwargs)
        return script[len(calls) - 1]

    monkeypatch.setattr(litellm, "acompletion", fake)
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    return calls


async def test_litellm_request_shape_and_success(factory, fake_litellm):
    rec = await run_one(factory, factory.tasks.get("fs-01"), factory.recipes["v2-base"], "llama-3.3-70b", "groq")
    assert rec.status.value == "SUCCESS"
    first = fake_litellm[0]
    assert first["model"] == "groq/llama-3.3-70b-versatile"
    assert first["api_key"] == "test-key"
    assert first["temperature"] == 0.0 and first["seed"] == 0
    names = {t["function"]["name"] for t in first["tools"]}
    assert names == {"fs__list_dir", "fs__read_file", "fs__write_file"}       # task allowance, not the whole slice
    assert fake_litellm[1]["messages"][-1] == {"role": "tool", "tool_call_id": "c1",
                                               "content": "wrote 8 characters to notes/todo.txt"}
    assert rec.cost.tokens_in == 200 and rec.cost.usd > 0                      # priced at the reference rate


async def test_rate_limit_is_retried(factory, monkeypatch):
    attempts = []

    async def flaky(**kwargs):
        attempts.append(1)
        if len(attempts) == 1:
            raise litellm.RateLimitError("slow down", llm_provider="groq", model="x")
        return _resp(content="3") if len(attempts) > 2 else _resp(
            tool_calls=[_call("c1", "sql__query", '{"sql": "SELECT COUNT(*) FROM customers WHERE city = \'Dubai\'"}')])

    monkeypatch.setattr(litellm, "acompletion", flaky)
    monkeypatch.setenv("GROQ_API_KEY", "k")
    recipe = factory.recipes["v2-base"]
    recipe = recipe.model_copy(update={"config": recipe.config.model_copy(update={"backoff_s": 0.01})})
    rec = await run_one(factory, factory.tasks.get("sql-01"), recipe, "llama-3.3-70b", "groq")
    assert rec.status.value == "SUCCESS" and len(attempts) == 3


async def test_missing_key_is_a_provision_error(factory, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    rec = await run_one(factory, factory.tasks.get("fs-01"), factory.recipes["v2-base"], "qwen3-32b", "openrouter")
    assert rec.status.value == "ERROR"
    assert any("OPENROUTER_API_KEY" in str(g.get("detail")) for g in rec.grades)
