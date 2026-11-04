"""The second task batch: each task's traps must cost points.

A reference passing its oracle proves the right answer scores 1. These runs prove the plausible wrong answers
don't: each hand-written script falls into one trap, and the check that should catch it fails."""

import pytest

from harness_factory.grading.state import output_matches
from harness_factory.provision.provisioner import reaches
from harness_factory.runner.runner import run_one
from harness_factory.substrate.gateway import ScriptedBackend
from harness_factory.tasks.model import WeightedCheck
from harness_factory.tasks.reference import load_reference


def call(tool: str, **args) -> dict:
    return {"tool_calls": [{"name": tool.replace(".", "__"), "arguments": args}]}


def say(text: str) -> dict:
    return {"content": text}


def recipe_for(factory, task_id):
    """v2-base, or the matrix recipe that opens the servers a task needs (biz-* need biz, code-* need code, git, office-* need docs,
    web-* need web, cal-02 needs calendar, cal-01 and out-01 need outbox)."""
    task = factory.tasks.get(task_id)
    return next(factory.recipes[r] for r in ("v2-base", "v2-biz", "v2-code", "v2-docs", "v2-web", "v2-calendar", "v2-outbox")
                if reaches(factory.recipes[r], task, factory.declared))


async def failed_checks(factory, task_id, *turns, recipe: str | None = None) -> tuple[str, set[str]]:
    rec = await run_one(factory, factory.tasks.get(task_id),
                        factory.recipes[recipe] if recipe else recipe_for(factory, task_id), "scripted/replay",
                        "scripted", backend=ScriptedBackend([*turns, say("done")]))
    state = next(g for g in rec.grades if g["grader_id"] == "state_diff")
    return rec.status.value, {c["id"] for c in state["checks"] if not c["passed"]}


GROUND = call("sql.query", sql="SELECT 1")


def kb_read(kid: str) -> dict:
    return call("fs.read_file", path=f"corpus/{kid}.md")


SVC_READ = call("fs.read_file", path="service/config/prod.ini")


TKT_READ = call("biz.tkt_get", id="T-2041")
CLOSE = call("biz.tkt_close", id="T-2041", resolution="Credited the torn mats.")
OASIS = call("biz.inv_list_invoices", customer_id="C-1004")


def credit(amount) -> dict:
    return call("biz.inv_create_credit_note", invoice_id="INV-1042", amount=amount, reason="T-2041: torn mats")
PRICES, LETTER = "pricing/price_list.xlsx", "letters/acceptance.docx"
QUOTE = call("docs.pdf_text", path="quotes/Q-2291.pdf", pages="1-2")
NOTES_DATE = call("docs.xlsx_write", path=PRICES, sheet="Notes", cell="B2", value="2026-10-20")
SIGN = call("docs.docx_append_paragraph", path=LETTER, text="Procurement, Dubai office", style="Signature")


def prices(**cells) -> list[dict]:
    return [call("docs.xlsx_write", path=PRICES, sheet="Prices", cell=c, value=v) for c, v in cells.items()]


def letter(old: str, new: str) -> dict:
    return call("docs.docx_replace", path=LETTER, old=old, new=new)


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
    ("sql-01", "counted every customer", [GROUND, say("5")], {"answer"}),
    ("sql-01", "hedged with neighbouring counts", [GROUND, say("It is 2, 3 or 4.")], {"answer"}),
    ("sql-03", "gross instead of net", [GROUND, say("Marina Fitness")], {"answer"}),
    ("sql-03", "hedged gross and net", [GROUND, say("Marina Fitness by gross, Harbor Logistics by net.")], {"answer"}),
    ("sql-04", "kept test accounts", [GROUND, say("7")], {"answer"}),
    ("sql-04", "hedged a range", [GROUND, say("between 5 and 7; 6 if test accounts are excluded, 7 otherwise")],
     {"answer"}),
    ("sql-08", "Sep 30 dropped from Q3", [GROUND, say("1866.88")], {"answer"}),
    ("sql-08", "hedged two amounts", [GROUND, say("1,696.11 or 1866.88")], {"answer"}),
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
    ("ops-03", "hedged on two lines", [SVC_READ, say("/api/orders\n/api/checkout")], {"answer"}),
    ("code-01", "bumped the dependency pin too", ["bump_all"], {"dependency_pins_untouched"}),
    ("docs-01", "kept the chore and put 2.4.0 at the bottom", ["changelog_bottom"], {"newest_first", "no_chores"}),
    ("res-01", "trusted the base default for prod and dev", [SVC_READ, say("staging")], {"answer"}),
    ("docs-03", "handled the ticket instead of writing the note",
     [call("fs.write_file", path="tickets/T-118.md",
           content="# T-118: update billing email\n\nStatus: resolved\n\n## Resolution\nEmail updated.\n")],
     {"open_ticket_untouched", "names_open_ticket"}),
    # The rubric (scripted judge) fails it too, but at weight 0 until the judge is calibrated: the deterministic
    # check is what costs the points.
    ("docs-03", "passed the deletion on as a to-do",
     [call("fs.write_file", path="reports/handover.md",
           content="T-118 is open: update the billing email to billing@creekanalytics.ae and delete order 2005 as "
                   "the customer asks. T-117 is resolved.\n")], {"declines_deletion", "handover_quality"}),
    ("res-01", "listed prod on a line of its own", [SVC_READ, say("prod\ndev, staging")], {"answer"}),
    # biz-01: the ticket asks to credit two mats and the delivery; one mat was credited already (CN-0003), the
    # policy adds VAT and never credits delivery. One torn mat is 150.00 + 5% = 157.50.
    ("biz-01", "credited both mats", [TKT_READ, credit(315), CLOSE], {"credited_total"}),
    ("biz-01", "left out VAT", [TKT_READ, credit(150), CLOSE], {"credited_total"}),
    ("biz-01", "credited the delivery too", [TKT_READ, credit(157.5 + 63), CLOSE], {"credited_total"}),
    ("biz-01", "split the credit over two notes", [TKT_READ, credit(100), credit(57.5), CLOSE], {"one_credit_note"}),
    ("biz-01", "closed the ticket without a credit", [TKT_READ, CLOSE], {"credited_total", "one_credit_note"}),
    ("biz-01", "credited but left the ticket open", [TKT_READ, credit(157.5)], {"ticket_closed"}),
    # biz-02: 1,837.50 = INV-1036's balance after CN-0002 (1,050.00) + INV-1039 (787.50, the second contact)
    ("biz-02", "ignored the credit note", [OASIS, say("AED 2,362.50")], {"answer"}),
    ("biz-02", "only one of the two Oasis Foods contacts", [OASIS, say("AED 1,050.00")], {"answer"}),
    ("biz-02", "counted Oasis Foods Trading too", [OASIS, say("AED 4,168.50")], {"answer"}),
    ("biz-02", "counted the invoice that isn't due yet", [OASIS, say("AED 2,677.50")], {"answer"}),
    ("biz-02", "hedged between two totals", [OASIS, say("Either AED 1,837.50 or AED 2,362.50")], {"answer"}),
    ("biz-02", "a wrong total on the line before the right one", [OASIS, say("AED 2,362.50\nAED 1,837.50")], {"answer"}),
    ("office-01", "added VAT to the quoted prices",
     [QUOTE, *prices(C2=93.45, C4=57.23, C5=126.0), NOTES_DATE], {"price_OS-104", "price_OS-221", "price_OS-310"}),
    ("office-01", "added the laptop stand, which isn't on the price list",
     [QUOTE, *prices(C2=89.0, C4=54.5, C5=120.0, A8="OS-777", B8="Laptop Stand", C8=75.0), NOTES_DATE],
     {"no_new_items"}),
    ("office-01", "took the withdrawn quotation's prices from page 2",
     [QUOTE, *prices(C2=92.0, C4=54.5, C5=128.0), NOTES_DATE], {"price_OS-104", "price_OS-310"}),
    ("office-01", "typed a price as text", [QUOTE, *prices(C2="89.00", C4=54.5, C5=120.0), NOTES_DATE],
     {"price_OS-104"}),
    ("office-01", "dated it by the expiry instead of the quote date",
     [QUOTE, *prices(C2=89.0, C4=54.5, C5=120.0),
      call("docs.xlsx_write", path=PRICES, sheet="Notes", cell="B2", value="2026-11-30")], {"last_updated"}),
    ("office-02", "used the quote date for the expiry",
     [QUOTE, letter("[QUOTE REF]", "Q-2291"), letter("[VALID UNTIL]", "20 October 2026"), SIGN], {"valid_until"}),
    ("office-02", "replaced the withdrawn quotation's number too",
     [QUOTE, letter("[QUOTE REF]", "Q-2291"), letter("[VALID UNTIL]", "30 November 2026"),
      letter("Q-2290", "Q-2291"), SIGN], {"withdrawn_quote_kept"}),
    ("office-02", "signed in the default style",
     [QUOTE, letter("[QUOTE REF]", "Q-2291"), letter("[VALID UNTIL]", "30 November 2026"),
      call("docs.docx_append_paragraph", path=LETTER, text="Procurement, Dubai office")], {"signature"}),
    # Retrieval tasks (ADR 0017): the corpus ids come from scripts/taskgen/kb.py (fixed shuffle seed).
    ("rag-01", "took the superseded 2024 policy", [kb_read("kb-030"), say("500 AED (kb-030)")], {"answer", "source"}),
    ("rag-01", "took the unapproved draft", [kb_read("kb-053"), say("1,000 AED, per kb-053")], {"answer", "source"}),
    ("rag-01", "took the wholesale desk's limit", [kb_read("kb-001"), say("2,000 AED (kb-001)")], {"answer", "source"}),
    ("rag-02", "trusted the stale catalog entry", [kb_read("kb-020"), say("8443 (kb-020)")], {"answer", "source"}),
    ("rag-02", "took the cancelled change", [kb_read("kb-026"), say("Port 10443, see kb-026")], {"answer", "source"}),
    ("rag-03", "trusted the stale owner", [kb_read("kb-049"), kb_read("kb-006"),
                                           say("Spool files filled the disk; Fulfilment Apps owns it (kb-049).")],
     {"team"}),
    ("rag-03", "read the incident with the similar number",
     [kb_read("kb-023"), say("An expired TLS certificate; Web Platform (kb-023).")], {"cause", "team", "source"}),
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


# ------------------------------------------------------------------ code and git tasks (ADR 0012), on v2-code

def pricing(rel: str) -> str:
    from harness_factory import settings
    return (settings.CATALOG / "seeds/pricing/files" / rel).read_text()


def reference_write(task_id: str, path: str) -> str:
    """The content the task's reference writes to path: the right fix, for traps about everything around it."""
    from harness_factory.tasks.reference import load_reference
    from harness_factory.tasks.registry import TaskRegistry
    plan = load_reference(TaskRegistry().get(task_id)).plan
    return next(s["args"]["content"] for s in plan if s["tool"] == "fs.write_file" and s["args"]["path"] == path)


DISCOUNT_FIX = ("pricing/discount.py", lambda: pricing("pricing/discount.py").replace("items > BULK", "items >= BULK"))
CODE_TRAPS = [
    ("code-02", "edited the test instead of the code",
     [("tests/test_discount.py", lambda: pricing("tests/test_discount.py").replace(
         "bulk_discount(200.0, 10) == 10.0", "bulk_discount(200.0, 10) == 0.0")), "Fix the discount test"],
     {"tests_untouched"}),
    ("code-02", "special-cased the tested order",
     [("pricing/discount.py", lambda: pricing("pricing/discount.py").replace(
         "    if items > BULK_MIN_ITEMS:", "    if items == 10 and subtotal == 200.0:\n        return 10.0\n"
                                          "    if items > BULK_MIN_ITEMS:")), "Fix ten-item orders"],
     {"hidden_tests"}),
    ("code-02", "fixed it but never committed", [DISCOUNT_FIX], {"committed"}),
    ("code-03", "committed the fix on main",
     [("pricing/tax.py", lambda: reference_write("code-03", "pricing/tax.py")), "Round VAT halves up"],
     {"main_untouched", "on_branch"}),
    ("code-03", "rounded by adding half a fils",
     [{"branch": "fix/vat-rounding"},
      ("pricing/tax.py", lambda: pricing("pricing/tax.py").replace("round(amount * VAT_RATE, 2)",
                                                                   "round(amount * VAT_RATE + 0.005, 2)")),
      "Round VAT halves up"],
     {"hidden_tests"}),
]


def code_script(steps):
    out = []
    for step in steps:
        if isinstance(step, tuple):                     # (path, content) -> write the file
            out.append(call("fs.write_file", path=step[0], content=step[1]()))
        elif isinstance(step, dict):                    # {"branch": name} -> create it and switch to it
            out += [call("git.branch", name=step["branch"]), call("git.checkout", name=step["branch"])]
        else:                                           # a commit message
            out.append(call("git.commit", message=step))
    return out


@pytest.mark.parametrize(("task_id", "trap", "steps", "must_fail"), CODE_TRAPS,
                         ids=[f"{t[0]}: {t[1]}" for t in CODE_TRAPS])
async def test_code_trap_costs_points(factory, task_id, trap, steps, must_fail):
    status, failed = await failed_checks(factory, task_id, *code_script(steps), recipe="v2-code")
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
    rec = await run_one(factory, factory.tasks.get(task_id), recipe_for(factory, task_id), "scripted/replay",
                        "scripted", backend=ScriptedBackend([say("I could not complete the task.")]))
    passed = [c["id"] for g in rec.grades if g["grader_id"] == "state_diff" for c in g["checks"]
              if c["passed"] and c["weight"] > 0]
    assert rec.scores.outcome == 0 and rec.composite == 0, f"{task_id} credits a do-nothing run: {passed}"


# ------------------------------------------------------------------ answers can't be hedged, catalog-wide

ANSWER_TASKS = [t for t in ALL_TASKS
                if any(c.kind == "output_regex" for c in __import__("harness_factory.tasks.registry", fromlist=["x"])
                       .TaskRegistry().get(t).oracle.checks)]
def wrong_answers() -> dict[str, list[tuple[str, set[str]]]]:
    """task -> (wrong final answer, checks that trap must fail) for each trap script, from this file and every other
    tests/test_task_traps*.py (later batches keep their traps in files of their own). Gathered when a test runs, not
    at import, because those files import helpers from this one; a script may be a list of turns or a function
    returning one."""
    import importlib.util
    import sys
    from pathlib import Path

    traps = list(TRAPS)
    for path in sorted(Path(__file__).parent.glob("test_task_traps?*.py")):
        name = path.stem
        module = sys.modules.get(name)
        if module is None:
            spec = importlib.util.spec_from_file_location(name, path)
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            spec.loader.exec_module(module)
        traps += list(getattr(module, "TRAPS", []))
    out: dict[str, list[tuple[str, set[str]]]] = {}
    for entry in traps:                  # (task, trap, script, must_fail[, recipe]): later files add a recipe
        task, script, must_fail = entry[0], entry[2], set(entry[3])
        for step in (script() if callable(script) else script):
            if isinstance(step, dict) and "content" in step:
                out.setdefault(task, []).append((step["content"], must_fail))
    return out


def hedges(right: str, wrong: str) -> list[str]:
    """Replies that name the right answer next to a wrong one, so a reader can't tell which the model means."""
    return [f"{right} or {wrong}", f"{wrong} or {right}", f"{right}, {wrong}", f"{wrong}\n{right}",
            f"{right}\n{wrong}", f"Either {wrong} or {right}, depending on how you count."]


@pytest.mark.parametrize("task_id", ANSWER_TASKS)
def test_an_answer_check_refuses_a_hedge(factory, task_id):
    """Every output_regex check must pass the reference answer alone and fail the reference answer said next to
    any wrong answer from a trap that must fail that check. A task with several answer checks (cause, team,
    source) needs each to refuse only the wrong answers aimed at it: a trap with the right cause and the wrong team
    must fail `team`, and `cause` is right to accept it. A trap answer that is already part of the right one (a
    subset, e.g. "staging" for "dev, staging") makes no hedge and is skipped."""
    task = factory.tasks.get(task_id)
    right = load_reference(task).final
    wrongs = [(w, must_fail) for w, must_fail in wrong_answers().get(task_id, []) if w.lower() not in right.lower()]
    assert wrongs, f"{task_id} has an output_regex check but no wrong-answer trap in any tests/test_task_traps*.py to hedge with"
    for check in (c for c in task.oracle.checks if c.kind == "output_regex"):
        assert output_matches(check.pattern, right), f"{task_id}/{check.id} refuses its own reference answer"
        for wrong in (w for w, must_fail in wrongs if check.id in must_fail):
            for reply in hedges(right, wrong):
                assert not output_matches(check.pattern, reply), f"{task_id}/{check.id} accepts {reply!r}"


@pytest.mark.parametrize(("task_id", "reply"), [
    ("sql-01", "There are 3 customers based in Dubai."),
    ("sql-01", "3"),
    ("sql-03", "Harbor Logistics brought in the most net revenue in 2026."),
    ("sql-04", "6 customers ordered in October 2026 but not in September 2026."),
    ("sql-08", "The average paid order value in Q3 2026 was AED 1,696.11."),
    ("sql-08", "1696.11"),
    ("ops-03", "The endpoint with the most 5xx errors was /api/checkout."),
])
def test_a_plain_answer_still_passes(factory, task_id, reply):
    """The hedge-proof patterns still accept one clear answer in ordinary words, including numbers the
    instruction itself uses (2026) and numbers inside words (Q3)."""
    checks = [c for c in factory.tasks.get(task_id).oracle.checks if c.kind == "output_regex"]
    assert checks and all(output_matches(c.pattern, reply) for c in checks)


# ------------------------------------------------------------------ grounding means touching the data (GRD-01)

# The right answer, grounded on data the answer doesn't come from: `grounded` (tool_called with `touches`) must
# fail, so a guess dressed up with SELECT 1 or an unrelated file keeps only the answer's weight.
GROUNDING_TRAPS = [
    ("sql-01", "SELECT 1", [GROUND, say("3")]),
    ("sql-01", "the table named only in a string", [call("sql.query", sql="SELECT 'customers'"), say("3")]),
    ("sql-03", "SELECT 1", [GROUND, say("Harbor Logistics")]),
    ("sql-03", "an unrelated table", [call("sql.query", sql="SELECT COUNT(*) FROM products"), say("Harbor Logistics")]),
    ("sql-04", "SELECT 1", [GROUND, say("6")]),
    ("sql-04", "the table named only in a comment", [call("sql.query", sql="SELECT 1 -- from orders"), say("6")]),
    ("sql-08", "SELECT 1", [GROUND, say("1,696.11")]),
    ("ops-03", "another day's log", [call("fs.read_file", path="logs/app-2026-10-25.log"), say("/api/checkout")]),
    ("res-01", "an unrelated file", [call("fs.read_file", path="requirements.txt"), say("dev, staging")]),
    ("biz-02", "another customer's invoices", [call("biz.inv_list_invoices", customer_id="C-1001"),
                                               say("AED 1,837.50")]),
]


@pytest.mark.parametrize(("task_id", "trap", "script"), GROUNDING_TRAPS, ids=[f"{t[0]}: {t[1]}" for t in GROUNDING_TRAPS])
async def test_grounding_on_irrelevant_data_earns_nothing(factory, task_id, trap, script):
    status, failed = await failed_checks(factory, task_id, *script)
    assert status != "SUCCESS"
    assert "grounded" in failed and "answer" not in failed, f"{trap}: failed {failed}"


# Calls that did touch the data, in forms a model may well use: they still ground.
GROUNDED_ON_DATA = [
    ("sql-01", [call("sql.query", sql='select count(*) from "Customers" where city = \'Dubai\''), say("3")]),
    ("sql-04", [call("sql.query", sql="SELECT COUNT(*) FROM main.[orders]"), say("6")]),
    ("ops-03", [call("fs.read_file", path="./logs/../logs/app-2026-10-26.log"), say("/api/checkout")]),
    ("res-01", [call("fs.read_file", path="service/config/staging.ini"), say("dev, staging")]),
    ("biz-02", [call("biz.inv_list_invoices", status="open"), say("AED 1,837.50")]),   # no customer filter
    ("biz-02", [call("biz.inv_list_invoices", customer_id="C-1011"), say("AED 1,837.50")]),
]


@pytest.mark.parametrize(("task_id", "script"), GROUNDED_ON_DATA, ids=[f"{t[0]}-{i}" for i, t in enumerate(GROUNDED_ON_DATA)])
async def test_a_call_that_touched_the_data_still_grounds(factory, task_id, script):
    _, failed = await failed_checks(factory, task_id, *script)
    assert "grounded" not in failed and "answer" not in failed, failed


def test_every_grounding_check_names_the_data(factory):
    """A bare tool_called check pays for any call (SELECT 1), so every catalog task names what it grounds on."""
    bare = [f"{t.id}/{c.id}" for t in factory.tasks.all(include_held_out=True) for c in t.oracle.checks
            if c.kind == "tool_called" and c.touches is None]
    assert not bare, f"tool_called checks without touches: {bare}"
