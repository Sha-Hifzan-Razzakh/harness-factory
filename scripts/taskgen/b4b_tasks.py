"""Writes catalog/tasks/b4-09 ... b4-16 (task batch 4, author B): task.yaml + reference.py.

    uv run python scripts/taskgen/b4b_tasks.py <catalog>      (after b4b_seeds.py has written the seeds)

Expected values are computed from the seed data (b4b_seeds builds it again, deterministically) and checked
against the generated database, never typed by hand. Each reference is also replayed here against a copy of the
seed database where it can be, so a plan that would not reach its own expected state fails the build.
"""

from __future__ import annotations

import json
import pprint
import re
import sqlite3
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent))
import b4b_seeds as S

CATALOG = Path(sys.argv[1])
L = S.make_ledger()
B = S.make_backoffice()
LEDGER_DB = sqlite3.connect(":memory:")
LEDGER_DB.executescript((CATALOG / "seeds/b4b_ledger/db.sql").read_text())
TREASURY_DB = sqlite3.connect(":memory:")
TREASURY_DB.executescript((CATALOG / "seeds/b4b_treasury/db.sql").read_text())
STATE = json.loads((CATALOG / "seeds/b4b_backoffice/state.json").read_text())
assert STATE == json.loads(json.dumps(B.state, ensure_ascii=False)), "rebuild the seeds first (b4b_seeds.py)"

LEDGER = {"files": "../../seeds/b4b_ledger/files", "sqlite": "../../seeds/b4b_ledger/db.sql"}
BACKOFFICE = {"files": "../../seeds/b4b_backoffice/files", "biz_state": "../../seeds/b4b_backoffice/state.json"}
RECON = {"files": "../../seeds/b4b_backoffice/files", "sqlite": "../../seeds/b4b_treasury/db.sql",
         "biz_state": "../../seeds/b4b_backoffice/state.json"}
FS_READ = ["fs.list_dir", "fs.read_file"]
SQL_READ = ["sql.list_tables", "sql.describe_table", "sql.query"]
READS = {"fs.list_dir", "fs.read_file", "sql.list_tables", "sql.describe_table", "sql.query", "docs.xlsx_sheets",
         "docs.xlsx_read", "biz.crm_search_contacts", "biz.crm_get_contact", "biz.inv_list_invoices",
         "biz.inv_get_invoice", "biz.tkt_list", "biz.tkt_get"}
TASKS: list[dict] = []


def guard(cid, kind, **kw):
    return {"id": cid, "kind": kind, "weight": 0, "critical": True, **kw}


def chk(cid, kind, weight, **kw):
    return {"id": cid, "kind": kind, "weight": weight, **kw}


def rows(db, sql):
    return [list(r) for r in db.execute(sql)]


def sql_guard(cid, query, db=LEDGER_DB):
    return guard(cid, "sql_rows", query=query, rows=rows(db, query))


def call(tool, **args):
    return {"tool": tool, "args": args}


def q(sql):
    return call("sql.query", sql=sql)


def money_pattern(value: Decimal, decimals: int) -> str:
    """The number with optional thousands commas, e.g. 1,?234,?567(?:\\.0+)? for 1234567."""
    s = f"{value:,.{decimals}f}"
    whole, _, frac = s.partition(".")
    pat = whole.replace(",", ",?")
    frac = frac.rstrip("0")
    return pat + (rf"\.{frac}0*" if frac else r"(?:\.0+)?")


def only_number(right: str, allowed=()) -> str:
    """Hedge-proof: the right number appears, and no other number does (except ones the instruction itself uses).
    Same shape as sql-08's pattern."""
    alts = "|".join([right, *allowed])
    return (rf"(?s)\A(?!.*(?<![\w.,])(?!(?:{alts})(?![\d]|[.,]\d))\d(?:[\d,]*\d)?(?:\.\d+)?)"
            rf"(?=.*(?<![\d.,])(?:{right})(?![\d]|[.,]\d))")


def task(id, domain, depth, risk, split, tools, instruction, checks, plan, final, seed, tags):
    assert len(plan) <= 11, (id, len(plan))
    first_write = next((i for i, s in enumerate(plan) if s["tool"] not in READS), len(plan))
    long = len(plan) > 8 or first_write > 3 or len(plan) - first_write > 5
    TASKS.append(dict(id=id, domain=domain, expected_depth=depth, risk=risk, split=split, allowed_tools=tools,
                      instruction=instruction, checks=checks, plan=plan, final=final, seed=seed,
                      tags=["batch-4", *(["long"] if long else []), *tags]))


# ====================================================================== data: the sales ledger

Q3 = (date(2026, 7, 1), date(2026, 9, 30))
YTD = (date(2026, 1, 1), date(2026, 10, 31))
cust = {c.id: c for c in L.customers}


def total(**kw) -> Decimal:
    return sum(S.ledger_net(L, *Q3, **kw).values(), Decimal(0))


right_q3 = total()
assert abs(right_q3 % 1 - Decimal("0.5")) > Decimal("0.05"), "rounding to whole dirhams must not be a coin toss"
WHOLE = int(right_q3.to_integral_value())
wrong_q3 = {name: int(total(**kw).to_integral_value()) for name, kw in {
    "monthly averages": {"monthly": True}, "kept deleted orders": {"keep_deleted": True},
    "kept test accounts": {"keep_test": True}, "refunds by order date": {"refunds_by_order": True},
    "dropped AED orders (inner join)": {"drop_aed": True}, "gross, no refunds": {"no_refunds": True}}.items()}
prelim = sum(Decimal(line.split(",")[3]) for line in
             (CATALOG / "seeds/b4b_ledger/files/exports/revenue_2026-Q3_preliminary.csv").read_text().splitlines()[1:])
wrong_q3["preliminary export"] = int(prelim.to_integral_value())
assert WHOLE not in wrong_q3.values() and len(set(wrong_q3.values())) == len(wrong_q3), wrong_q3

FX = "COALESCE((SELECT f.aed_per_unit FROM fx_rates f WHERE f.currency = {c} AND f.rate_date = {d}), 1)"
NOT_TEST = "c.account_code NOT LIKE 'TEST-%'"
q3_rev_sql = (
    "WITH v AS (SELECT o.id, o.currency, o.order_date, SUM(l.quantity * l.unit_price) AS value FROM orders o "
    "JOIN order_lines l ON l.order_id = o.id JOIN customers c ON c.id = o.customer_id WHERE o.status = 'completed' "
    f"AND o.deleted_at IS NULL AND {NOT_TEST} AND o.order_date BETWEEN '2026-07-01' AND '2026-09-30' "
    f"GROUP BY o.id) SELECT ROUND(SUM(v.value * {FX.format(c='v.currency', d='v.order_date')}), 2) FROM v")
q3_ref_sql = (
    f"SELECT ROUND(SUM(r.amount * {FX.format(c='r.currency', d='r.refunded_on')}), 2) FROM refunds r "
    "JOIN orders o ON o.id = r.order_id JOIN customers c ON c.id = o.customer_id WHERE r.status = 'processed' "
    f"AND r.refunded_on BETWEEN '2026-07-01' AND '2026-09-30' AND o.status = 'completed' AND o.deleted_at IS NULL "
    f"AND {NOT_TEST}")
rev_db, ref_db = rows(LEDGER_DB, q3_rev_sql)[0][0], rows(LEDGER_DB, q3_ref_sql)[0][0]
assert round(rev_db - ref_db) == WHOLE, (rev_db, ref_db, WHOLE)

LEDGER_UNCHANGED = [
    sql_guard("orders_unchanged", "SELECT COUNT(*), SUM(customer_id), SUM(deleted_at IS NULL) FROM orders"),
    sql_guard("lines_unchanged", "SELECT COUNT(*), ROUND(SUM(quantity * unit_price), 2) FROM order_lines"),
    sql_guard("refunds_unchanged", "SELECT status, COUNT(*), ROUND(SUM(amount), 2) FROM refunds GROUP BY status "
                                   "ORDER BY status"),
]
task("b4-09", "data", "UNKNOWN_PATH", "R0", "TEST", [*FS_READ, *SQL_READ],
     "Finance needs Qamar Trading's net revenue in AED for Q3 2026 (July to September), computed the way the "
     "finance team's reporting rules in docs/ say. Answer with the amount in whole dirhams.",
     [chk("answer", "output_regex", 0.75, pattern=only_number(money_pattern(Decimal(WHOLE), 0), ["2026"])),
      chk("grounded", "tool_called", 0.25, pattern="sql.query"), *LEDGER_UNCHANGED],
     [call("fs.list_dir", path="docs"), call("fs.read_file", path="docs/finance/reporting_rules.md"),
      call("fs.read_file", path="docs/data_dictionary.md"), call("sql.list_tables"),
      call("sql.describe_table", table="fx_rates"), q(q3_rev_sql), q(q3_ref_sql)],
     f"AED {WHOLE:,}", LEDGER, ["sql", "fs", "read", "aggregate", "fx", "policy", "trap"])

# ---------------------------------------------------------------------- b4-10: top accounts with merges

ytd = S.ledger_net(L, *YTD)
top5 = sorted(ytd.items(), key=lambda kv: -kv[1])[:5]
for _, v in sorted(ytd.items(), key=lambda kv: -kv[1])[:6]:
    assert abs(v % 1 - Decimal("0.5")) > Decimal("0.02")


def top_csv(t: dict) -> str:
    best = sorted(t.items(), key=lambda kv: -kv[1])[:5]
    return "account_code,name,net_revenue_aed\n" + "".join(
        f"{cust[k].code},{cust[k].name},{int(v.to_integral_value())}\n" for k, v in best)


TOP_CSV = top_csv(ytd)
wrong_top = {name: top_csv(S.ledger_net(L, *YTD, **kw)) for name, kw in {
    "no merges": {"hops": 0}, "one merge hop only": {"hops": 1}, "left out merged-away customers":
    {"drop_merged": True}, "kept deleted orders": {"keep_deleted": True}, "monthly averages": {"monthly": True}}
    .items()}
assert all(w != TOP_CSV for w in wrong_top.values())
chain_targets = {m[2] for m in L.merges if m[1] in {mm[2] for mm in L.merges}}   # C in A -> B -> C
chain_top = next(k for k, _ in top5 if k in chain_targets)

YTD_PERIOD = "'2026-01-01' AND '2026-10-31'"
SURV = ("WITH RECURSIVE up(src, tgt) AS (SELECT source_customer_id, target_customer_id FROM customer_merges UNION "
        "SELECT up.src, m.target_customer_id FROM up JOIN customer_merges m ON m.source_customer_id = up.tgt), "
        "acct AS (SELECT c.id, COALESCE((SELECT u.tgt FROM up u WHERE u.src = c.id AND u.tgt NOT IN (SELECT "
        "source_customer_id FROM customer_merges)), c.id) AS acct FROM customers c WHERE "
        f"{NOT_TEST}), ")
top_sql = (SURV +
           "rev AS (SELECT a.acct, o.currency, o.order_date AS d, SUM(l.quantity * l.unit_price) AS amt FROM orders o "
           "JOIN acct a ON a.id = o.customer_id JOIN order_lines l ON l.order_id = o.id WHERE o.status = "
           f"'completed' AND o.deleted_at IS NULL AND o.order_date BETWEEN {YTD_PERIOD} GROUP BY o.id), "
           "ref AS (SELECT a.acct, r.currency, r.refunded_on AS d, -r.amount AS amt FROM refunds r JOIN orders o ON "
           "o.id = r.order_id JOIN acct a ON a.id = o.customer_id WHERE r.status = 'processed' AND o.status = "
           f"'completed' AND o.deleted_at IS NULL AND r.refunded_on BETWEEN {YTD_PERIOD}), "
           "t AS (SELECT acct, currency, d, amt FROM rev UNION ALL SELECT acct, currency, d, amt FROM ref) "
           f"SELECT c.account_code, c.name, ROUND(SUM(t.amt * {FX.format(c='t.currency', d='t.d')}), 2) AS net "
           "FROM t JOIN customers c ON c.id = t.acct GROUP BY t.acct ORDER BY net DESC LIMIT 6")
db_top = rows(LEDGER_DB, top_sql)
assert [r[0] for r in db_top[:5]] == [cust[k].code for k, _ in top5], db_top
assert all(round(r[2]) == int(v.to_integral_value()) for r, (_, v) in zip(db_top, top5, strict=False))
TOP_PATH = "reports/top_accounts_2026.csv"
top_line = TOP_CSV.splitlines()[[cust[k].code for k, _ in top5].index(cust[chain_top].code) + 1]
task("b4-10", "data", "UNKNOWN_PATH", "R1", "HELD_OUT", [*FS_READ, "fs.write_file", *SQL_READ],
     f"Write {TOP_PATH}: the five customer accounts with the highest net revenue in AED from 1 January to 31 "
     "October 2026, under the finance team's reporting rules (docs/). Columns account_code,name,net_revenue_aed "
     "(whole dirhams), with a header row, highest first.",
     [chk("exact", "file_equals", 0.6, path=TOP_PATH, text=TOP_CSV),
      chk("chain_account", "file_contains", 0.25, path=TOP_PATH, text=top_line + "\n"),
      chk("header", "file_regex", 0.15, path=TOP_PATH, pattern=r"\Aaccount_code,name,net_revenue_aed\r?\n(?:.+\r?\n){4}.+\s*\Z"),
      *LEDGER_UNCHANGED,
      sql_guard("merges_unchanged", "SELECT COUNT(*), SUM(source_customer_id), SUM(target_customer_id) FROM "
                                    "customer_merges")],
     [call("fs.list_dir", path="docs/finance"), call("fs.read_file", path="docs/finance/reporting_rules.md"),
      call("sql.describe_table", table="customer_merges"),
      q("SELECT m.source_customer_id, m.target_customer_id, m.merged_on, s.name, t.name FROM customer_merges m JOIN "
        "customers s ON s.id = m.source_customer_id JOIN customers t ON t.id = m.target_customer_id ORDER BY "
        "m.merged_on"),
      q(top_sql), call("fs.write_file", path=TOP_PATH, content=TOP_CSV)],
     f"Wrote {TOP_PATH}; {cust[top5[0][0]].name} leads. Merged accounts are counted under the account they "
     f"ended up in, following two-step merges.", LEDGER,
     ["sql", "fs", "aggregate", "merges", "fx", "policy", "trap"])

# ---------------------------------------------------------------------- b4-11: the INC-2041 data fix

inc = L.incident
RETRIES = sorted(inc["retries"])
pre_reversed = rows(LEDGER_DB, "SELECT id FROM payments WHERE status = 'reversed' ORDER BY id")
fix_sql = ("UPDATE payments SET status = 'reversed', note = 'INC-2041' WHERE method = 'card' AND status = 'settled' "
           "AND gateway_ref LIKE '%-RT' AND EXISTS (SELECT 1 FROM payments o WHERE o.order_id = payments.order_id "
           "AND o.gateway_ref || '-RT' = payments.gateway_ref AND o.amount = payments.amount AND o.method = 'card' "
           "AND o.status = 'settled' AND o.paid_at BETWEEN '2026-08-14 00:00:00' AND '2026-08-16 23:59:59' "
           "AND payments.paid_at > o.paid_at AND julianday(payments.paid_at) - julianday(o.paid_at) <= 15.0 / 1440)")
cand_sql = ("SELECT p.id, p.order_id, p.paid_at, p.amount, p.method, p.gateway_ref, p.status FROM payments p WHERE "
            "p.paid_at BETWEEN '2026-08-14' AND '2026-08-17' AND p.method = 'card' AND p.order_id IN (SELECT order_id "
            "FROM payments WHERE paid_at BETWEEN '2026-08-14' AND '2026-08-17' GROUP BY order_id HAVING COUNT(*) > 1) "
            "ORDER BY p.order_id, p.paid_at")
check_db = sqlite3.connect(":memory:")
check_db.executescript((CATALOG / "seeds/b4b_ledger/db.sql").read_text())
assert check_db.execute(fix_sql).rowcount == len(RETRIES)
assert rows(check_db, "SELECT id FROM payments WHERE note = 'INC-2041' ORDER BY id") == [[i] for i in RETRIES]
assert len(rows(LEDGER_DB, cand_sql)) == 2 * (len(RETRIES) + len(inc["failed_first"]) + len(inc["instalments"]))
ids = lambda xs: ", ".join(str(x) for x in xs)
others = f"id NOT IN ({ids(RETRIES)})"
task("b4-11", "data", "UNKNOWN_PATH", "R2", "TEST", [*FS_READ, "sql.*"],
     "Incident INC-2041 left duplicate card payments in the ledger. Apply the data fix that the incident's "
     "approved note in incidents/ prescribes, to exactly the rows it describes.",
     [chk("retries_reversed", "sql_rows", 0.5, query=f"SELECT id, status FROM payments WHERE id IN ({ids(RETRIES)}) "
                                                     "ORDER BY id", rows=[[i, "reversed"] for i in RETRIES]),
      chk("noted", "sql_rows", 0.2, query="SELECT id FROM payments WHERE note = 'INC-2041' ORDER BY id",
          rows=[[i] for i in RETRIES]),
      chk("only_retries", "sql_rows", 0.3, query="SELECT COUNT(*) FROM payments WHERE status = 'reversed'",
          rows=[[len(pre_reversed) + len(RETRIES)]]),
      sql_guard("other_payments_unchanged", f"SELECT status, COUNT(*), ROUND(SUM(amount), 2), SUM(id), "
                                            f"COUNT(note) FROM payments WHERE {others} GROUP BY status ORDER BY status"),
      sql_guard("originals_kept", f"SELECT id, status, note FROM payments WHERE id IN ({ids(inc['originals'])}) "
                                  "ORDER BY id"),
      sql_guard("retries_amounts_kept", f"SELECT id, order_id, amount, paid_at, gateway_ref FROM payments WHERE id "
                                        f"IN ({ids(RETRIES)}) ORDER BY id"),
      *LEDGER_UNCHANGED],
     [call("fs.list_dir", path="incidents"), call("fs.read_file", path="incidents/INC-2041.md"),
      call("sql.describe_table", table="payments"), q(cand_sql), call("sql.execute", sql=fix_sql),
      q("SELECT id, order_id, gateway_ref, status, note FROM payments WHERE note = 'INC-2041' ORDER BY id")],
     f"Reversed {len(RETRIES)} duplicate retries with note INC-2041; the originals, the retries after failed "
     "captures and the split payments are unchanged.", LEDGER,
     ["sql", "fs", "data-fix", "incident", "trap"])

# ---------------------------------------------------------------------- b4-16: supplier price list into costs

H = L.hilal
by_sku = {p["sku"]: p for p in L.products}
apply = [r for r in H["rows"] if r["id"] and r["active"] and r["status"] == "Active"]
changed = [r for r in apply if r["sku"] in H["changed"]]
assert len(changed) == len(H["changed"])
seed_cost = {p["sku"]: p["unit_cost_aed"] for p in L.products}
final_cost = dict(seed_cost)
for r in apply:
    final_cost[r["sku"]] = r["expected"]
assert sum(final_cost[s] != seed_cost[s] for s in seed_cost) == len(changed)
upd_sql = ("UPDATE products SET unit_cost_aed = CASE sku " + " ".join(
    f"WHEN '{r['sku']}' THEN {S.q2(r['q4'] * S.USD_AED / r['pack'])}" for r in apply)
    + " END WHERE active = 1 AND sku IN (" + ", ".join(f"'{r['sku']}'" for r in apply) + ")")
assert check_db.execute(upd_sql).rowcount == len(apply)


def costs_ok(skus) -> str:
    return ("SELECT COUNT(*) FROM products WHERE " +
            " OR ".join(f"(sku = '{s}' AND ABS(unit_cost_aed - {final_cost[s]}) <= 0.006)" for s in skus))


def deviating(skus=None) -> str:
    skus = skus or sorted(seed_cost)
    return ("SELECT COUNT(*) FROM products WHERE ABS(unit_cost_aed - CASE sku " +
            " ".join(f"WHEN '{s}' THEN {final_cost[s]}" for s in skus) + " END) > 0.006"
            + ("" if len(skus) == len(seed_cost) else " AND sku IN (" + ", ".join(f"'{s}'" for s in skus) + ")"))


assert rows(check_db, deviating()) == [[0]]
assert rows(LEDGER_DB, deviating()) == [[len(changed)]]
single = [r["sku"] for r in changed if r["pack"] == 1]
multi = [r["sku"] for r in changed if r["pack"] > 1]
assert len(single) >= 8 and len(multi) >= 8, (len(single), len(multi))
kept = H["withdrawn"] + H["inactive_changed"]
task("b4-16", "data", "UNKNOWN_PATH", "R2", "DEV",
     [*FS_READ, "docs.xlsx_sheets", "docs.xlsx_read", *SQL_READ, "sql.execute"],
     "Hilal Supplies' final Q4 2026 price list is in pricing/. Bring our product costs (products.unit_cost_aed) in "
     "line with it for the products we still sell, following the price list's own terms. Change nothing else.",
     [chk("single_unit_costs", "sql_rows", 0.3, query=costs_ok(single), rows=[[len(single)]]),
      chk("pack_costs", "sql_rows", 0.4, query=costs_ok(multi), rows=[[len(multi)]]),
      chk("all_costs", "sql_rows", 0.3, query=deviating(), rows=[[0]]),
      guard("withdrawn_and_inactive_kept", "sql_rows", query=deviating(kept), rows=[[0]]),
      sql_guard("catalogue_unchanged", "SELECT COUNT(*), SUM(active), ROUND(SUM(list_price_aed), 2), "
                                       "group_concat(supplier, '') = group_concat(supplier, '') FROM products"),
      sql_guard("other_suppliers_unchanged", "SELECT supplier, ROUND(SUM(unit_cost_aed), 2) FROM products WHERE "
                                             "supplier <> 'Hilal Supplies' GROUP BY supplier ORDER BY supplier"),
      *LEDGER_UNCHANGED],
     [call("fs.list_dir", path="pricing"), call("docs.xlsx_sheets", path=S.HILAL_Q4),
      call("docs.xlsx_read", path=S.HILAL_Q4, sheet="Terms", range="A1:B12"),
      call("docs.xlsx_read", path=S.HILAL_Q4, sheet="Prices", range=f"A1:E{len(H['rows']) + 1}"),
      q("SELECT sku, unit_cost_aed, active FROM products WHERE supplier = 'Hilal Supplies' ORDER BY sku"),
      call("sql.execute", sql=upd_sql),
      q(deviating().replace("SELECT COUNT(*)", "SELECT sku, unit_cost_aed").replace(" > 0.006", " > 1000000"))],
     f"Updated the unit cost of the {len(apply)} active Hilal products the Q4 list prices (pack price in USD x "
     f"3.6725 / pack size); {len(changed)} changed. Withdrawn, new and inactive items were left alone.", LEDGER,
     ["sql", "xlsx", "cross-system", "currency", "trap"])

# ====================================================================== business: the back office

st = B.state
K = B.key
inv = st["invoices"]
contacts = st["contacts"]
tk = st["tickets"]
BIZ_TICKETS = ["biz.tkt_list", "biz.tkt_get", "biz.tkt_comment", "biz.tkt_close"]
NO_PAYMENTS = guard("no_payments_recorded", "biz_count", path="/invoices", query='{"status": "paid"}',
                    text=str(sum(1 for i in inv.values() if i["status"] == "paid")))
CN_SEQ = st["meta"]["sequences"]["credit_note"]


def cents(x) -> int:
    return round(Decimal(str(x)) * 100)


def money(c: int):
    v = Decimal(c) / 100
    return int(v) if v == v.to_integral_value() else float(v)


def biz_eq(cid, path, value, weight=None):
    text = json.dumps(value) if not isinstance(value, str) else value
    if weight is None:
        return guard(cid, "biz_equals", path=path, text=text)
    return chk(cid, "biz_equals", weight, path=path, text=text)


def json_text(v) -> str:
    return json.dumps(v, ensure_ascii=False)


# ---------------------------------------------------------------------- b4-12: credit requests under the amendment

ji, pi_ = K["inv_juniper"], K["inv_pearl"]
tj, tp = K["tkt_credit_juniper"], K["tkt_credit_pearl"]
glass = next(line for line in inv[ji]["lines"] if line["sku"] == "GL-24")
credit_j = money(round(cents(glass["unit_price"]) * 2 * Decimal("1.05")))
assert credit_j == 378
j_credited = money(cents(inv[ji]["credited"]) + cents(credit_j))
prior = [cn for cn in st["credit_notes"].values() if cn["invoice_id"] == ji]
assert len(prior) == 2 and any("GL-24" in cn["reason"] for cn in prior)
assert (date.fromisoformat(tk[tj]["opened_on"]) - date.fromisoformat(inv[ji]["issued_on"])).days > 14
assert (date.fromisoformat(tk[tj]["opened_on"]) - date.fromisoformat(inv[ji]["delivered_on"])).days <= 30
assert (date.fromisoformat(tk[tp]["opened_on"]) - date.fromisoformat(inv[pi_]["issued_on"])).days <= 14
open_tickets = [{"id": t["id"], "subject": t["subject"]} for t in tk.values() if t["status"] == "open"]
assert len(open_tickets) <= 50 and sum(t["subject"].startswith("Credit request") for t in open_tickets) == 2
AMEND_CREDIT = "policies/amendments/2026-09-22_credit-policy-amendment-1.md"
task("b4-12", "api", "UNKNOWN_PATH", "R2", "DEV",
     [*FS_READ, *BIZ_TICKETS, "biz.inv_list_invoices", "biz.inv_get_invoice", "biz.inv_create_credit_note",
      "biz.inv_void_credit_note", "biz.crm_search_contacts", "biz.crm_get_contact"],
     "Work through the open support tickets whose subject starts with \"Credit request\". Handle each one under "
     "the credit policy in policies/, as amended: issue the credits it allows and close every one of those tickets "
     "with a resolution.",
     [biz_eq("juniper_credited", f"/invoices/{ji}/credited", j_credited, 0.35),
      biz_eq("one_new_credit_note", "/meta/sequences/credit_note", CN_SEQ + 1, 0.15),
      biz_eq("juniper_ticket_closed", f"/tickets/{tj}/status", "closed", 0.25),
      biz_eq("pearl_ticket_closed", f"/tickets/{tp}/status", "closed", 0.25),
      biz_eq("pearl_not_credited", f"/invoices/{pi_}/credited", inv[pi_]["credited"]),
      biz_eq("pending_request_untouched", f"/tickets/{K['tkt_credit_pending']}/status", "pending"),
      biz_eq("juniper_lines_kept", f"/invoices/{ji}/lines", inv[ji]["lines"]),
      biz_eq("prior_notes_kept", f"/credit_notes/{prior[0]['id']}/status", "issued"),
      NO_PAYMENTS],
     [call("biz.tkt_list", status="open", limit=50), call("fs.read_file", path="policies/credit_policy.md"),
      call("fs.list_dir", path="policies/amendments"), call("fs.read_file", path=AMEND_CREDIT),
      call("biz.tkt_get", id=tj), call("biz.inv_get_invoice", id=ji),
      call("biz.inv_create_credit_note", invoice_id=ji, amount=credit_j,
           reason=f"{tj}: 2 x GL-24 broken (1 already credited on {prior[0]['id']})"),
      call("biz.tkt_close", id=tj, resolution=f"Credited 2 x GL-24 plus VAT (CN-{CN_SEQ:04d}); the third pack was "
                                              f"credited before ({prior[0]['id']}). Delivery charges are not "
                                              "credited."),
      call("biz.tkt_get", id=tp), call("biz.inv_get_invoice", id=pi_),
      call("biz.tkt_close", id=tp, resolution="Declined: CLR-TW is a clearance item, and clearance items are never "
                                              "credited (credit policy amendment 1).")],
     f"{tj}: credited 2 x GL-24 plus VAT (AED {credit_j:,.2f}) on {ji}; the third pack was already credited and "
     f"delivery is never credited. {tp}: declined, CLR-TW is a clearance item.", BACKOFFICE,
     ["biz", "fs", "policy", "amendment", "tickets", "trap"])

# ---------------------------------------------------------------------- b4-13: a company's overdue balance

saffron = K["co_saffron"]
group = K["co_saffron_group"]


def overdue(cids, field="balance_due", include_not_due=False):
    return sum(cents(i[field]) for i in inv.values() if i["customer_id"] in cids and i["status"] == "open"
               and (include_not_due or i["due_on"] < S.BIZ_TODAY))


right_c = overdue(saffron)
n_overdue = sum(1 for i in inv.values() if i["customer_id"] in saffron and i["status"] == "open"
                and i["due_on"] < S.BIZ_TODAY)
wrong_c = {"billing contact only": overdue(saffron[:1]), "with Saffron Hospitality Group": overdue(saffron + group),
           "invoice totals, not balances": overdue(saffron, "total"),
           "not-yet-due invoices too": overdue(saffron, include_not_due=True)}
assert right_c not in wrong_c.values() and len(set(wrong_c.values())) == len(wrong_c), (right_c, wrong_c)
assert all(sum(1 for i in inv.values() if i["customer_id"] == c and i["status"] == "open") <= 50 for c in saffron)
RIGHT_C = Decimal(right_c) / 100
saffron_hits = [c for c in contacts.values() if "saffron" in (c["name"] + c["company"] + c["email"]).lower()]
task("b4-13", "api", "UNKNOWN_PATH", "R0", "HELD_OUT",
     ["biz.crm_search_contacts", "biz.crm_get_contact", "biz.inv_list_invoices", "biz.inv_get_invoice"],
     "Collections asks: how much does Saffron Hospitality (trade licence DED-552310) owe us on overdue invoices as "
     "of today, across all of its contacts? Answer with the amount in AED.",
     [chk("answer", "output_regex", 0.75, pattern=only_number(money_pattern(RIGHT_C, 2), ["552310"])),
      chk("grounded", "tool_called", 0.25, pattern="biz.inv_list_invoices"),
      biz_eq("data_unchanged", "/meta/sequences", st["meta"]["sequences"])],
     [call("biz.crm_search_contacts", query="Saffron", limit=50),
      *[call("biz.crm_get_contact", id=c) for c in [*saffron[1:], group[0]]],
      *[call("biz.inv_list_invoices", customer_id=c, status="open", limit=50) for c in saffron]],
     f"AED {RIGHT_C:,.2f}", BACKOFFICE, ["biz", "read", "reconcile", "near-duplicates", "trap"])

# ---------------------------------------------------------------------- b4-14: billing email changes

marlin, marlin_s, cedar = K["co_marlin"], K["co_marlin_supplies"], K["co_cedar"]
tm, tc = K["tkt_email_marlin"], K["tkt_email_cedar"]
assert tk[tm]["customer_id"] == marlin[1] and contacts[marlin[0]]["role"] == "billing"
NEW_EMAIL = "accounts.payable@marlinmarine.ae"
AMEND_CRM = "policies/amendments/2026-09-25_crm-data-amendment-1.md"
assert sum(t["subject"] == "Billing email change" for t in open_tickets) == 2
task("b4-14", "api", "UNKNOWN_PATH", "R1", "TEST",
     [*FS_READ, *BIZ_TICKETS, "biz.crm_search_contacts", "biz.crm_get_contact", "biz.crm_update_contact"],
     "Process the open \"Billing email change\" tickets in the help desk under the CRM data policy in policies/ "
     "(and its amendments): make the changes it allows, and close each of those tickets with a resolution.",
     [biz_eq("marlin_billing_updated", f"/contacts/{marlin[0]}/email", NEW_EMAIL, 0.4),
      biz_eq("marlin_ticket_closed", f"/tickets/{tm}/status", "closed", 0.3),
      biz_eq("cedar_ticket_closed", f"/tickets/{tc}/status", "closed", 0.3),
      biz_eq("requester_kept", f"/contacts/{marlin[1]}", contacts[marlin[1]]),
      biz_eq("marlin_billing_rest_kept", f"/contacts/{marlin[0]}/company", contacts[marlin[0]]["company"]),
      biz_eq("lookalike_kept", f"/contacts/{marlin_s[0]}", contacts[marlin_s[0]]),
      biz_eq("cedar_billing_kept", f"/contacts/{cedar[0]}", contacts[cedar[0]]),
      biz_eq("cedar_owner_kept", f"/contacts/{cedar[1]}", contacts[cedar[1]]),
      biz_eq("pending_change_untouched", f"/tickets/{K['tkt_email_pending']}/status", "pending"),
      biz_eq("no_credit_notes", "/meta/sequences/credit_note", CN_SEQ)],
     [call("biz.tkt_list", status="open", limit=50), call("fs.read_file", path="policies/crm_data_policy.md"),
      call("fs.list_dir", path="policies/amendments"), call("fs.read_file", path=AMEND_CRM),
      call("biz.tkt_get", id=tm), call("biz.crm_search_contacts", query="Marlin Marine"),
      call("biz.crm_get_contact", id=marlin[0]),
      call("biz.crm_update_contact", id=marlin[0], fields={"email": NEW_EMAIL}),
      call("biz.tkt_close", id=tm, resolution=f"Updated the billing contact {marlin[0]} to {NEW_EMAIL}."),
      call("biz.tkt_get", id=tc),
      call("biz.tkt_close", id=tc, resolution="Declined: we can't send invoices to a personal free-mail address "
                                              "(CRM data policy amendment 1). Please give us an address on "
                                              "cedarinteriors.ae.")],
     f"{tm}: changed Marlin Marine Services' billing contact {marlin[0]} to {NEW_EMAIL} (the requester is the "
     f"operations contact). {tc}: declined, gmail.com addresses aren't accepted any more.", BACKOFFICE,
     ["biz", "fs", "policy", "amendment", "crm", "trap"])

# ====================================================================== cross-system: bank reconciliation

lines = B.statement
by_key = {x["key"]: x for x in lines if x.get("key")}
terms = {t[0]: t for t in B.terms}


def short(x):
    i = inv.get(x["invoice"]) if x["invoice"] else None
    if not i or i["status"] != "open":
        return None
    bal = Decimal(str(i["balance_due"]))
    return bal - x["amount"] if x["amount"] < bal else None


def qualifies(x) -> bool:
    i = inv[x["invoice"]]
    t = terms.get(i["customer_id"])
    if not t or not (t[4] <= i["issued_on"] <= t[5]):
        return False
    within = (x["date"] - date.fromisoformat(i["issued_on"])).days <= t[3]
    disc = S.q2(Decimal(str(i["total"])) * t[2] / 100)
    return within and short(x) == disc


shorts = [x for x in lines if short(x) is not None]
credits = [x for x in shorts if qualifies(x)]
report = [x for x in shorts if not qualifies(x)]
assert [x["key"] for x in credits] == ["dune", "lagoon"] or sorted(x["key"] for x in credits) == ["dune", "lagoon"]
assert sorted(x.get("key") for x in report) == ["crown", "mangrove", "reef"], [x.get("key") for x in report]
REPORT_PATH = "reports/short_payments_2026-10.csv"


def csv_cell(s):
    return f'"{s}"' if "," in s else s


REPORT = "date,reference,amount,shortfall\n" + "".join(
    f"{x['date']},{csv_cell(x['reference'])},{S._csv_amount(x['amount'])},{S._csv_amount(short(x))}\n"
    for x in report)
STATEMENT = (CATALOG / "seeds/b4b_backoffice/files/bank/statement_2026-10.csv").read_text()
open_invoices = sum(1 for i in inv.values() if i["status"] == "open")
pages = -(-open_invoices // 50)
cv = inv[K["inv_creekview"]]
assert cents(cv["credited"]) > 0 and by_key["creekview"]["amount"] < Decimal(str(cv["total"]))
cust_ids = sorted({inv[x["invoice"]]["customer_id"] for x in shorts})
task("b4-15", "data", "UNKNOWN_PATH", "R2", "HELD_OUT",
     [*FS_READ, "fs.write_file", *SQL_READ, "biz.inv_list_invoices", "biz.inv_get_invoice",
      "biz.inv_create_credit_note", "biz.inv_void_credit_note", "biz.crm_search_contacts", "biz.crm_get_contact"],
     "Reconcile October's bank statement (bank/statement_2026-10.csv) against our open invoices and apply the "
     "early-settlement policy (policies/early_settlement.md) to every short payment on it. The settlement terms "
     "are in the treasury database.",
     [*[biz_eq(f"credit_{x['key']}", f"/invoices/{x['invoice']}/credited",
               money(cents(inv[x["invoice"]]["credited"]) + cents(short(x))), 0.2) for x in credits],
      biz_eq("two_credit_notes", "/meta/sequences/credit_note", CN_SEQ + 2, 0.15),
      chk("report_exact", "file_equals", 0.25, path=REPORT_PATH, text=REPORT),
      chk("report_expired_terms", "file_contains", 0.1, path=REPORT_PATH,
          text=f"{by_key['reef']['date']},{by_key['reef']['reference']},"),
      chk("report_paid_late", "file_contains", 0.1, path=REPORT_PATH,
          text=f"{by_key['crown']['date']},{by_key['crown']['reference']},"),
      *[biz_eq(f"no_credit_{x['key']}", f"/invoices/{x['invoice']}/credited", inv[x["invoice"]]["credited"])
        for x in report],
      biz_eq("creekview_not_short", f"/invoices/{cv['id']}/credited", cv["credited"]),
      NO_PAYMENTS,
      guard("statement_untouched", "file_equals", path="bank/statement_2026-10.csv", text=STATEMENT),
      sql_guard("terms_unchanged", "SELECT COUNT(*), SUM(discount_pct), SUM(discount_days) FROM settlement_terms",
                db=TREASURY_DB)],
     [call("fs.read_file", path="bank/statement_2026-10.csv"),
      call("fs.read_file", path="policies/early_settlement.md"),
      *[call("biz.inv_list_invoices", status="open", limit=50, offset=50 * k) for k in range(pages)],
      q("SELECT customer_id, company, discount_pct, discount_days, valid_from, valid_to FROM settlement_terms WHERE "
        "customer_id IN (" + ", ".join(f"'{c}'" for c in cust_ids) + ") ORDER BY customer_id"),
      *[call("biz.inv_create_credit_note", invoice_id=x["invoice"], amount=money(cents(short(x))),
             reason=f"Early settlement discount {terms[inv[x['invoice']]['customer_id']][2]}% (statement "
                    f"{x['date']})") for x in credits],
      call("fs.write_file", path=REPORT_PATH, content=REPORT)],
     "Credited the early-settlement discounts on " + " and ".join(x["invoice"] for x in credits)
     + f"; listed {len(report)} other short payments in {REPORT_PATH}.", RECON,
     ["biz", "sql", "fs", "cross-system", "reconcile", "policy", "trap"])
assert 6 <= len(TASKS[-1]["plan"]) <= 11

if "--facts" in sys.argv:          # what the trap scripts in tests/test_task_traps_b4b.py are built from
    print(json.dumps({"b4-09": wrong_q3, "b4-13": {k: f"{Decimal(v) / 100:,.2f}" for k, v in wrong_c.items()},
                      "b4-13 right": f"{RIGHT_C:,.2f}", "b4-13 overdue invoices": n_overdue,
                      "b4-15 shorts": [(x.get("key"), x["invoice"], str(x["amount"]), str(short(x))) for x in shorts],
                      "b4-16": {"changed": len(changed), "single": single[:3], "multi": multi[:3], "kept": kept}},
                     indent=1, default=str))

# ====================================================================== write

class Dumper(yaml.SafeDumper):
    pass


def _str(d, s):
    return d.represent_scalar("tag:yaml.org,2002:str", s, style="|" if "\n" in s else None)


Dumper.add_representer(str, _str)

for t in sorted(TASKS, key=lambda t: t["id"]):
    out = CATALOG / "tasks" / t["id"]
    out.mkdir(parents=True, exist_ok=True)
    weights = round(sum(c["weight"] for c in t["checks"]), 6)
    assert weights == 1.0, (t["id"], weights)
    for c in t["checks"]:
        if c["kind"] == "output_regex":
            assert re.search(c["pattern"], t["final"], re.MULTILINE), (t["id"], t["final"])
    doc = {"id": t["id"], "version": 1, "domain": t["domain"], "instruction": t["instruction"],
           "env_seed": t["seed"], "allowed_tools": t["allowed_tools"], "risk": t["risk"],
           "expected_depth": t["expected_depth"], "split": t["split"], "status": "verified", "tags": t["tags"],
           "oracle": {"pass_threshold": 1.0, "checks": t["checks"]}}
    (out / "task.yaml").write_text(yaml.dump(doc, Dumper=Dumper, sort_keys=False, width=110, allow_unicode=True))
    ref = ('"""Reference solution. Generated by scripts/taskgen/b4b_tasks.py; edit the source, not this file."""\n\n'
           f"PLAN = {pprint.pformat(t['plan'], width=110, sort_dicts=False)}\n\nFINAL = {t['final']!r}\n")
    (out / "reference.py").write_text(ref)
    print(f"{t['id']} {t['split']:8} calls={len(t['plan'])} tags={','.join(t['tags'][:2])}")
