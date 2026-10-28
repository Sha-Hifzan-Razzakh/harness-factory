"""LiteLLM path without network: litellm.acompletion is replaced by a fake that records its kwargs."""

import asyncio
from types import SimpleNamespace

import httpx
import litellm
import pytest

from harness_factory.recipes.validate import validate_recipe
from harness_factory.results.store import ResultsStore
from harness_factory.runner.runner import run_one
from harness_factory.substrate.budget import BudgetGuard
from harness_factory.substrate.errors import ErrorKind, HarnessError
from harness_factory.substrate.gateway import ModelGateway


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


# ---------------------------------------------------------------- failure classification (review fixes 3-5)

_REQ = httpx.Request("POST", "https://example.invalid/v1/chat/completions")


def _status(cls, code, msg="boom", **kw):
    return cls(msg, llm_provider="groq", model="x", response=httpx.Response(code, request=_REQ), **kw)


@pytest.fixture
def no_backoff(monkeypatch):
    waits = []

    async def fake_sleep(s):
        waits.append(s)

    monkeypatch.setattr(ModelGateway, "_sleep", staticmethod(fake_sleep))
    monkeypatch.setenv("GROQ_API_KEY", "k")
    return waits


def _script(monkeypatch, *steps):
    """steps: exceptions to raise or responses to return, in order (the last one repeats)."""
    calls = []

    async def fake(**kwargs):
        calls.append(kwargs)
        step = steps[min(len(calls), len(steps)) - 1]
        if isinstance(step, BaseException):
            raise step
        return step

    monkeypatch.setattr(litellm, "acompletion", fake)
    return calls


async def test_transient_5xx_is_retried_and_not_counted_as_a_step(factory, monkeypatch, no_backoff):
    _script(monkeypatch, _status(litellm.InternalServerError, 502),
            _resp(tool_calls=[_call("c1", "sql__query", '{"sql": "SELECT COUNT(*) FROM customers WHERE city=\'Dubai\'"}')]),
            _resp(content="3"))
    rec = await run_one(factory, factory.tasks.get("sql-01"), factory.recipes["v2-base"], "llama-3.3-70b", "groq")
    assert rec.status.value == "SUCCESS"
    assert rec.cost.model_calls == 2 and rec.cost.failed_calls == 1
    assert no_backoff == [2.0]


async def test_persistent_provider_failure_is_incomplete_recorded_and_unscored(factory, monkeypatch, no_backoff,
                                                                               tmp_path):
    factory.store = ResultsStore(f"sqlite:///{tmp_path / 'r.db'}")
    _script(monkeypatch, _status(litellm.ServiceUnavailableError, 503))
    rec = await run_one(factory, factory.tasks.get("fs-01"), factory.recipes["v2-base"], "llama-3.3-70b", "groq")
    assert rec.status.value == "INCOMPLETE" and "PROVIDER_ERROR" in rec.grades[-1]["detail"]
    assert rec.trace_path and len(factory.store.rows()) == 1                    # recorded, with a trace
    _, cells = factory.store.aggregate()
    assert cells[0].excluded == 1 and cells[0].e.n == 0                        # never averaged in as a zero


async def test_retry_after_header_is_honoured(factory, monkeypatch, no_backoff):
    _script(monkeypatch, litellm.RateLimitError("slow down", llm_provider="groq", model="x",
                                                headers={"retry-after": "7"}), _resp(content="3"))
    await run_one(factory, factory.tasks.get("sql-01"), factory.recipes["v2-base"], "llama-3.3-70b", "groq")
    assert no_backoff == [7.0]


async def test_provider_daily_quota_stops_every_worker(factory, monkeypatch, no_backoff):
    calls = _script(monkeypatch, litellm.RateLimitError(
        "Rate limit reached for model on tokens per day (TPD): Limit 100000", llm_provider="groq", model="x"))
    a = await run_one(factory, factory.tasks.get("fs-01"), factory.recipes["v2-base"], "llama-3.3-70b", "groq")
    b = await run_one(factory, factory.tasks.get("fs-02"), factory.recipes["v2-base"], "llama-3.3-70b", "groq")
    assert a.status.value == b.status.value == "INCOMPLETE"
    assert len(calls) == 1 and no_backoff == []                 # not retried; the second run never calls out


async def test_auth_failure_is_a_setup_error_not_a_harness_result(factory, monkeypatch, no_backoff):
    _script(monkeypatch, _status(litellm.AuthenticationError, 401, "invalid api key"))
    rec = await run_one(factory, factory.tasks.get("fs-01"), factory.recipes["v2-base"], "llama-3.3-70b", "groq")
    assert rec.status.value == "ERROR" and "CONFIG" in rec.grades[-1]["detail"] and no_backoff == []


async def test_context_overflow_counts_against_the_harness(factory, monkeypatch, no_backoff):
    _script(monkeypatch, litellm.ContextWindowExceededError("too long", model="x", llm_provider="groq"))
    rec = await run_one(factory, factory.tasks.get("fs-01"), factory.recipes["v2-base"], "llama-3.3-70b", "groq")
    assert rec.status.value == "FAIL"                           # scored (not excluded); nothing was written
    assert any(v.kind == "context_overflow" for v in rec.violations)


async def test_malformed_tool_call_is_the_models_failure_not_setup(factory, monkeypatch, no_backoff):
    """Groq answers a broken tool call with 400 tool_use_failed: dropping it would flatter the model."""
    _script(monkeypatch, _status(litellm.BadRequestError, 400, "tool_use_failed: Failed to call a function"))
    rec = await run_one(factory, factory.tasks.get("fs-01"), factory.recipes["v2-base"], "llama-3.3-70b", "groq")
    assert rec.status.value == "FAIL" and any(v.kind == "malformed_output" for v in rec.violations)


async def test_a_400_after_the_run_started_is_the_models_doing(factory, monkeypatch, no_backoff):
    _script(monkeypatch,
            _resp(tool_calls=[_call("c1", "fs__write_file", '{"path": "notes/todo.txt", "content": "buy milk"}')]),
            _status(litellm.BadRequestError, 400, "messages: invalid content"))
    rec = await run_one(factory, factory.tasks.get("fs-01"), factory.recipes["v2-base"], "llama-3.3-70b", "groq")
    assert rec.status.value == "SUCCESS"                        # the write landed before the broken turn
    assert any(v.kind == "malformed_output" for v in rec.violations)


async def test_a_400_on_the_first_call_is_setup(factory, monkeypatch, no_backoff):
    _script(monkeypatch, _status(litellm.BadRequestError, 400, "unsupported parameter: foo"))
    rec = await run_one(factory, factory.tasks.get("fs-01"), factory.recipes["v2-base"], "llama-3.3-70b", "groq")
    assert rec.status.value == "ERROR" and "CONFIG" in rec.grades[-1]["detail"]


async def test_timeouts_are_retried_once_only(factory, monkeypatch, no_backoff):
    calls = _script(monkeypatch, litellm.Timeout("read timed out", model="x", llm_provider="groq"))
    rec = await run_one(factory, factory.tasks.get("fs-01"), factory.recipes["v2-base"], "llama-3.3-70b", "groq")
    assert rec.status.value == "INCOMPLETE" and len(calls) == 2 and "timeout" in rec.grades[-1]["detail"]


async def test_failed_attempt_time_is_not_charged_to_the_harness(factory, no_backoff):
    from harness_factory.observability.tracer import Tracer
    from harness_factory.recipes.model import ModelSlot

    class SlowFail:
        async def complete(self, *a):
            await asyncio.sleep(0.3)
            raise litellm.InternalServerError("502", llm_provider="groq", model="x")

    gw = ModelGateway(factory.providers, factory.ledger, Tracer("t"), "groq", backend=SlowFail(), retries=0)
    with pytest.raises(HarnessError) as e:
        await gw.complete(ModelSlot(canonical_model="llama-3.3-70b"), [{"role": "user", "content": "x"}], [])
    assert e.value.kind == ErrorKind.PROVIDER_ERROR
    assert gw.infra_wait_s >= 0.3


async def test_hung_call_is_cut_off(factory, monkeypatch, no_backoff):
    async def hang(**kwargs):
        await asyncio.sleep(60)

    monkeypatch.setattr(litellm, "acompletion", hang)
    base = factory.recipes["v2-base"]
    quick = base.model_copy(update={"config": base.config.model_copy(update={"call_timeout_s": 1, "retries": 0})})
    rec = await asyncio.wait_for(run_one(factory, factory.tasks.get("fs-01"), quick, "llama-3.3-70b", "groq"), 15)
    assert rec.status.value == "INCOMPLETE"


async def test_a_crash_inside_a_run_is_still_recorded(factory, monkeypatch, tmp_path):
    from harness_factory.shapes.v2 import ToolLoopShape
    factory.store = ResultsStore(f"sqlite:///{tmp_path / 'r.db'}")

    async def boom(self, inst):
        raise ValueError("bug in a shape")

    monkeypatch.setattr(ToolLoopShape, "run", boom)
    rec = await run_one(factory, factory.tasks.get("fs-01"), factory.recipes["v2-base"], "scripted/replay",
                        "scripted")
    assert rec.status.value == "ERROR" and "bug in a shape" in rec.grades[-1]["detail"]
    assert rec.trace_path and len(factory.store.rows()) == 1


def test_wall_clock_excludes_infrastructure_time(factory):
    cfg = factory.recipes["v2-base"].config.model_copy(update={"wall_time_s": 10})
    excluded = {"s": 0.0}
    budget = BudgetGuard(cfg, excluded_s=lambda: excluded["s"])
    budget.start()
    budget.started_at -= 12                              # 12 s have passed...
    assert budget.exceeded_by() == "wall_time_s 10"
    excluded["s"] = 5                                    # ...5 of them waiting on quota or retry backoff
    assert budget.exceeded_by() is None


def test_v3_workflow_must_fit_its_step_budget(factory):
    v3 = factory.recipes["v3-staged"]
    tight = v3.model_copy(update={"config": v3.config.model_copy(update={"max_steps": 20})})
    assert any(i.rule == "workflow_fits_budget" for i in validate_recipe(tight, factory.declared))
    assert validate_recipe(v3, factory.declared) == []
