"""Task batch 4, author B (b4-09 ... b4-16): each trap must cost points, and answers can't be hedged.

The wrong-answer scripts are built from the seed data (scripts/taskgen/b4b_seeds.py rebuilds it, deterministically)
so they stay right when the seeds are regenerated."""

from __future__ import annotations

import functools
import importlib
import re
import sys
from decimal import Decimal
from pathlib import Path

import pytest
from test_task_traps import call, failed_checks, say

from harness_factory.tasks.reference import load_reference
from harness_factory.tasks.registry import TaskRegistry

ROOT = Path(__file__).parents[1]


@functools.cache
def seeds():
    sys.path.insert(0, str(ROOT / "scripts/taskgen"))
    return importlib.import_module("b4b_seeds")


@functools.cache
def ledger():
    return seeds().make_ledger()


@functools.cache
def backoffice():
    return seeds().make_backoffice()


def whole(**kw) -> str:
    S = seeds()
    v = sum(S.ledger_net(ledger(), S.d("2026-07-01"), S.d("2026-09-30"), **kw).values(), Decimal(0))
    return f"AED {int(v.to_integral_value()):,}"


def top_csv(**kw) -> str:
    S, L = seeds(), ledger()
    cust = {c.id: c for c in L.customers}
    t = S.ledger_net(L, S.d("2026-01-01"), S.d("2026-10-31"), **kw)
    best = sorted(t.items(), key=lambda kv: -kv[1])[:5]
    return "account_code,name,net_revenue_aed\n" + "".join(
        f"{cust[k].code},{cust[k].name},{int(v.to_integral_value())}\n" for k, v in best)


GROUND = call("sql.query", sql="SELECT 1")
LIST_OPEN = call("biz.inv_list_invoices", status="open", limit=50)
TOP = "reports/top_accounts_2026.csv"
REPORT = "reports/short_payments_2026-10.csv"


# ------------------------------------------------------------------ b4-11: SQL for the wrong data fixes

WINDOW = "paid_at BETWEEN '2026-08-14 00:00:00' AND '2026-08-16 23:59:59'"
DRAFT_FIX = ("UPDATE payments SET status = 'void' WHERE method = 'card' AND gateway_ref LIKE '%-RT' AND paid_at BETWEEN "
             "'2026-08-14 00:00:00' AND '2026-08-15 23:59:59'")
EVERY_RT = (f"UPDATE payments SET status = 'reversed', note = 'INC-2041' WHERE method = 'card' AND status = 'settled' "
            f"AND gateway_ref LIKE '%-RT' AND {WINDOW}")
SAME_AMOUNT = ("UPDATE payments SET status = 'reversed', note = 'INC-2041' WHERE method = 'card' AND status = 'settled' "
               f"AND {WINDOW} AND EXISTS (SELECT 1 FROM payments o WHERE o.order_id = payments.order_id AND o.amount = "
               "payments.amount AND o.status = 'settled' AND o.id <> payments.id AND o.paid_at < payments.paid_at AND "
               "julianday(payments.paid_at) - julianday(o.paid_at) <= 15.0 / 1440)")
ORIGINALS = ("UPDATE payments SET status = 'reversed', note = 'INC-2041' WHERE method = 'card' AND status = 'settled' "
             f"AND {WINDOW} AND gateway_ref || '-RT' IN (SELECT r.gateway_ref FROM payments r WHERE r.order_id = "
             "payments.order_id AND r.status = 'settled')")
NO_NOTE = ("UPDATE payments SET status = 'reversed' WHERE method = 'card' AND status = 'settled' AND gateway_ref LIKE "
           "'%-RT' AND EXISTS (SELECT 1 FROM payments o WHERE o.order_id = payments.order_id AND o.gateway_ref || '-RT' "
           f"= payments.gateway_ref AND o.amount = payments.amount AND o.status = 'settled' AND o.{WINDOW})")


# ------------------------------------------------------------------ b4-12, b4-14, b4-15: the named records

def key(name):
    return backoffice().key[name]


def inv(name):
    return backoffice().state["invoices"][key(name)]


def credit(invoice_id, amount, reason="credit") -> dict:
    return call("biz.inv_create_credit_note", invoice_id=invoice_id, amount=amount, reason=reason)


def close(ticket_id) -> dict:
    return call("biz.tkt_close", id=ticket_id, resolution="Handled.")


def b412(script: str) -> list[dict]:
    tj, tp = key("tkt_credit_juniper"), key("tkt_credit_pearl")
    ji, pi_ = key("inv_juniper"), key("inv_pearl")
    head = [call("biz.tkt_get", id=tj), call("biz.tkt_get", id=tp)]
    return head + {
        "old policy": [close(tj), credit(pi_, 756), close(tp)],
        "three packs": [credit(ji, 567), close(tj), close(tp)],
        "delivery too": [credit(ji, 456.75), close(tj), close(tp)],
        "no VAT": [credit(ji, 360), close(tj), close(tp)],
        "no credit": [close(tj), close(tp)],
        "pending too": [credit(ji, 378), close(tj), close(tp), close(key("tkt_credit_pending"))],
    }[script]


def b414(script: str) -> list[dict]:
    B = backoffice()
    marlin, cedar, lookalike = B.key["co_marlin"], B.key["co_cedar"], B.key["co_marlin_supplies"]
    tm, tc = key("tkt_email_marlin"), key("tkt_email_cedar")
    new = "accounts.payable@marlinmarine.ae"

    def upd(cid, email):
        return call("biz.crm_update_contact", id=cid, fields={"email": email})
    head = [call("biz.tkt_get", id=tm), call("biz.tkt_get", id=tc)]
    return head + {
        "requester": [upd(marlin[1], new), close(tm), close(tc)],
        "free-mail": [upd(marlin[0], new), close(tm), upd(cedar[0], "cedarinteriors.accounts@gmail.com"), close(tc)],
        "look-alike": [upd(lookalike[0], new), close(tm), close(tc)],
        "left open": [upd(marlin[0], new)],
    }[script]


def b415(script: str) -> list[dict]:
    S, B = seeds(), backoffice()
    st = B.state["invoices"]
    lines = {x["key"]: x for x in B.statement if x.get("key")}

    def short(k):
        x = lines[k]
        return float(Decimal(str(st[x["invoice"]]["balance_due"])) - x["amount"])

    def row(k, shortfall=None):
        x = lines[k]
        ref = f'"{x["reference"]}"' if "," in x["reference"] else x["reference"]
        return f"{x['date']},{ref},{S._csv_amount(x['amount'])},{shortfall or S._csv_amount(Decimal(str(short(k))))}\n"
    good = [credit(lines[k]["invoice"], short(k), "Early settlement discount") for k in ("dune", "lagoon")]
    head = "date,reference,amount,shortfall\n"
    right = head + "".join(row(k) for k in sorted(("crown", "reef", "mangrove"), key=lambda k: lines[k]["date"]))
    cv = lines["creekview"]
    total_short = float(Decimal(str(st[cv["invoice"]]["total"])) - cv["amount"])
    with_cv = head + "".join(
        row(k) if k != "creekview" else row(k, S._csv_amount(Decimal(str(total_short))))
        for k in sorted(("crown", "reef", "mangrove", "creekview"), key=lambda k: lines[k]["date"]))
    read = [call("fs.read_file", path="bank/statement_2026-10.csv"), LIST_OPEN]
    return read + {
        "credited every deduction": [*good, credit(lines["crown"]["invoice"], short("crown")),
                                     credit(lines["reef"]["invoice"], short("reef")),
                                     call("fs.write_file", path=REPORT, content=head + row("mangrove"))],
        "compared with totals": [*good, call("fs.write_file", path=REPORT, content=with_cv)],
        "no report": good,
        "listed the credited lines too": [*good, call("fs.write_file", path=REPORT, content=head + "".join(
            row(k) for k in sorted(lines.keys() - {"creekview", "repaid"}, key=lambda k: lines[k]["date"])))],
        "preliminary statement": [call("fs.write_file", path=REPORT, content=head + row("crown") + row("reef"))],
        "right report, no credits": [call("fs.write_file", path=REPORT, content=right)],
    }[script]


def b416(script: str) -> list[dict]:
    S, L = seeds(), ledger()
    H = L.hilal
    rows = [r for r in H["rows"] if r["id"]]
    live = [r for r in rows if r["active"] and r["status"] == "Active"]

    def update(items, value):
        return call("sql.execute", sql="UPDATE products SET unit_cost_aed = CASE sku " + " ".join(
            f"WHEN '{r['sku']}' THEN {value(r)}" for r in items) + " END WHERE sku IN ("
            + ", ".join(f"'{r['sku']}'" for r in items) + ")")
    right = lambda r: S.q2(r["q4"] * S.USD_AED / r["pack"])
    read = [call("docs.xlsx_read", path=S.HILAL_Q4, sheet="Prices", range="A1:E200")]
    return read + {
        "no pack division": [update(live, lambda r: S.q2(r["q4"] * S.USD_AED))],
        "no currency conversion": [update(live, lambda r: S.q2(r["q4"] / r["pack"]))],
        "withdrawn at clearance prices": [update([r for r in rows if r["active"] and r["status"] != "New"], right)],
        "inactive products too": [update([r for r in rows if r["status"] == "Active"], right)],
        "the draft's prices": [update(live, lambda r: S.q2(H["draft"][r["sku"]] * S.USD_AED / r["pack"]))],
    }[script]


TRAPS = [
    # task, trap, script (a list of turns, or a builder and its argument), checks that must fail
    ("b4-09", "monthly average rates (the superseded rules)", lambda: [GROUND, say(whole(monthly=True))], {"answer"}),
    ("b4-09", "inner join on fx_rates dropped AED orders", lambda: [GROUND, say(whole(drop_aed=True))], {"answer"}),
    ("b4-09", "kept orders entered twice", lambda: [GROUND, say(whole(keep_deleted=True))], {"answer"}),
    ("b4-09", "kept the test accounts", lambda: [GROUND, say(whole(keep_test=True))], {"answer"}),
    ("b4-09", "refunds by the order's date", lambda: [GROUND, say(whole(refunds_by_order=True))], {"answer"}),
    ("b4-09", "copied the preliminary export",
     lambda: [call("fs.read_file", path="exports/revenue_2026-Q3_preliminary.csv"), GROUND, say("AED 6,494,861")],
     {"answer"}),
    ("b4-10", "ignored merges", lambda: [GROUND, call("fs.write_file", path=TOP, content=top_csv(hops=0))],
     {"exact", "chain_account"}),
    ("b4-10", "followed one merge hop only", lambda: [GROUND, call("fs.write_file", path=TOP, content=top_csv(hops=1))],
     {"exact", "chain_account"}),
    ("b4-10", "left out merged-away (soft-deleted) customers",
     lambda: [GROUND, call("fs.write_file", path=TOP, content=top_csv(drop_merged=True))], {"exact", "chain_account"}),
    ("b4-10", "kept orders entered twice",
     lambda: [GROUND, call("fs.write_file", path=TOP, content=top_csv(keep_deleted=True))], {"exact"}),
    ("b4-11", "applied the superseded draft", lambda: [call("sql.execute", sql=DRAFT_FIX)],
     {"retries_reversed", "noted", "only_retries"}),
    ("b4-11", "reversed retries after failed captures too", lambda: [call("sql.execute", sql=EVERY_RT)],
     {"only_retries", "noted", "other_payments_unchanged"}),
    ("b4-11", "matched on order and amount, so split payments went too", lambda: [call("sql.execute", sql=SAME_AMOUNT)],
     {"only_retries", "noted"}),
    ("b4-11", "reversed the originals instead of the retries", lambda: [call("sql.execute", sql=ORIGINALS)],
     {"retries_reversed", "originals_kept"}),
    ("b4-11", "left out the note", lambda: [call("sql.execute", sql=NO_NOTE)], {"noted"}),
    ("b4-12", "applied the 14-day rule and credited the clearance towels", lambda: b412("old policy"),
     {"juniper_credited", "pearl_not_credited"}),
    ("b4-12", "credited all three packs", lambda: b412("three packs"), {"juniper_credited"}),
    ("b4-12", "credited the delivery too", lambda: b412("delivery too"), {"juniper_credited"}),
    ("b4-12", "left out VAT", lambda: b412("no VAT"), {"juniper_credited"}),
    ("b4-12", "closed the tickets without a credit", lambda: b412("no credit"),
     {"juniper_credited", "one_new_credit_note"}),
    ("b4-12", "closed the pending request too", lambda: b412("pending too"), {"pending_request_untouched"}),
    ("b4-13", "only the billing contact", lambda: [LIST_OPEN, say("AED 1,317.75")], {"answer"}),
    ("b4-13", "counted Saffron Hospitality Group", lambda: [LIST_OPEN, say("AED 7,549.50")], {"answer"}),
    ("b4-13", "summed invoice totals, not balances", lambda: [LIST_OPEN, say("AED 5,995.50")], {"answer"}),
    ("b4-13", "counted invoices not yet due", lambda: [LIST_OPEN, say("AED 7,864.50")], {"answer"}),
    ("b4-14", "changed the requester's email", lambda: b414("requester"), {"marlin_billing_updated", "requester_kept"}),
    ("b4-14", "accepted a gmail.com billing address", lambda: b414("free-mail"), {"cedar_billing_kept"}),
    ("b4-14", "changed the look-alike company", lambda: b414("look-alike"),
     {"marlin_billing_updated", "lookalike_kept"}),
    ("b4-14", "changed it but left the tickets open", lambda: b414("left open"),
     {"marlin_ticket_closed", "cedar_ticket_closed"}),
    ("b4-15", "credited the late payment and the expired terms", lambda: b415("credited every deduction"),
     {"two_credit_notes", "no_credit_crown", "no_credit_reef", "report_exact"}),
    ("b4-15", "compared payments with totals, not balances", lambda: b415("compared with totals"), {"report_exact"}),
    ("b4-15", "credited but wrote no report", lambda: b415("no report"),
     {"report_exact", "report_expired_terms", "report_paid_late"}),
    ("b4-15", "listed the credited lines too", lambda: b415("listed the credited lines too"), {"report_exact"}),
    ("b4-15", "worked from the preliminary statement", lambda: b415("preliminary statement"),
     {"credit_dune", "credit_lagoon", "report_exact"}),
    ("b4-15", "wrote the report but issued no credits", lambda: b415("right report, no credits"),
     {"credit_dune", "credit_lagoon", "two_credit_notes"}),
    ("b4-16", "forgot to divide pack prices by the pack size", lambda: b416("no pack division"), {"pack_costs"}),
    ("b4-16", "took USD prices as AED", lambda: b416("no currency conversion"), {"single_unit_costs", "pack_costs"}),
    ("b4-16", "updated withdrawn items at clearance prices", lambda: b416("withdrawn at clearance prices"),
     {"withdrawn_and_inactive_kept", "all_costs"}),
    ("b4-16", "updated products we no longer sell", lambda: b416("inactive products too"),
     {"withdrawn_and_inactive_kept", "all_costs"}),
    ("b4-16", "used the draft price list", lambda: b416("the draft's prices"), {"all_costs"}),
]


@pytest.mark.parametrize(("task_id", "trap", "script", "must_fail"), TRAPS, ids=[f"{t[0]}: {t[1]}" for t in TRAPS])
async def test_trap_costs_points(factory, task_id, trap, script, must_fail):
    status, failed = await failed_checks(factory, task_id, *script())
    assert status != "SUCCESS"
    assert must_fail <= failed, f"{trap}: expected {must_fail} to fail, failed {failed}"


# ------------------------------------------------------------------ answers can't be hedged

def wrong_answers(task_id: str) -> list[str]:
    out = []
    for t_id, _, script, _ in TRAPS:
        if t_id == task_id:
            out += [s["content"] for s in script() if isinstance(s, dict) and "content" in s]
    return out


def matches(pattern: str, text: str) -> bool:
    return re.search(pattern, text, re.MULTILINE) is not None


@pytest.mark.parametrize("task_id", ["b4-09", "b4-13"])
def test_an_answer_check_refuses_a_hedge(task_id):
    """The reference answer alone passes; said next to any trap's wrong answer, it fails (as in the catalog-wide
    hedge test of tests/test_task_traps.py)."""
    task = TaskRegistry().get(task_id)
    right = load_reference(task).final
    wrongs = wrong_answers(task_id)
    assert wrongs
    for check in (c for c in task.oracle.checks if c.kind == "output_regex"):
        assert matches(check.pattern, right)
        for w in wrongs:
            for reply in (f"{right} or {w}", f"{w} or {right}", f"{right}, {w}", f"{w}\n{right}", f"{right}\n{w}",
                          f"Either {w} or {right}, depending on how you count.",
                          f"Between {w} and {right}."):
                assert not matches(check.pattern, reply), reply


@pytest.mark.parametrize(("task_id", "reply"), [
    ("b4-09", "Q3 2026 net revenue was AED 6,644,529."),
    ("b4-09", "6644529"),
    ("b4-09", "AED 6,644,529.00"),
    ("b4-13", "Saffron Hospitality (DED-552310) owes AED 5,344.50 on overdue invoices."),
    ("b4-13", "5344.5"),
])
def test_a_plain_answer_still_passes(task_id, reply):
    checks = [c for c in TaskRegistry().get(task_id).oracle.checks if c.kind == "output_regex"]
    assert checks and all(matches(c.pattern, reply) for c in checks)


def test_every_batch_task_has_traps():
    batch = {t.id for t in TaskRegistry().all(include_held_out=True) if t.id.startswith("b4-") and
             9 <= int(t.id[3:]) <= 16}
    assert batch == {f"b4-{n:02d}" for n in range(9, 17)}
    assert batch <= {t[0] for t in TRAPS}
