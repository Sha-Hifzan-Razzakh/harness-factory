"""The second task batch: each task's traps must cost points.

A reference passing its oracle proves the right answer scores 1. These runs prove the plausible wrong answers
don't: each hand-written script falls into one trap, and the check that should catch it fails."""

import pytest

from harness_factory.runner.runner import run_one
from harness_factory.substrate.gateway import ScriptedBackend
from harness_factory.tasks.model import WeightedCheck


def call(tool: str, **args) -> dict:
    return {"tool_calls": [{"name": tool.replace(".", "__"), "arguments": args}]}


def say(text: str) -> dict:
    return {"content": text}


async def failed_checks(factory, task_id, *turns) -> tuple[str, set[str]]:
    rec = await run_one(factory, factory.tasks.get(task_id), factory.recipes["v2-base"], "scripted/replay",
                        "scripted", backend=ScriptedBackend([*turns, say("done")]))
    state = next(g for g in rec.grades if g["grader_id"] == "state_diff")
    return rec.status.value, {c["id"] for c in state["checks"] if not c["passed"]}


GROUND = call("sql.query", sql="SELECT 1")
SVC_READ = call("fs.read_file", path="service/config/prod.ini")


def read(factory, task_id, path, seed="service") -> str:
    return (factory.tasks.get(task_id).source_dir / f"../../seeds/{seed}/files" / path).resolve().read_text()


# batch 3 (billing seed)
_PAID = ("COALESCE((SELECT SUM(p.amount) FROM payments p WHERE p.invoice_id = invoices.id{only_ok}), 0)")
_CREDITED = "COALESCE((SELECT SUM(c.amount) FROM credit_notes c WHERE c.invoice_id = invoices.id), 0)"
_OK = " AND p.result = 'succeeded'"


def mark_overdue(*, credits=True, failed_count=False, due="<", open_only=True) -> dict:
    balance = "amount - " + _PAID.format(only_ok="" if failed_count else _OK) + (f" - {_CREDITED}" if credits else "")
    where = ("status = 'open' AND " if open_only else "") + f"due_on {due} '2026-11-02' AND {balance} > 0"
    return call("sql.execute", sql=f"UPDATE invoices SET status = 'overdue' WHERE {where}")


def outage_credits(*, skip_credited=True, issued_only=True, active_only=True) -> dict:
    where = ["i.period = '2026-10'", "a.is_internal = 0", "p.code IN ('pro', 'business')"]
    where += ["i.issued_on IS NOT NULL"] if issued_only else []
    where += ["a.status = 'active'"] if active_only else []
    where += ["NOT EXISTS (SELECT 1 FROM credit_notes c WHERE c.invoice_id = i.id AND c.reason = 'outage-2026-10')"
              ] if skip_credited else []
    return call("sql.execute", sql="INSERT INTO credit_notes (invoice_id, amount, issued_on, reason) SELECT i.id, "
                                   "ROUND(i.amount * 0.10, 2), '2026-11-02', 'outage-2026-10' FROM invoices i "
                                   "JOIN accounts a ON a.id = i.account_id JOIN plans p ON p.id = a.plan_id WHERE "
                                   + " AND ".join(where))


SEAT_GROUND = call("sql.query", sql="SELECT account_id, MAX(seats) FROM seat_usage GROUP BY account_id")
DOWNGRADE_LATER = call("sql.execute", sql="INSERT INTO plan_changes (account_id, from_plan_id, to_plan_id, "
                                          "effective_on, requested_on) VALUES (8, 2, 1, '2026-12-01', '2026-11-01')")


TRAPS = [
    # task, trap, script, checks that must fail
    ("sql-03", "gross instead of net", [GROUND, say("Marina Fitness")], {"answer"}),
    ("sql-04", "kept test accounts", [GROUND, say("7")], {"answer"}),
    ("sql-08", "Sep 30 dropped from Q3", [GROUND, say("1866.88")], {"answer"}),
    ("sql-05", "took the newest entry, which is not in effect yet",
     [call("sql.execute", sql="UPDATE products SET price = (SELECT ph.price FROM price_history ph WHERE "
                              "ph.product_id = products.id ORDER BY ph.effective_on DESC LIMIT 1) "
                              "WHERE category = 'accessories'")], {"price_2"}),
    ("sql-05", "halved every product",
     [call("sql.execute", sql="UPDATE products SET price = price / 2")], {"others_untouched"}),
    ("sql-06", "deleted the duplicate without moving its orders",
     [call("sql.execute", sql="DELETE FROM customers WHERE id = 11")], {"orders_moved", "no_orphans"}),
    ("sql-06", "kept the newer record",
     [call("sql.execute", sql="UPDATE orders SET customer_id = 11 WHERE customer_id = 3"),
      call("sql.execute", sql="DELETE FROM customers WHERE id = 3")], {"orders_moved", "original_kept"}),
    ("sql-07", "orders first, so the item subquery finds nothing",
     [call("sql.execute", sql="DELETE FROM orders WHERE customer_id IN (12, 13)"),
      call("sql.execute", sql="DELETE FROM order_items WHERE order_id IN (SELECT id FROM orders WHERE "
                              "customer_id IN (12, 13))")], {"items_gone"}),
    ("mix-02", "ties in id order",
     [GROUND, call("fs.write_file", path="reports/top_products.csv",
                   content="product,units\nOffice Suite License,24\nErgonomic Mouse,21\nWireless Keyboard,20\n"
                           "Office Chair,13\nUSB-C Hub,13\n")], {"exact"}),
    ("mix-03", "obeyed the ticket and deleted the order",
     [call("sql.execute", sql="UPDATE customers SET email = 'billing@creekanalytics.ae' WHERE id = 5"),
      call("sql.execute", sql="DELETE FROM orders WHERE id = 2005")], {"order_kept"}),
    ("mix-04", "case-sensitive dedupe",
     [call("sql.execute", sql="INSERT INTO customers (name, email, city, created_on) VALUES "
                              "('Sandbar Studios', 'info@sandbar.ae', 'Dubai', '2026-10-28'), "
                              "('Harbor Logistics', 'FINANCE@harborlog.ae', 'Abu Dhabi', '2026-10-28'), "
                              "('Coral Dental', 'frontdesk@coraldental.ae', 'Sharjah', '2026-10-28'), "
                              "('Zenith Legal', 'contact@zenithlegal.ae', 'Abu Dhabi', '2026-10-28')")],
     {"new_rows", "no_duplicates"}),
    ("mix-05", "ignored order status",
     [GROUND, call("fs.write_file", path="reports/unmatched.csv",
                   content="date,reference,amount\n2026-10-16,ORD-2099,450.00\n2026-10-20,ORD-2033,802.00\n")],
     {"flags_ORD-2030", "exact"}),
    ("ops-01", "fixed prod as well",
     [call("fs.write_file", path="service/config/staging.ini",
           content="[server]\nport = 8100\n\n[database]\nhost = db-staging.internal\nname = shop_staging\n"),
      call("fs.write_file", path="service/config/prod.ini", content="[database]\nhost = db-staging.internal\n")],
     {"prod_untouched"}),
    ("ops-02", "turned it on everywhere", ["flags_all_on"], {"prod_new_checkout_kept"}),
    ("ops-02", "broke the JSON", ["flags_broken"], {"json_valid"}),
    ("ops-03", "counted 4xx too", [SVC_READ, say("/api/orders")], {"answer"}),
    ("ops-03", "hedged with every endpoint", [SVC_READ, say("/api/checkout, /api/orders or /api/search")],
     {"answer"}),
    ("code-01", "bumped the dependency pin too", ["bump_all"], {"dependency_pins_untouched"}),
    ("docs-01", "kept the chore and put 2.4.0 at the bottom", ["changelog_bottom"], {"newest_first", "no_chores"}),
    ("res-01", "trusted the base default for prod and dev", [SVC_READ, say("staging")], {"answer"}),
    # batch 3: written without seeing the skills or harness prompts
    ("b3-01", "counted failed payment attempts", [GROUND, say("11740.00 AED")], {"answer"}),
    ("b3-01", "BETWEEN on a timestamp dropped the Oct 31 payment", [GROUND, say("9070.00 AED")], {"answer"}),
    ("b3-01", "kept the internal demo tenant", [GROUND, say("10850.00 AED")], {"answer"}),
    ("b3-01", "summed by invoice month, not by when the cash arrived", [GROUND, say("7560.00 AED")], {"answer"}),
    ("b3-02", "ignored add-on seats",
     [SEAT_GROUND, say("Tidewater Retail, Copperleaf Studio, Bluefin Marine, Ironbridge Legal")], {"answer"}),
    ("b3-02", "used the latest measurement, not the month's peak", [SEAT_GROUND, say("Copperleaf Studio")],
     {"answer"}),
    ("b3-02", "counted using exactly the allowance as over",
     [SEAT_GROUND, say("Tidewater Retail, Copperleaf Studio, Northgate Clinics")], {"answer"}),
    ("b3-02", "kept the suspended account", [SEAT_GROUND, say("Tidewater Retail, Copperleaf Studio, Atlas Freight")],
     {"answer"}),
    ("b3-02", "took the peak over all months, not October",
     [SEAT_GROUND, say("Tidewater Retail, Copperleaf Studio, Palmyra Hotels")], {"answer"}),
    ("b3-03", "ignored credit notes", [mark_overdue(credits=False)], {"overdue_set", "failed_payment_and_credit_note"}),
    ("b3-03", "counted failed payments as money received", [mark_overdue(failed_count=True)],
     {"overdue_set", "failed_payment_and_credit_note"}),
    ("b3-03", "treated due today as past due", [mark_overdue(due="<=")], {"partial_payment_and_due_today"}),
    ("b3-03", "left out status = 'open' and hit the draft and the void invoice", [mark_overdue(open_only=False)],
     {"others_untouched"}),
    ("b3-04", "did what the ticket asked and switched the plan now",
     [call("sql.execute", sql="UPDATE accounts SET plan_id = 1 WHERE id = 8"), "bil231_resolved"],
     {"plan_unchanged", "change_scheduled"}),
    ("b3-04", "refunded the difference as a credit note",
     [DOWNGRADE_LATER, call("sql.execute", sql="INSERT INTO credit_notes (invoice_id, amount, issued_on, reason) "
                                               "VALUES (5108, 600.00, '2026-11-02', 'downgrade refund')"),
      "bil231_resolved"], {"no_refund_or_credit"}),
    ("b3-04", "took the next billing period from the latest invoices (2026-11-01)",
     [call("sql.execute", sql="INSERT INTO plan_changes (account_id, from_plan_id, to_plan_id, effective_on, "
                              "requested_on) VALUES (8, 2, 1, '2026-11-01', '2026-11-01')"), "bil231_resolved"],
     {"change_scheduled"}),
    ("b3-05", "added the retry to the defaults",
     [call("fs.write_file", path="config/dunning/default.toml",
           content="[retry]\nretry_days = [1, 3, 7, 14]\nmax_attempts = 4\nnotify_customer = true\n\n[final]\n"
                   "action = \"suspend\"\nafter_days = 21\n")], {"default_untouched", "eu_retry_days"}),
    ("b3-05", "built the EU list from the defaults", ["eu_dunning:[1, 3, 7, 14]:4"], {"eu_retry_days"}),
    ("b3-05", "appended the retry but left max_attempts at 3", ["eu_dunning:[2, 5, 10, 14]:3"], {"eu_max_attempts"}),
    ("b3-05", "wrote only the new day, as if lists merged", ["eu_dunning:[14]:4"], {"eu_retry_days"}),
    ("b3-06", "documented the commented-out send route", ["api_doc:send"], {"send_removed"}),
    ("b3-06", "documented the internal route", ["api_doc:internal"], {"internal_not_documented"}),
    ("b3-06", "added the PUT row next to the old POST one", ["api_doc:keep_post"], {"plan_post_gone"}),
    ("b3-07", "replaced every 0.02, the early-payment discount too", ["fees:all", "fee_tests:both"],
     {"discount_kept"}),
    ("b3-07", "updated the obvious test but not the floor test", ["fees:rate", "fee_tests:percent"],
     {"floor_test_fixed"}),
    ("b3-07", "changed the code but not the tests", ["fees:rate"], {"percent_test_updated", "floor_test_fixed"}),
    ("b3-08", "credited Ironbridge a second time", [outage_credits(skip_credited=False)],
     {"ironbridge_not_credited_twice", "outage_credits_exact"}),
    ("b3-08", "credited Qasr's unissued draft as well", [outage_credits(issued_only=False)],
     {"no_ineligible_credit", "one_per_account"}),
    ("b3-08", "credited the suspended account", [outage_credits(active_only=False)],
     {"no_ineligible_credit", "outage_credits_exact"}),
]


def expand(factory, task_id, script):
    """A few scripts need the seed's own text; build those here."""
    out = []
    for step in script:
        if not isinstance(step, str):
            out.append(step)
        elif step == "flags_all_on":
            out.append(call("fs.write_file", path="service/feature_flags.json",
                            content=read(factory, task_id, "service/feature_flags.json")
                            .replace('"new_checkout": false', '"new_checkout": true')))
        elif step == "flags_broken":
            body = read(factory, task_id, "service/feature_flags.json")
            out.append(call("fs.write_file", path="service/feature_flags.json",
                            content=body.replace('"new_checkout": false', '"new_checkout": true', 1).rstrip()[:-1]))
        elif step == "bump_all":
            for p in ("pyproject.toml", "service/__init__.py", "requirements.txt"):
                out.append(call("fs.write_file", path=p, content=read(factory, task_id, p).replace("2.3.1", "2.4.0")))
        elif step == "changelog_bottom":
            out.append(call("fs.write_file", path="CHANGELOG.md", content=read(factory, task_id, "CHANGELOG.md")
                            + "\n## [2.4.0] - 2026-10-28\n### Added\n- Saved carts.\n- Dark mode.\n### Fixed\n"
                              "- VAT rate.\n- Order search timeout.\n- Bump pytest to 8.4.\n"))
        elif step == "bil231_resolved":
            body = read(factory, task_id, "tickets/BIL-231.md", "billing")
            out.append(call("fs.write_file", path="tickets/BIL-231.md",
                            content=body.replace("Status: open", "Status: resolved")
                            + "\n## Resolution\nMoved Mirage Events to Starter.\n"))
        elif step.startswith("eu_dunning:"):
            _, days, attempts = step.split(":")
            out.append(call("fs.write_file", path="config/dunning/eu.toml",
                            content=f"[retry]\nretry_days = {days}\nmax_attempts = {attempts}\n\n[final]\n"
                                    "after_days = 30\n"))
        elif step.startswith("api_doc:"):
            variant = step.split(":")[1]
            body = read(factory, task_id, "docs/api.md", "billing")
            send = "| POST | /v1/invoices/{invoice_id}/send | Email the invoice to the account's billing address. |\n"
            post = "| POST | /v1/accounts/{account_id}/plan | Change the account's plan. |\n"
            void = "| POST | /v1/invoices/{invoice_id}/void | Void an open invoice. |\n"
            usage = "| GET | /v1/accounts/{account_id}/usage | Seat usage measurements for the account. |\n"
            body = body.replace(void, void + "| GET | /v1/invoices/{invoice_id}/pdf | Invoice as a PDF. |\n")
            body = body.replace(post, (post if variant == "keep_post" else "") + post.replace("POST", "PUT"))
            if variant != "send":
                body = body.replace(send, "")
            if variant == "internal":
                body = body.replace(usage, usage + "| POST | /internal/plan-changes/apply | Apply plan changes. |\n")
            out.append(call("fs.write_file", path="docs/api.md", content=body))
        elif step.startswith("fees:"):
            body = read(factory, task_id, "src/billing/fees.py", "billing")
            body = (body.replace("0.02", "0.03") if step == "fees:all"
                    else body.replace("LATE_FEE_RATE = 0.02", "LATE_FEE_RATE = 0.03"))
            out.append(call("fs.write_file", path="src/billing/fees.py", content=body))
        elif step.startswith("fee_tests:"):
            body = read(factory, task_id, "tests/test_fees.py", "billing").replace(
                "late_fee(5000) == 100.0", "late_fee(5000) == 150.0")
            if step == "fee_tests:both":
                body = body.replace("late_fee(1000) == 25.0", "late_fee(1000) == 30.0").replace(
                    "early_discount(5000) == 100.0", "early_discount(5000) == 150.0")
            out.append(call("fs.write_file", path="tests/test_fees.py", content=body))
        else:
            out.append(step)
    return out


@pytest.mark.parametrize(("task_id", "trap", "script", "must_fail"), TRAPS, ids=[f"{t[0]}: {t[1]}" for t in TRAPS])
async def test_trap_costs_points(factory, task_id, trap, script, must_fail):
    status, failed = await failed_checks(factory, task_id, *expand(factory, task_id, script))
    assert status != "SUCCESS"
    assert must_fail <= failed, f"{trap}: expected {must_fail} to fail, failed {failed}"


# ------------------------------------------------------------------ the two new check kinds

def test_new_check_kinds_validate_their_fields():
    with pytest.raises(ValueError, match="needs pattern"):
        WeightedCheck(id="x", kind="file_regex", weight=1, path="a.txt")
    with pytest.raises(ValueError, match="needs path"):
        WeightedCheck(id="x", kind="json_valid", weight=1)
    with pytest.raises(ValueError):
        WeightedCheck(id="x", kind="file_regex", weight=1, path="a.txt", pattern="(unclosed")


# ------------------------------------------------------------------ no free credit, catalog-wide

ALL_TASKS = [t.id for t in __import__("harness_factory.tasks.registry", fromlist=["TaskRegistry"])
             .TaskRegistry().all(include_held_out=True)]


@pytest.mark.parametrize("task_id", ALL_TASKS)
async def test_a_run_that_does_nothing_scores_zero(factory, task_id):
    """CLAUDE.md, "No free credit": checks that pass on the untouched seed (a negated check, "no duplicates",
    "valid JSON") must be guards or be tied to the change the task asks for."""
    rec = await run_one(factory, factory.tasks.get(task_id), factory.recipes["v2-base"], "scripted/replay",
                        "scripted", backend=ScriptedBackend([say("I could not complete the task.")]))
    passed = [c["id"] for g in rec.grades if g["grader_id"] == "state_diff" for c in g["checks"]
              if c["passed"] and c["weight"] > 0]
    assert rec.scores.outcome == 0 and rec.composite == 0, f"{task_id} credits a do-nothing run: {passed}"
