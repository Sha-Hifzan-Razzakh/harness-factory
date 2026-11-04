"""The biz server (mock CRM, invoicing and ticketing): its tools and refusals, the per-run business state, the
biz_equals / biz_count checks, and that adding it moved no existing identity."""

import copy
import json

import pytest

from harness_factory.env.sandbox import LocalSandbox
from harness_factory.env.state import EnvState, StateInspector
from harness_factory.grading.base import GradeContext
from harness_factory.grading.state import StateDiffGrader
from harness_factory.hashing import hash_paths
from harness_factory.mcp.pool import MCPSessionPool
from harness_factory.provision.provisioner import allowed_tools, reaches, task_class
from harness_factory.runner.runner import plan, run_one
from harness_factory.shapes.result import RunResult
from harness_factory.substrate.gateway import ScriptedBackend
from harness_factory.tasks.model import Oracle, WeightedCheck


@pytest.fixture
def biz(factory):
    with LocalSandbox("biz-test") as sb:
        sb.reset(factory.tasks.get("biz-01"))
        yield sb


async def _call(pool, tool, **args):
    res = await pool.call(f"biz.{tool}", args)
    return res if res.is_error else json.loads(res.text)


def _state(sb) -> dict:
    return json.loads(sb.biz_path.read_text())


# ------------------------------------------------------------------ tools

async def test_crm_search_get_and_update(factory, biz):
    async with MCPSessionPool(factory.declared, ["biz"], biz.env()) as pool:
        found = await _call(pool, "crm_search_contacts", query="oasis foods")
        assert [c["id"] for c in found["contacts"]] == ["C-1004", "C-1009", "C-1011"]      # company substring
        assert found["total"] == 3 and found["next_offset"] is None
        page = await _call(pool, "crm_search_contacts", query="ae", limit=5, offset=5)
        assert page["offset"] == 5 and len(page["contacts"]) == 5 and page["next_offset"] == 10
        assert (await _call(pool, "crm_get_contact", id="C-1006"))["company"] == "Marina Fitness"
        upd = await _call(pool, "crm_update_contact", id="C-1005", fields={"email": "billing@creekanalytics.ae"})
        assert upd["email"] == "billing@creekanalytics.ae"
        assert _state(biz)["contacts"]["C-1005"]["email"] == "billing@creekanalytics.ae"
        for fields, msg in (({"id": "C-9"}, "can't be changed"), ({"credit_limit": "9"}, "can't be changed"),
                            ({"email": "not-an-email"}, "invalid email"), ({"tier": "diamond"}, "tier must be"),
                            ({}, "at least one field")):
            res = await _call(pool, "crm_update_contact", id="C-1005", fields=fields)
            assert res.is_error and msg in res.text, (fields, res.text)        # ToolError: the model sees why
        for res in (await _call(pool, "crm_get_contact", id="C-0000"),
                    await _call(pool, "crm_search_contacts", query="a")):
            assert res.is_error
        assert "unknown contact id 'C-0000'" in (await _call(pool, "crm_get_contact", id="C-0000")).text


async def test_invoices_list_filters_and_pages(factory, biz):
    async with MCPSessionPool(factory.declared, ["biz"], biz.env()) as pool:
        everything = await _call(pool, "inv_list_invoices", limit=50)
        assert everything["total"] == 14 and everything["as_of"] == "2026-10-20"
        first = await _call(pool, "inv_list_invoices", limit=4)
        assert len(first["invoices"]) == 4 and first["next_offset"] == 4
        oasis = await _call(pool, "inv_list_invoices", customer_id="C-1004", status="open")
        assert {i["id"]: (i["balance_due"], i["overdue"]) for i in oasis["invoices"]} == {
            "INV-1036": (1050, True), "INV-1044": (840, False)}
        assert "lines" not in oasis["invoices"][0]                               # summaries stay small
        for args, msg in (({"status": "late"}, "unknown invoice status"), ({"customer_id": "C-9"}, "unknown customer"),
                          ({"limit": 500}, "limit must be"), ({"offset": -1}, "offset must be")):
            res = await _call(pool, "inv_list_invoices", **args)
            assert res.is_error and msg in res.text, args


async def test_credit_notes_reduce_the_balance_and_can_be_voided(factory, biz):
    async with MCPSessionPool(factory.declared, ["biz"], biz.env()) as pool:
        inv = await _call(pool, "inv_get_invoice", id="INV-1042")
        assert inv["total"] == 2110.5 and inv["credited"] == 157.5 and inv["balance_due"] == 1953
        assert [cn["id"] for cn in inv["credit_notes"]] == ["CN-0003"]
        a = await _call(pool, "inv_create_credit_note", invoice_id="INV-1042", amount=157.5, reason="T-2041: mat")
        b = await _call(pool, "inv_create_credit_note", invoice_id="INV-1042", amount=10, reason="T-2041: test")
        assert (a["id"], b["id"]) == ("CN-0004", "CN-0005")                    # ids from the seed's sequence
        assert _state(biz)["invoices"]["INV-1042"]["credited"] == 325
        voided = await _call(pool, "inv_void_credit_note", id="CN-0005", reason="issued by mistake")
        assert voided["status"] == "void" and _state(biz)["invoices"]["INV-1042"]["balance_due"] == 1795.5
        again = await _call(pool, "inv_void_credit_note", id="CN-0005", reason="again")
        assert again.is_error and "already void" in again.text
        over = await _call(pool, "inv_create_credit_note", invoice_id="INV-1042", amount=2000, reason="T-1: all")
        assert over.is_error and "more than is left to credit on INV-1042" in over.text
        assert "left 1,795.50" in over.text
        for amount, msg in ((0, "more than 0"), (-5, "more than 0"), (1.234, "2 decimal places")):
            res = await _call(pool, "inv_create_credit_note", invoice_id="INV-1042", amount=amount, reason="x")
            assert res.is_error and msg in res.text, amount
        blank = await _call(pool, "inv_create_credit_note", invoice_id="INV-1042", amount=1, reason="  ")
        assert blank.is_error and "reason must be non-empty" in blank.text
        assert (await _call(pool, "inv_create_credit_note", invoice_id="INV-9", amount=1, reason="x")).is_error


async def test_mark_paid_rules(factory, biz):
    async with MCPSessionPool(factory.declared, ["biz"], biz.env()) as pool:
        for args, msg in (({"id": "INV-1038", "paid_on": "2026-11-01"}, "in the future"),
                          ({"id": "INV-1038", "paid_on": "2026-09-01"}, "before INV-1038 was issued"),
                          ({"id": "INV-1038", "paid_on": "Oct 10"}, "YYYY-MM-DD"),
                          ({"id": "INV-1031", "paid_on": "2026-10-01"}, "already paid")):
            res = await _call(pool, "inv_mark_paid", **args)
            assert res.is_error and msg in res.text, args
        paid = await _call(pool, "inv_mark_paid", id="INV-1038", paid_on="2026-10-19")
        assert (paid["status"], paid["paid"], paid["balance_due"]) == ("paid", paid["total"], 0)
        full = await _call(pool, "inv_create_credit_note", invoice_id="INV-1041", amount=2310, reason="T-1: all")
        assert not getattr(full, "is_error", False) and _state(biz)["invoices"]["INV-1041"]["status"] == "credited"
        nothing = await _call(pool, "inv_mark_paid", id="INV-1041", paid_on="2026-10-19")
        assert nothing.is_error and "fully credited" in nothing.text


async def test_tickets_comment_close_and_reopen(factory, biz):
    async with MCPSessionPool(factory.declared, ["biz"], biz.env()) as pool:
        open_ = await _call(pool, "tkt_list", status="open")
        assert [t["id"] for t in open_["tickets"]] == ["T-2038", "T-2040", "T-2041", "T-2042"]
        assert "body" not in open_["tickets"][0]
        assert (await _call(pool, "tkt_list", status="solved")).is_error
        closed = await _call(pool, "tkt_close", id="T-2041", resolution="Credited one mat (CN-0004).")
        assert closed["status"] == "closed" and closed["resolution"] == "Credited one mat (CN-0004)."
        twice = await _call(pool, "tkt_close", id="T-2041", resolution="again")
        assert twice.is_error and "already closed" in twice.text
        reopened = await _call(pool, "tkt_comment", id="T-2041", text="Customer replied: the mat is fine.")
        assert reopened["status"] == "open" and reopened["resolution"] is None              # closing is reversible
        assert reopened["comments"][-1] == {"id": "TC-0003", "text": "Customer replied: the mat is fine.",
                                            "at": "2026-10-20"}
        long = await _call(pool, "tkt_comment", id="T-2041", text="x" * 5000)
        assert long.is_error and "too long" in long.text
        assert (await _call(pool, "tkt_get", id="T-1")).is_error


async def test_a_task_without_business_state_gets_a_clear_error(factory):
    with LocalSandbox("biz-none") as sb:
        sb.reset(factory.tasks.get("fs-01"))
        assert not sb.biz_path.exists()
        async with MCPSessionPool(factory.declared, ["biz"], sb.env()) as pool:
            res = await pool.call("biz.tkt_list", {})
            assert res.is_error and "no business systems" in res.text


def test_seed_is_consistent_with_the_server():
    """Derived invoice fields in the seed are what the server itself would compute."""
    from harness_factory import settings
    from harness_factory.env.mcp.impl.biz_server import recompute
    seed = json.loads((settings.CATALOG / "seeds/biz/state.json").read_text())
    again = copy.deepcopy(seed)
    for iid in again["invoices"]:
        recompute(again, iid)
    assert again == seed


# ------------------------------------------------------------------ per-run state

async def test_business_state_is_reset_for_every_run(factory):
    """Canary: the credit note the first run issues must not exist when the second run starts (it would make
    the second run's note CN-0005), and the seed file itself is never written."""
    task, recipe = factory.tasks.get("biz-01"), factory.recipes["v2-biz"]
    seed = task.env_seed.biz_seed(task.source_dir)
    before = hash_paths([seed])
    first = await run_one(factory, task, recipe, "scripted/replay", "scripted")
    second = await run_one(factory, task, recipe, "scripted/replay", "scripted", seed=1)
    for rec in (first, second):
        state = next(g for g in rec.grades if g["grader_id"] == "state_diff")
        assert state["detail"] == "+biz credit_notes/CN-0004; ~biz invoices/INV-1042, meta, tickets/T-2041"
    assert hash_paths([seed]) == before


def test_state_diff_reports_business_records(tmp_path):
    db = tmp_path / "state" / "task.sqlite"
    db.parent.mkdir()
    insp = StateInspector(tmp_path / "workspace", db)
    assert insp.capture().biz is None                                         # nothing seeded
    doc = {"meta": {"today": "2026-10-20"}, "tickets": {"T-1": {"status": "open"}, "T-2": {"status": "open"}},
           "credit_notes": {}}
    insp.biz_path.write_text(json.dumps(doc))
    before = insp.capture()
    doc["tickets"]["T-1"]["status"] = "closed"
    del doc["tickets"]["T-2"]
    doc["credit_notes"]["CN-1"] = {"amount": 5}
    insp.biz_path.write_text(json.dumps(doc))
    d = StateInspector.diff(before, insp.capture())
    assert (d.biz_added, d.biz_removed, d.biz_changed) == (["credit_notes/CN-1"], ["tickets/T-2"], ["tickets/T-1"])
    assert d.summary() == "+biz credit_notes/CN-1; -biz tickets/T-2; ~biz tickets/T-1" and not d.is_empty


def test_seed_must_be_a_json_object(factory, tmp_path):
    task = factory.tasks.get("biz-01").model_copy(deep=True)
    bad = tmp_path / "bad.json"
    bad.write_text("[1, 2]")
    task.env_seed.biz_state = str(bad)
    with LocalSandbox("biz-bad") as sb, pytest.raises(ValueError, match="not a JSON object"):
        sb.reset(task)


# ------------------------------------------------------------------ checks

DOC = {"invoices": {"INV-1": {"status": "paid", "credited": 315, "flag": True, "po": "42"},
                    "INV-2": {"status": "open", "credited": 0, "flag": False, "po": None}},
       "list": [{"a": 1}, {"a": 2}, {"a": 1}]}


def _grade(*checks, doc=DOC) -> dict[str, bool]:
    weight = 1 / len(checks)
    cs = [WeightedCheck(id=f"c{i}", weight=weight, **c) for i, c in enumerate(checks)]
    cs[-1].weight = round(1 - weight * (len(cs) - 1), 9)
    task = __import__("types").SimpleNamespace(oracle=Oracle(checks=cs))
    ctx = GradeContext(task=task, result=RunResult.model_construct(), before=EnvState(), after=EnvState(biz=doc),
                       diff=StateInspector.diff(EnvState(), EnvState()), workspace=None, db_path=None, spans=[],
                       max_steps=1)
    return {c.id: c.passed for c in StateDiffGrader().grade(ctx).checks}


def test_biz_equals_is_strict_on_type():
    got = _grade({"kind": "biz_equals", "path": "/invoices/INV-1/status", "text": "paid"},
                 {"kind": "biz_equals", "path": "/invoices/INV-1/credited", "text": "315.0"},     # numbers by value
                 {"kind": "biz_equals", "path": "/invoices/INV-1/flag", "text": "1"},             # true is not 1
                 {"kind": "biz_equals", "path": "/invoices/INV-2/flag", "text": "0"},             # false is not 0
                 {"kind": "biz_equals", "path": "/invoices/INV-1/po", "text": "42"},              # "42" is not 42
                 {"kind": "biz_equals", "path": "/invoices/INV-1/po", "text": '"42"'},
                 {"kind": "biz_equals", "path": "/invoices/INV-2/po", "text": "null"},
                 {"kind": "biz_equals", "path": "/invoices/INV-9/status", "text": "paid"},        # missing
                 {"kind": "biz_equals", "path": "/invoices/INV-2/status", "text": "paid", "negate": True},
                 {"kind": "biz_equals", "path": "/invoices/INV-2", "text": json.dumps(DOC["invoices"]["INV-2"])})
    assert list(got.values()) == [True, True, False, False, False, True, True, False, True, True]


def test_biz_count_counts_matching_records():
    got = _grade({"kind": "biz_count", "path": "/invoices", "text": "2"},
                 {"kind": "biz_count", "path": "/invoices", "text": "1", "query": '{"status": "open"}'},
                 {"kind": "biz_count", "path": "/invoices", "text": "0", "query": '{"flag": 1}'},  # strict here too
                 {"kind": "biz_count", "path": "/list", "text": "2", "query": '{"a": 1}'},
                 {"kind": "biz_count", "path": "/invoices/INV-1/status", "text": "1"},            # not a collection
                 {"kind": "biz_count", "path": "/nope", "text": "0"})
    assert list(got.values()) == [True, True, True, True, False, False]
    assert _grade({"kind": "biz_count", "path": "/invoices", "text": "0"}, doc=None) == {"c0": False}


def test_biz_checks_validate_their_fields():
    for bad, msg in (({"kind": "biz_equals", "path": "/a"}, "needs text"),
                     ({"kind": "biz_equals", "path": "invoices/INV-1", "text": "x"}, "JSON pointer"),
                     ({"kind": "biz_count", "path": "/a", "text": "two"}, "whole number"),
                     ({"kind": "biz_count", "path": "/a", "text": "2", "query": "[1]"}, "JSON object"),
                     ({"kind": "biz_count", "path": "/a", "text": "2", "query": "{oops"}, "JSON object")):
        with pytest.raises(ValueError, match=msg):
            WeightedCheck(id="x", weight=1, **bad)


# ------------------------------------------------------------------ runs, plans and identity

async def test_biz_tasks_succeed_end_to_end_in_v2_biz(factory):
    for task_id in ("biz-01", "biz-02"):
        rec = await run_one(factory, factory.tasks.get(task_id), factory.recipes["v2-biz"], "scripted/replay",
                            "scripted")
        assert rec.status.value == "SUCCESS", rec.grades
        assert rec.composite == pytest.approx(1.0) and not rec.violations


async def test_biz_01_fits_the_v3_stage_caps(factory):
    """CLAUDE.md: a reference fits V3 (≤3 reads before the first write, ≤5 calls after). v3-staged with biz."""
    base = factory.recipes["v3-staged"]
    reads = ["biz.tkt_get", "biz.tkt_list", "biz.inv_get_invoice", "biz.inv_list_invoices", "biz.crm_get_contact",
             "biz.crm_search_contacts"]
    stages = [s.model_copy(update={"tools": s.tools + reads}) if s.id != "act" else s for s in base.workflow.stages]
    v3 = base.model_copy(update={
        "id": "v3-biz-test",
        "tools": base.tools.model_copy(update={"tool_ids": [*base.tools.tool_ids, "biz.*"],
                                               "mcp_servers": [*base.tools.mcp_servers, "biz"]}),
        "workflow": base.workflow.model_copy(update={"stages": stages})})
    rec = await run_one(factory, factory.tasks.get("biz-01"), v3, "scripted/replay", "scripted")
    assert rec.status.value == "SUCCESS", rec.grades


async def test_recording_a_payment_is_irreversible_and_refused(factory):
    """inv_mark_paid can't be undone in the mock, so v2-biz's policy (IRREVERSIBLE: DENY) refuses it."""
    script = [{"tool_calls": [{"name": "biz__inv_mark_paid", "arguments": {"id": "INV-1042",
                                                                           "paid_on": "2026-10-19"}}]},
              {"content": "Marked it paid."}]
    rec = await run_one(factory, factory.tasks.get("biz-01"), factory.recipes["v2-biz"], "scripted/replay",
                        "scripted", backend=ScriptedBackend(script))
    assert any(v.kind == "policy_denied" and "biz.inv_mark_paid" in v.detail for v in rec.violations)
    state = next(g for g in rec.grades if g["grader_id"] == "state_diff")
    assert state["detail"] == "no change" and rec.scores.outcome == 0


def test_campaigns_plan_biz_tasks_only_with_recipes_that_open_biz(factory):
    d = factory.declared
    biz1, fs1 = factory.tasks.get("biz-01"), factory.tasks.get("fs-01")
    assert not reaches(factory.recipes["v2-base"], biz1, d) and reaches(factory.recipes["v2-biz"], biz1, d)
    # every recipe opens all of fs and sql, except v2-outbox: it may send, so it leaves out what else is IRREVERSIBLE
    assert all(reaches(r, fs1, d) for r in factory.recipes.values() if r.id != "v2-outbox")
    specs = plan(factory, ["v2-base", "v2-biz"], "scripted", ["scripted/replay"], task_ids=["fs-01", "biz-01"])
    assert [(s.task_id, s.recipe_id) for s in specs] == [("fs-01", "v2-base"), ("fs-01", "v2-biz"),
                                                         ("biz-01", "v2-biz")]
    assert "biz.inv_mark_paid" in allowed_tools(factory.recipes["v2-biz"], biz1, d)
    assert task_class(biz1, d) == "UNKNOWN_PATH|IRREVERSIBLE|R2"             # mark_paid is in its allowance
    assert task_class(factory.tasks.get("biz-02"), d) == "UNKNOWN_PATH|READ|R0"


def test_adding_biz_moved_no_existing_identity(factory):
    d = factory.declared
    # The same values as before the biz server existed: a recipe that doesn't open biz keeps its env hash, and
    # a task without biz_state keeps its hash (the field is left out while unset).
    assert d.env_hash(factory.recipes["v2-base"].tools.mcp_servers, "openai/gpt-oss-120b") == "a55c2fb739f1"
    assert factory.recipes["v2-base"].content_hash() == "79abd03818943f65"
    assert factory.tasks.get("fs-01").content_hash() == "f88f6a779f5f0dfc"
    assert factory.tasks.get("mix-03").content_hash() == "26630f4e6a7ea04a"
