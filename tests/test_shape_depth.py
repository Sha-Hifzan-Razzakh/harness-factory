"""Shape depth (SUB-08, SHP-04): structured plans for V4 and V5, V5 workers as a concurrent graph, and parallel READ
tool calls. Offline: scripted backends only; concurrency is proven by order of events, never by timing."""

import asyncio
import json
import re
import shutil
from pathlib import Path

import pytest

from harness_factory.mcp.pool import MCPSessionPool
from harness_factory.observability.tracer import Tracer
from harness_factory.recipes.model import PlanSpec, load_recipes
from harness_factory.recipes.validate import validate_recipe
from harness_factory.runner.runner import run_one
from harness_factory.shapes import plans
from harness_factory.substrate.gateway import Completion, ModelGateway, ScriptedBackend

README = ("fs__read_file", {"path": "notes/README.txt"})
INI = ("fs__read_file", {"path": "config/app.ini"})
LIST = ("fs__list_dir", {"path": "notes"})
TODO = ("fs__write_file", {"path": "notes/todo.txt", "content": "buy milk"})


def step(sid, tool, deps=(), instruction=None):
    return {"id": sid, "instruction": instruction or f"use {tool}", "tools": [tool], "depends_on": list(deps),
            "done_when": "it answered"}


DIAMOND = {"steps": [step("s1", README[0]), step("s2", INI[0], ["s1"]), step("s3", LIST[0], ["s1"]),
                     step("s4", TODO[0], ["s2", "s3"])]}
DIAMOND_CALLS = {"s1": README, "s2": INI, "s3": LIST, "s4": TODO}


def recipes(tmp_path, **files) -> dict:
    for f in ("base/v5_orchestrator", "base/v2_tool_loop", "base/v4_planner", "matrix/v4_structured",
              "matrix/v5_parallel", "matrix/v2_parallel_tools"):
        shutil.copy(f"catalog/recipes/{f}.yaml", tmp_path / f"{f.split('/')[1]}.yaml")
    for name, text in files.items():
        (tmp_path / f"{name}.yaml").write_text(text)
    return load_recipes(tmp_path)


def spans(rec):
    return [json.loads(line) for line in Path(rec.trace_path).read_text().splitlines()]


def snapshot(rec):
    return json.loads(Path(rec.transcript_path).read_text())["snapshot"]


def delegate_spans(rec) -> dict[str, dict]:
    """The worker (delegate.run) span of each step, by scope ("step:3")."""
    return {s["attributes"]["scope"]: s for s in spans(rec) if s["kind"] == "tool_call" and s["name"] == "delegate.run"}


def waits_for(event):
    """A close hook: wait (bounded) until another worker logged `event`. If workers ran one after another, the event
    can't happen while this one waits, and the order assertions fail."""
    async def hook(backend):
        for _ in range(200):
            if event in backend.events:
                return
            await asyncio.sleep(0.01)
    return hook


class GraphBackend:
    """Planner calls reply with the next of `plans`. A worker reads its step id from "Your part: [sX]", makes that
    step's call (`calls`), then closes with `replies[sX]` (default "done sX"), after `before_close[sX]` if any.
    `greedy` workers call their tool on every turn, until their budget stops them."""

    def __init__(self, plans_, calls, replies=None, before_close=None, greedy=False):
        self.plans, self.calls = list(plans_), calls
        self.replies, self.before_close, self.greedy = replies or {}, before_close or {}, greedy
        self.events: list[str] = []
        self.planner_prompts: list[str] = []

    async def complete(self, messages, tools, slot, provider, served):
        if slot.role == "planner":
            self.planner_prompts.append(messages[-1]["content"])
            plan = self.plans.pop(0)
            return Completion(message={"role": "assistant", "content": plan if isinstance(plan, str)
                                       else json.dumps(plan)}, tokens_in=10, tokens_out=10)
        task = next(m["content"] for m in messages if m["role"] == "user")
        sid = re.search(r"Your part: \[(\w+)\]", task).group(1)
        if messages[-1]["role"] != "tool" or self.greedy:
            if messages[-1]["role"] != "tool":
                self.events.append(f"start:{sid}")
            name, args = self.calls[sid]
            call = {"id": f"c_{sid}_{len(messages)}", "type": "function",
                    "function": {"name": name, "arguments": json.dumps(args)}}
            return Completion(message={"role": "assistant", "content": None, "tool_calls": [call]},
                              tokens_in=10, tokens_out=10)
        if sid in self.before_close:
            await self.before_close[sid](self)
        self.events.append(f"end:{sid}")
        return Completion(message={"role": "assistant", "content": self.replies.get(sid, f"done {sid}")},
                          tokens_in=10, tokens_out=10)


# ------------------------------------------------------------------ plan validation

def test_a_valid_plan_runs_in_dependency_order_and_fences_or_a_bare_list_are_the_same_plan():
    rev = {"steps": [step("d", "t", ["b", "c"]), step("c", "t", ["a"]), step("b", "t", ["a"]), step("a", "t")]}
    assert [s.id for s in plans.parse(json.dumps(rev), 8)] == ["a", "c", "b", "d"]    # first ready, planner order
    fenced = f"```json\n{json.dumps(DIAMOND)}\n```"
    assert [s.id for s in plans.parse(fenced, 8)] == ["s1", "s2", "s3", "s4"]
    assert [s.id for s in plans.parse(json.dumps(DIAMOND["steps"]), 8)] == ["s1", "s2", "s3", "s4"]
    s2 = plans.parse(json.dumps(DIAMOND), 8, tools=["fs__read_file", "fs__list_dir", "fs__write_file"])[1]
    assert s2.text() == "[s2] use fs__read_file (after s1; tools: fs__read_file; done when: it answered)"
    dotted = {"steps": [step("a", "fs.read_file")]}
    assert plans.parse(json.dumps(dotted), 8, tools=["fs__read_file"])[0].tools == ["fs__read_file"]
    replan = {"steps": [step("s5", TODO[0], ["s1"])]}
    assert plans.parse(json.dumps(replan), 8, done={"s1"})[0].depends_on == ["s1"]      # may build on done steps


@pytest.mark.parametrize("plan, why", [
    ("1. read\n2. write", "not a JSON object"),
    ({"steps": []}, "steps"),
    ({"steps": [{"id": "a", "instruction": "x"}]}, "done_when"),
    ({"steps": [step("a", "t"), step("a", "t")]}, "duplicate step ids: a"),
    ({"steps": [step("a", "t", ["z"])]}, "unknown steps: z"),
    ({"steps": [step("a", "t", ["a"])]}, "depends on itself"),
    ({"steps": [step("a", "t", ["b"]), step("b", "t", ["a"])]}, "dependency cycle"),
    ({"steps": [step(f"s{i}", "t") for i in range(4)]}, "4 steps; at most 3"),
    ({"steps": [step("a", "fs__delete_file")]}, "unknown tools fs__delete_file"),
    ({"steps": [step("s1", "t")]}, "already used by finished steps: s1"),
])
def test_an_invalid_plan_is_refused_with_a_reason_the_planner_can_act_on(plan, why):
    with pytest.raises(plans.PlanInvalid, match=re.escape(why)):
        plans.parse(plan if isinstance(plan, str) else json.dumps(plan), 3,
                    tools=["t"] if "fs__delete_file" in json.dumps(plan) else None, done={"s1"})


# ------------------------------------------------------------------ identity and validation

def test_no_existing_hash_changes_and_the_no_op_values_hash_as_unset(factory):
    # As on main before this change (V5's own hash is checked against itself below: #42 changes what it covers).
    main = {"v1-base": "abb80de0446a63ad", "v2-base": "79abd03818943f65", "v3-staged": "0280c3a66705ce4b",
            "v4-planner": "c7a8990da8b06ddf", "v2-delegate": "fb1be0206bb9f7b1", "v2-recover": "5ebbdab69f8ffddc"}
    assert {r: factory.recipes[r].content_hash() for r in main} == main
    v5 = factory.recipes["v5-orchestrator"]
    noop = PlanSpec.model_validate({**v5.planning.model_dump(), "structured": False, "max_parallel": 1,
                                    "plan_retries": 1})
    assert (noop.structured, noop.max_parallel, noop.plan_retries) == (None, None, None)
    assert v5.model_copy(update={"planning": noop}).content_hash() == v5.content_hash()
    on = v5.model_copy(update={"planning": noop.model_copy(update={"structured": True})})
    assert on.content_hash() != v5.content_hash()
    assert {"plan/planner_structured", "plan/replan_structured", "plan/fix_structured"} <= set(on.prompt_refs())


def test_structured_fields_are_validated(factory, tmp_path):
    rs = recipes(tmp_path,
                 par4="id: par4\nbase: v4-structured\nplanning: {max_parallel: 2}\n",
                 bare="id: bare\nbase: v5-orchestrator\nplanning: {max_parallel: 2, plan_retries: 2}\n",
                 tight="id: tight\nbase: v4-planner\nplanning: {structured: true}\n",
                 flag1="id: flag1\nbase: v4-planner\nconfig: {parallel_tool_calls: true}\n")
    rules = lambda r: [i.rule for i in validate_recipe(rs[r], factory.declared)]
    assert "max_parallel_v5_only" in rules("par4")
    assert rules("bare").count("structured_plan_required") == 2
    assert "planning_fits_budget" in rules("tight")          # 2 x (1 + 8 x 3) + 1 re-ask = 51 > 50
    assert rs["v4-structured"].planning.worst_case_calls() == 51
    for ok in ("v4-structured", "v5-parallel", "v2-parallel-tools", "flag1"):
        assert not rules(ok), ok                              # parallel_tool_calls is no longer an inert field


# ------------------------------------------------------------------ structured plans: recovery and malformed stop

PLAN_TODO = json.dumps({"steps": [step("s1", "fs__write_file", instruction="write notes/todo.txt")]})
WRITE = {"tool_calls": [{"name": TODO[0], "arguments": TODO[1]}]}


async def test_an_invalid_plan_is_re_asked_with_the_reason_and_counts_as_an_invalid_call(factory):
    script = [{"content": "1. write the todo"}, {"content": PLAN_TODO}, WRITE, {"content": "Created it."}]
    rec = await run_one(factory, factory.tasks.get("fs-01"), factory.recipes["v4-structured"], "scripted/replay",
                        "scripted", backend=ScriptedBackend(script))
    assert rec.status.value == "SUCCESS"
    assert rec.scores.trajectory == 0.5                       # 1 tool call + 1 rejected plan, 1 invalid: never free
    planner = [s for s in spans(rec) if s["kind"] == "model_call"][:2]
    assert planner[0]["error"] == "MODEL_PARSE" and "plan_invalid" in planner[0]["attributes"]["rejected"]
    assert planner[0]["status"] == "ok" and planner[1].get("error") is None      # its tokens count in C
    assert {s["attributes"]["response_format"] for s in planner} == {"prompt_only"}   # scripted reads the prompt
    assert any(v.kind == "plan_invalid" and "not a JSON object" in v.detail for v in rec.violations)
    snap = snapshot(rec)
    assert len(snap["plans"]) == 1 and snap["plans"][0]["graph"][0]["id"] == "s1"
    assert snap["plan_trail"][0]["step"].startswith("[s1] write notes/todo.txt")
    assert rec.cost.model_calls == 4


async def test_a_plan_that_never_validates_stops_the_run_as_malformed_output(factory, tmp_path):
    rs = recipes(tmp_path, once="id: once\nbase: v4-structured\nplanning: {plan_retries: 0}\n")
    for recipe, calls in ((factory.recipes["v4-structured"], 2), (rs["once"], 1)):
        script = [{"content": "nope"}, {"content": json.dumps({"steps": [{"id": "a"}]})}, WRITE]
        rec = await run_one(factory, factory.tasks.get("fs-01"), recipe, "scripted/replay", "scripted",
                            backend=ScriptedBackend(script))
        kinds = [v.kind for v in rec.violations]
        assert "malformed_output" in kinds and kinds.count("plan_invalid") == calls, recipe.id
        error = json.loads(Path(rec.transcript_path).read_text())["error"]
        assert "did not validate" in error and rec.scores is not None             # a harness stop: scored
        assert rec.status.value == "FAIL" and rec.cost.model_calls == calls and rec.cost.tool_calls == 0


async def test_json_mode_is_sent_only_when_the_model_declares_it_and_the_backend_takes_it(factory):
    class Recording:
        accepts_response_format = True

        def __init__(self):
            self.kwargs = []

        async def complete(self, messages, tools, slot, provider, served, **kw):
            self.kwargs.append(kw)
            return Completion(message={"role": "assistant", "content": "{}"})

    slot = factory.recipes["v4-planner"].model_slots["planner"].model_copy(update={"canonical_model": "scripted/replay"})
    backend = Recording()
    gw = ModelGateway(factory.providers, factory.ledger, Tracer("k"), "scripted", backend=backend)
    await gw.complete(slot, [{"role": "user", "content": "plan"}], [], response_format=plans.JSON_MODE)
    factory.providers.served("scripted", "scripted/replay").caps.structured_output = False
    await gw.complete(slot, [{"role": "user", "content": "plan"}], [], response_format=plans.JSON_MODE)
    await gw.complete(slot, [{"role": "user", "content": "act"}], [])
    assert backend.kwargs == [{"response_format": {"type": "json_object"}}, {}, {}]
    assert [s.attributes.get("response_format") for s in gw.tracer.spans] == ["json_object", "prompt_only", None]


# ------------------------------------------------------------------ V5: workers as a concurrent graph

async def test_a_diamond_runs_its_middle_steps_concurrently_and_records_them_in_plan_order(factory):
    backend = GraphBackend([DIAMOND], DIAMOND_CALLS, before_close={"s2": waits_for("start:s3"),
                                                                    "s3": waits_for("start:s2")})
    rec = await run_one(factory, factory.tasks.get("fs-01"), factory.recipes["v5-parallel"], "scripted/replay",
                        "scripted", backend=backend)
    assert rec.status.value == "SUCCESS"
    ev = backend.events
    # B and C both started before either finished; A finished before them, D started after both.
    assert max(ev.index("start:s2"), ev.index("start:s3")) < min(ev.index("end:s2"), ev.index("end:s3"))
    assert ev.index("end:s1") < ev.index("start:s2") and max(ev.index("end:s2"), ev.index("end:s3")) < \
        ev.index("start:s4")
    ws = delegate_spans(rec)
    assert sorted(ws) == ["step:1", "step:2", "step:3", "step:4"]
    assert ws["step:3"]["start_ns"] < ws["step:2"]["end_ns"]                       # the spans overlap too
    snap = snapshot(rec)
    assert [t["step"][:4] for t in snap["plan_trail"]] == ["[s1]", "[s2]", "[s3]", "[s4]"]
    assert [d["call"] for d in snap["delegations"]] == [1, 2, 3, 4]
    assert rec.output == "done s4"
    # A worker sees what the steps it depends on reported, nothing else: s4 sees s1, s2, s3; s2 sees only s1.
    tasks = {d["task"].split("Your part: ")[1][:4]: d["task"] for d in snap["delegations"]}
    assert "[s1]" in tasks["[s2]"] and "[s3]" not in tasks["[s2]"].split("Your part")[0]
    assert all(f"[{s}]" in tasks["[s4]"].split("Your part")[0] for s in ("s1", "s2", "s3"))
    # Each worker's spans nest under its own delegate call, never under a sibling's.
    by_id = {s["span_id"]: s for s in spans(rec)}
    for s in spans(rec):
        scope = s["attributes"].get("scope", "")
        if s["kind"] == "tool_call" and scope.startswith("delegate:"):
            p = by_id[s["parent_id"]]
            while not (p["kind"] == "tool_call" and p["name"] == "delegate.run"):
                p = by_id[p["parent_id"]]
            assert p["attributes"]["call"] == int(scope.split(":")[1].split("/")[0])


async def test_concurrent_workers_never_hold_more_budget_than_the_team_has(factory, tmp_path):
    """Two independent greedy workers (each calls a tool until its budget stops it) on a team of 6 tool calls: the
    first holds 3 + its delegate call, so the second gets what is left (1 + 1), and the team never overruns."""
    rs = recipes(tmp_path, w3="id: w3\nbase: v2-base\nconfig: {max_tool_calls: 3}\n",
                 team="id: team\nbase: v5-parallel\nconfig: {max_tool_calls: 6}\nplanning: {max_replans: 0}\n"
                      "delegation: {recipe: w3, max_calls: 16}\n")
    two = {"steps": [step("s1", README[0]), step("s2", INI[0])]}
    backend = GraphBackend([two], {"s1": README, "s2": INI}, greedy=True)
    rec = await run_one(factory, factory.tasks.get("fs-01"), rs["team"], "scripted/replay", "scripted",
                        backend=backend)
    ws = delegate_spans(rec)
    assert ws["step:2"]["start_ns"] < ws["step:1"]["end_ns"]                       # they ran at the same time
    work = [s for s in spans(rec) if s["kind"] == "tool_call" and s["name"] != "delegate.run"]
    assert [ws[k]["attributes"]["child_tool_calls"] for k in ("step:1", "step:2")] == [3, 1]
    assert len(work) + len(ws) == rec.cost.tool_calls == 6                          # exactly the team's limit


async def test_a_step_without_budget_waits_for_a_running_worker_then_the_run_stops_on_its_budget(factory, tmp_path):
    rs = recipes(tmp_path, w3="id: w3\nbase: v2-base\nconfig: {max_tool_calls: 3}\n",
                 team="id: team\nbase: v5-parallel\nconfig: {max_tool_calls: 7}\n"
                      "delegation: {recipe: w3, max_calls: 16}\n")
    backend = GraphBackend([DIAMOND], DIAMOND_CALLS)
    rec = await run_one(factory, factory.tasks.get("fs-01"), rs["team"], "scripted/replay", "scripted",
                        backend=backend)
    ws = delegate_spans(rec)
    # After s1 (2 calls), s2 holds 3 + 1 of the 5 left: s3 can't get a budget until s2 hands its share back.
    assert ws["step:3"]["start_ns"] >= ws["step:2"]["end_ns"]
    assert "step:4" not in ws and rec.cost.tool_calls <= 7
    assert any(v.kind == "budget_exceeded" and "max_tool_calls 7" in v.detail for v in rec.violations)
    assert rec.status.value == "FAIL"                                              # the todo was never written


async def test_a_blocked_worker_replans_only_after_its_running_siblings_finish(factory):
    replan = {"steps": [step("s5", TODO[0], ["s1", "s3"])]}
    calls = {**DIAMOND_CALLS, "s5": TODO}

    async def slow(backend):
        await waits_for("end:s2")(backend)
        await asyncio.sleep(0.05)

    backend = GraphBackend([DIAMOND, replan], calls, replies={"s2": "BLOCKED: app.ini is not what I expected"},
                           before_close={"s3": slow})
    rec = await run_one(factory, factory.tasks.get("fs-01"), factory.recipes["v5-parallel"], "scripted/replay",
                        "scripted", backend=backend)
    assert rec.status.value == "SUCCESS"
    hand = {s["name"]: s for s in spans(rec) if s["kind"] == "handoff"}
    ws = delegate_spans(rec)
    assert hand["replan"]["start_ns"] > ws["step:3"]["end_ns"]                     # waited for s3
    assert backend.events.index("end:s2") < backend.events.index("end:s3")         # s2 blocked first
    prompt = backend.planner_prompts[1]
    assert "[s3]" in prompt.split("Blocked:")[0] and "Blocked: [s2]" in prompt     # s3's result was seen
    snap = snapshot(rec)
    assert [p["kind"] for p in snap["plans"]] == ["plan", "replan"] and snap["replans"] == 1
    assert [t["step"][:4] for t in snap["plan_trail"]] == ["[s1]", "[s2]", "[s3]", "[s5]"]
    assert sorted(ws) == ["step:1", "step:2", "step:3", "step:4"]                  # s4 never ran; s5 is step 4


@pytest.mark.parametrize("recipe", ["v4-structured", "v5-parallel"])
async def test_scripted_styles_behave_on_structured_plans_as_they_do_on_v2(factory, recipe):
    for model, ok in (("scripted/replay", True), ("scripted/noisy", True), ("scripted/lazy", False)):
        rec = await run_one(factory, factory.tasks.get("fs-02"), factory.recipes[recipe], model, "scripted")
        assert (rec.status.value == "SUCCESS") is ok, (recipe, model)
    snap = snapshot(rec)
    assert snap["plans"][0]["graph"][0]["id"] == "s1"


# ------------------------------------------------------------------ parallel tool calls

@pytest.fixture
def pool_events(monkeypatch):
    """Every MCP call logs its start and end; the first README read is the slowest, so it finishes last."""
    events: list[tuple[str, str]] = []
    original = MCPSessionPool.call

    async def call(self, ref, args):
        what = args.get("path", ref)
        events.append(("start", what))
        await asyncio.sleep(0.1 if what == "notes/README.txt" else 0.01)
        try:
            return await original(self, ref, args)
        finally:
            events.append(("end", what))

    monkeypatch.setattr(MCPSessionPool, "call", call)
    return events


def batch(*calls):
    return {"tool_calls": [{"name": n, "arguments": a} for n, a in calls]}


def tool_replies(rec):
    msgs = json.loads(Path(rec.transcript_path).read_text())["messages"]
    return [m["tool_call_id"] for m in msgs if m["role"] == "tool"]


async def test_reads_of_one_reply_run_concurrently_and_come_back_in_call_order(factory, pool_events):
    script = [batch(README, LIST), WRITE, {"content": "done"}]
    rec = await run_one(factory, factory.tasks.get("fs-01"), factory.recipes["v2-parallel-tools"], "scripted/replay",
                        "scripted", backend=ScriptedBackend(script))
    assert rec.status.value == "SUCCESS" and rec.scores.trajectory == 1.0
    assert pool_events[:4] == [("start", "notes/README.txt"), ("start", "notes"), ("end", "notes"),
                               ("end", "notes/README.txt")]                         # README finished last ...
    assert tool_replies(rec)[:2] == ["call_1_0", "call_1_1"]                         # ... but comes back first


@pytest.mark.parametrize("recipe, script", [
    ("v2-parallel-tools", [batch(README, TODO), {"content": "done"}]),             # a write: the batch is sequential
    ("v2-base", [batch(README, LIST), WRITE, {"content": "done"}]),                # the flag off: as before
])
async def test_a_batch_with_a_write_or_without_the_flag_runs_one_call_after_another(factory, pool_events, recipe,
                                                                                    script):
    rec = await run_one(factory, factory.tasks.get("fs-01"), factory.recipes[recipe], "scripted/replay", "scripted",
                        backend=ScriptedBackend(script))
    first, second = script[0]["tool_calls"][0]["arguments"]["path"], script[0]["tool_calls"][1]["arguments"]["path"]
    assert pool_events[:4] == [("start", first), ("end", first), ("start", second), ("end", second)]
    assert tool_replies(rec)[:2] == ["call_1_0", "call_1_1"]


async def test_faults_hit_the_same_call_in_a_parallel_batch_as_in_order(factory):
    for recipe in ("v2-parallel-tools", "v2-base"):
        rec = await run_one(factory, factory.tasks.get("fs-01"), factory.recipes[recipe], "scripted/replay",
                            "scripted", backend=ScriptedBackend([batch(README, INI), WRITE, {"content": "done"}]),
                            fault="first-call-fails")
        reads = [s for s in spans(rec) if s["name"] == "fs.read_file"]
        assert [("fault" in s["attributes"], json.loads(s["attributes"]["args"])["path"]) for s in reads] == \
            [(True, "notes/README.txt"), (False, "config/app.ini")], recipe


async def test_a_parallel_batch_starts_only_the_calls_the_budget_allows_in_order(factory, tmp_path):
    rs = recipes(tmp_path, p1="id: p1\nbase: v2-parallel-tools\nconfig: {max_tool_calls: 2}\n",
                 s1="id: s1\nbase: v2-base\nconfig: {max_tool_calls: 2}\n")
    out = {}
    for rid in ("p1", "s1"):
        rec = await run_one(factory, factory.tasks.get("fs-01"), rs[rid], "scripted/replay", "scripted",
                            backend=ScriptedBackend([batch(README, LIST, INI), {"content": "done"}]))
        out[rid] = ([json.loads(s["attributes"]["args"])["path"] for s in spans(rec) if s["kind"] == "tool_call"],
                    tool_replies(rec), json.loads(Path(rec.transcript_path).read_text())["error"])
    assert out["p1"] == out["s1"]
    assert sorted(out["p1"][0]) == ["notes", "notes/README.txt"] and out["p1"][2] == "budget:max_tool_calls 2"
