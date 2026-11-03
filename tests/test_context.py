"""Context management (ADR 0015): token accounting per turn, the pre-call overflow stop, compaction and output
spill. Offline, with the scripted provider; the context window is shrunk on the test's own provider catalog."""

import json
from pathlib import Path

import pytest

from harness_factory.observability.span import Span, SpanKind
from harness_factory.recipes.model import CompactionSpec, HarnessRecipe, ModelSlot
from harness_factory.recipes.validate import validate_recipe
from harness_factory.runner.runner import OVERRUN_OUTCOME_FACTOR, run_one
from harness_factory.shapes import compaction
from harness_factory.substrate.context import ContextWindow, estimate_tokens
from harness_factory.substrate.gateway import ScriptedBackend
from harness_factory.substrate.spill import SPILL_NAME, SpillStore

READ = {"tool_calls": [{"name": "fs__read_file", "arguments": {"path": "config/app.ini"}}]}
LIST = {"tool_calls": [{"name": "fs__list_dir", "arguments": {"path": "."}}]}
FIX = {"tool_calls": [{"name": "fs__write_file", "arguments": {
    "path": "config/app.ini", "content": "[server]\nhost = 0.0.0.0\nport = 9090\n\n[logging]\nlevel = info\n"}}]}
TODO = {"tool_calls": [{"name": "fs__write_file", "arguments": {"path": "notes/todo.txt", "content": "buy milk"}}]}


def spans(rec) -> list[dict]:
    return [json.loads(line) for line in Path(rec.trace_path).read_text().splitlines()]


def transcript(rec) -> dict:
    return json.loads(Path(rec.transcript_path).read_text())


def context(factory, n: int, model: str = "scripted/replay") -> None:
    factory.providers.served("scripted", model).caps.context_len = n


def compacting(recipe: HarnessRecipe, at: float = 0.1, keep: int = 1) -> HarnessRecipe:
    cfg = recipe.config.model_copy(update={"compaction": CompactionSpec(at=at, keep_last_turns=keep)})
    return recipe.model_copy(update={"config": cfg})


def assert_pairs_intact(messages: list[dict]) -> None:
    """Every tool result follows the assistant message that asked for it, with nothing else in between."""
    pending: set[str] = set()
    for m in messages:
        if m["role"] == "tool":
            assert m["tool_call_id"] in pending, f"orphan tool result {m['tool_call_id']}"
            pending.discard(m["tool_call_id"])
            continue
        assert not pending, f"tool calls without results before a {m['role']} message: {pending}"
        if m["role"] == "assistant":
            pending = {c["id"] for c in m.get("tool_calls") or []}


# ------------------------------------------------------------------ estimator

def test_estimate_is_deterministic_and_counts_bytes_messages_and_tools():
    msgs = [{"role": "system", "content": "x" * 300}, {"role": "user", "content": "hello"}]
    tools = [{"type": "function", "function": {"name": "fs__read_file", "description": "d", "parameters": {}}}]
    a = estimate_tokens(msgs, tools)
    assert a == estimate_tokens([dict(m) for m in msgs], [dict(t) for t in tools])          # same input, same number
    assert estimate_tokens(msgs) < a                                                       # tool schemas count
    assert estimate_tokens(msgs + [{"role": "user", "content": ""}]) > estimate_tokens(msgs)   # per-message overhead
    ascii_, wide = [{"role": "user", "content": "a" * 300}], [{"role": "user", "content": "語" * 300}]
    assert estimate_tokens(wide) > 2.5 * estimate_tokens(ascii_) - 30                      # UTF-8 bytes, not chars
    assert estimate_tokens([{"role": "user", "content": "a" * 3000}]) == 1000 + estimate_tokens(
        [{"role": "user", "content": ""}])                                                  # 3 bytes per token


def test_window_trigger_never_passes_the_limit():
    w = ContextWindow(context_len=10_000, reply=4_000)
    assert w.limit == 6_000 and w.trigger(0.5) == 5_000 and w.trigger(0.9) == 6_000


# ------------------------------------------------------------------ compaction plan: pairs never split

def _call(i, name="fs__read_file"):
    return {"role": "assistant", "content": None,
            "tool_calls": [{"id": f"c{i}", "type": "function", "function": {"name": name, "arguments": "{}"}}]}


def _res(i, text="r"):
    return {"role": "tool", "tool_call_id": f"c{i}", "content": text}


HEAD = [{"role": "system", "content": "sys"}, {"role": "user", "content": "Task: do it"}]


def test_plan_keeps_the_head_and_the_last_turns_and_never_cuts_between_a_call_and_its_result():
    two = {"role": "assistant", "content": None, "tool_calls": [
        {"id": "c2", "type": "function", "function": {"name": "a", "arguments": "{}"}},
        {"id": "c3", "type": "function", "function": {"name": "b", "arguments": "{}"}}]}
    history = [*HEAD, _call(1), _res(1), two, _res(2), _res(3), _call(4), _res(4)]
    for keep in (1, 2):
        p = compaction.plan(history, keep)
        assert p.head == HEAD
        assert p.head + p.region + p.tail == history
        assert p.tail[0]["role"] == "assistant" and p.region[-1]["role"] == "tool"
        assert_pairs_intact(p.head + p.region) and assert_pairs_intact(p.head + p.tail)
    assert compaction.plan(history, 3) is None                     # nothing older than the kept turns
    assert compaction.plan(HEAD, 1) is None


def test_plan_refuses_a_region_whose_calls_and_results_do_not_pair():
    history = [*HEAD, _call(1), _call(2), _res(2), _call(3), _res(3)]    # c1 never got a result
    assert compaction.plan(history, 1) is None


def test_plan_keeps_the_latest_instruction_and_moves_it_with_its_turn():
    stage = {"role": "user", "content": "Stage act: make the change"}
    history = [*HEAD, {"role": "user", "content": "Stage inspect: look"}, _call(1), _res(1), stage, _call(2),
               _res(2), _call(3), _res(3)]
    p = compaction.plan(history, 1)
    assert p.instruction == stage and p.tail == [_call(3), _res(3)]
    p = compaction.plan(history, 2)                                      # the instruction leads the kept turns
    assert p.tail[0] == stage and p.instruction is None


# ------------------------------------------------------------------ compaction in runs

async def test_v2_compaction_replaces_old_turns_and_is_charged_to_the_run(factory):
    context(factory, 8_000)
    task = factory.tasks.get("fs-02")
    script = [LIST, READ, LIST, READ, FIX, {"content": "Port changed to 9090."}]
    plain = await run_one(factory, task, factory.recipes["v2-base"], "scripted/replay", "scripted",
                          backend=ScriptedBackend(script))
    rec = await run_one(factory, task, compacting(factory.recipes["v2-base"]), "scripted/replay", "scripted",
                        backend=ScriptedBackend(script))
    assert plain.status.value == rec.status.value == "SUCCESS"
    trace = spans(rec)
    comps = [s for s in trace if s["kind"] == "compaction"]
    summaries = [s for s in trace if s["kind"] == "model_call" and s["attributes"].get("purpose") == "compaction"]
    assert comps and len(summaries) == len(comps)
    assert all(s["parent_id"] == c["span_id"] for s, c in zip(summaries, comps, strict=True))   # nested
    c = comps[0]["attributes"]
    assert c["est_tokens_after"] < c["est_tokens_before"] and c["tokens_dropped"] > 0 and c["replaced_messages"] > 0
    # never free: every summary is a step with tokens, in the cost and in the trajectory's step count
    assert rec.cost.model_calls == plain.cost.model_calls + len(summaries)
    assert rec.cost.tokens_in > 0 and all(s["tokens_in"] > 0 and s["tokens_out"] > 0 for s in summaries)
    t = transcript(rec)
    assert any(m["role"] == "user" and m["content"].startswith(compaction.SUMMARY_PREFIX) for m in t["messages"])
    assert len(t["messages"]) < len(transcript(plain)["messages"])
    assert_pairs_intact(t["messages"])
    assert t["snapshot"]["compactions"][0]["replaced"]                   # the full conversation is still recorded
    # every model call records the estimate next to the provider's count
    calls = [s for s in trace if s["kind"] == "model_call"]
    assert all(s["attributes"]["est_tokens_in"] > 0 and s["attributes"]["context_len"] == 8_000 for s in calls)


async def test_compaction_summary_uses_a_step_of_the_budget(factory):
    context(factory, 8_000)
    recipe = compacting(factory.recipes["v2-base"], at=0.075)          # 600 tokens: compacts before call 3
    recipe = recipe.model_copy(update={"config": recipe.config.model_copy(update={"max_steps": 4})})
    rec = await run_one(factory, factory.tasks.get("fs-02"), recipe, "scripted/replay", "scripted",
                        backend=ScriptedBackend([LIST, READ, LIST, READ, FIX, {"content": "done"}]))
    calls = [s for s in spans(rec) if s["kind"] == "model_call"]
    assert len(calls) == 4 and any(s["attributes"].get("purpose") == "compaction" for s in calls)
    assert any(v.kind == "budget_exceeded" for v in rec.violations)      # the summary took a step of the four


@pytest.mark.parametrize("recipe_id", ["v2-base", "v3-staged", "v4-planner"])
async def test_every_shape_compacts_through_common_and_still_solves(factory, recipe_id):
    context(factory, 8_000)
    rec = await run_one(factory, factory.tasks.get("fs-02"), compacting(factory.recipes[recipe_id], at=0.06),
                        "scripted/replay", "scripted")
    assert rec.status.value == "SUCCESS" and rec.scores.outcome == 1.0
    assert any(s["kind"] == "compaction" for s in spans(rec))
    assert_pairs_intact([m for m in transcript(rec)["messages"]])


async def test_without_compaction_nothing_is_summarised(factory):
    context(factory, 8_000)
    rec = await run_one(factory, factory.tasks.get("fs-02"), factory.recipes["v2-base"], "scripted/replay",
                        "scripted")
    assert rec.status.value == "SUCCESS" and not any(s["kind"] == "compaction" for s in spans(rec))


# ------------------------------------------------------------------ overflow: a harness stop, before the call

@pytest.mark.parametrize("recipe_id", ["v1-base", "v2-base", "v3-staged", "v4-planner", "v5-orchestrator"])
async def test_an_estimate_over_the_window_stops_before_calling_in_every_shape(factory, recipe_id):
    recipe = factory.recipes[recipe_id]
    slots = {k: v.model_copy(update={"max_output_tokens": 64}) for k, v in recipe.model_slots.items()}
    context(factory, 100)                        # a 64-token reply budget leaves 36 tokens: no prompt fits
    rec = await run_one(factory, factory.tasks.get("fs-01"), recipe.model_copy(update={"model_slots": slots}),
                        "scripted/replay", "scripted")
    assert rec.status.value == "FAIL" and rec.scores.outcome == 0                  # scored; nothing was written
    assert any(v.kind == "context_overflow" for v in rec.violations)
    assert "estimated" in next(g for g in rec.grades if g["grader_id"] == "run_error")["detail"]
    assert rec.cost.model_calls == 0                                               # no call was spent on it


async def test_an_overflow_after_solving_is_partial_with_the_overrun_factor(factory):
    task = factory.tasks.get("fs-01")
    probe = await run_one(factory, task, factory.recipes["v2-base"], "scripted/replay", "scripted",
                          backend=ScriptedBackend([TODO, {"content": "done"}]))
    first, second = (s["attributes"]["est_tokens_in"] for s in spans(probe) if s["kind"] == "model_call")
    context(factory, 2048 + (first + second) // 2)        # the first prompt fits, the second does not
    rec = await run_one(factory, task, factory.recipes["v2-base"], "scripted/replay", "scripted",
                        backend=ScriptedBackend([TODO, {"content": "done"}]))
    assert rec.status.value == "PARTIAL" and rec.scores.outcome == OVERRUN_OUTCOME_FACTOR
    assert any(v.kind == "context_overflow" for v in rec.violations) and rec.cost.model_calls == 1


# ------------------------------------------------------------------ spill

def _span() -> Span:
    return Span(trace_id="t", span_id="s", kind=SpanKind.tool_call, name="x", run_key="k", start_ns=0)


def test_spill_store_pages_caps_and_bad_arguments(tmp_path):
    store = SpillStore(tmp_path / "spill", above=100, head=20, tail=10, page=50, max_total=500, max_spills=3)
    assert store.shrink("short", _span()) == "short" and not store.active
    text = "".join(f"{i:04d}\r\n" for i in range(40))      # 240 chars, CRLF kept byte for byte
    span = _span()
    shown = store.shrink(text, span)
    assert store.active and span.attributes["spill_handle"] == "spill-1" and span.attributes["truncated_from"] == 240
    assert shown.startswith(text[:20]) and shown.endswith(text[-10:]) and "spill-1" in shown and "5 pages" in shown
    pages = [store.read({"handle": "spill-1", "page": p}, _span()) for p in range(1, 6)]
    assert not any(p.is_error for p in pages)
    assert "".join(p.text.split("\n", 1)[1] for p in pages) == text
    for bad in ({"handle": "spill-1", "page": 6}, {"handle": "spill-9", "page": 1}, {"page": 1}, "not json"):
        sp = _span()
        assert store.read(bad, sp).is_error and sp.error == "TOOL_ARGS"
    assert not list((tmp_path / "spill").glob("*.tmp"))
    store.shrink("y" * 200, _span())                        # 440 of 500 used
    sp = _span()
    capped = store.shrink("z" * 200, sp)                    # over the total cap: cut, and said so
    assert sp.attributes["spill_refused"] and capped.startswith("z" * 100) and "full" in capped
    assert store.sizes.keys() == {"spill-1", "spill-2"}


class Recording:
    """A ScriptedBackend that records the tool names offered on each call."""

    def __init__(self, turns):
        self.inner, self.offered = ScriptedBackend(turns), []

    async def complete(self, messages, tools, slot, provider, served):
        self.offered.append({t["function"]["name"] for t in tools})
        return await self.inner.complete(messages, tools, slot, provider, served)


async def test_an_oversized_output_spills_and_is_read_in_pages(factory):
    big = "".join(f"line {i:05d} " + "x" * 40 + "\n" for i in range(800))           # about 41k characters
    backend = Recording([
        {"tool_calls": [{"name": "fs__write_file", "arguments": {"path": "big.txt", "content": big}}]},
        {"tool_calls": [{"name": "fs__read_file", "arguments": {"path": "big.txt"}}]},
        {"tool_calls": [{"name": SPILL_NAME, "arguments": {"handle": "spill-1", "page": 2}}]},
        {"tool_calls": [{"name": SPILL_NAME, "arguments": {"handle": "spill-1", "page": 99}}]},
        TODO, {"content": "done"}])
    rec = await run_one(factory, factory.tasks.get("fs-01"), factory.recipes["v2-base"], "scripted/replay",
                        "scripted", backend=backend)
    assert SPILL_NAME not in backend.offered[1] and SPILL_NAME in backend.offered[2]    # offered once it spilled
    msgs = transcript(rec)["messages"]
    tool_texts = [m["content"] for m in msgs if m["role"] == "tool"]
    shown, page2, missing = tool_texts[1], tool_texts[2], tool_texts[3]
    assert len(shown) < 16_000 and "spill-1" in shown and shown.startswith(big[:100]) and shown.endswith(big[-100:])
    assert page2.startswith("[spill-1 page 2 of") and big[8_000:16_000] in page2
    assert "page 99 does not exist" in missing
    trace = spans(rec)
    reads = [s for s in trace if s["kind"] == "tool_call" and s["name"] == "spill.read"]
    assert len(reads) == 2 and rec.cost.tool_calls == 5                                 # spill reads are tool calls
    assert next(s for s in trace if s["name"] == "fs.read_file")["attributes"]["spill_chars"] == len(big)
    # the bad page is the model's invalid call; the spill area is outside the workspace, so not in the diff
    assert rec.scores.trajectory == pytest.approx(1 - 1 / 5)
    state = next(g for g in rec.grades if g["grader_id"] == "state_diff")
    assert "spill" not in json.dumps(state.get("checks", []))


async def test_spill_read_is_refused_before_anything_spilled(factory):
    rec = await run_one(factory, factory.tasks.get("fs-01"), factory.recipes["v2-base"], "scripted/replay",
                        "scripted", backend=ScriptedBackend([
                            {"tool_calls": [{"name": SPILL_NAME, "arguments": {"handle": "spill-1", "page": 1}}]},
                            TODO, {"content": "done"}]))
    assert any(v.kind == "tool_not_allowed" for v in rec.violations)
    assert "not available" in next(m for m in transcript(rec)["messages"] if m["role"] == "tool")["content"]


# ------------------------------------------------------------------ validation (RCP-05)

def test_catalog_recipes_fit_every_catalogued_context(factory):
    for rid, recipe in factory.recipes.items():
        assert validate_recipe(recipe, factory.declared, providers=factory.providers) == [], rid


def test_validation_checks_reply_budget_compaction_prompt_and_compactor_slot(factory):
    cat = factory.providers.model_copy(deep=True)
    cat.served("scripted", "scripted/replay").caps.context_len = 2_048
    base = factory.recipes["v2-base"]
    rules = lambda r, **kw: {i.rule for i in validate_recipe(r, factory.declared, providers=cat, **kw)}
    assert "context_fits_reply" in rules(base)                                         # hfx validate: every model
    assert "context_fits_reply" in rules(base.with_model("scripted/replay"), provider_id="scripted")
    assert "context_fits_reply" not in rules(base.with_model("scripted/noisy"), provider_id="scripted")
    bad = compacting(base)
    bad = bad.model_copy(update={"config": bad.config.model_copy(update={
        "compaction": CompactionSpec(prompt="compaction/nope", keep_last_turns=50)})})
    got = rules(bad)
    assert {"prompt_exists", "compaction_fits_context"} <= got
    pinned = base.model_copy(update={"model_slots": {**base.model_slots,
                                                     "compactor": ModelSlot(role="compactor", canonical_model="x")}})
    assert "compactor_model_unpinned" in rules(pinned)


def test_compaction_is_a_new_optional_field_that_leaves_existing_hashes_alone(factory):
    base = factory.recipes["v2-base"]
    assert base.content_hash() == "79abd03818943f65"
    assert base.config.compaction is None
    compact = factory.recipes["v2-compact"]
    assert compact.config.compaction == CompactionSpec(at=0.75, keep_last_turns=2)
    assert compact.content_hash() != base.content_hash()
    assert "compaction/summary" in compact.prompt_refs() and "compaction/summary" not in base.prompt_refs()
