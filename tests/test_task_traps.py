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
    """v2-base, or the matrix recipe that opens the servers a task needs (biz-* need biz, code-* need code, git, office-* need docs)."""
    task = factory.tasks.get(task_id)
    return next(factory.recipes[r] for r in ("v2-base", "v2-biz", "v2-code", "v2-docs") if reaches(factory.recipes[r], task, factory.declared))


async def failed_checks(factory, task_id, *turns, recipe: str | None = None) -> tuple[str, set[str]]:
    rec = await run_one(factory, factory.tasks.get(task_id),
                        factory.recipes[recipe] if recipe else recipe_for(factory, task_id), "scripted/replay",
                        "scripted", backend=ScriptedBackend([*turns, say("done")]))
    state = next(g for g in rec.grades if g["grader_id"] == "state_diff")
    return rec.status.value, {c["id"] for c in state["checks"] if not c["passed"]}


GROUND = call("sql.query", sql="SELECT 1")
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


def read(factory, task_id, path) -> str:
    return (factory.tasks.get(task_id).source_dir / "../../seeds/service/files" / path).resolve().read_text()


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
]


def expand(factory, task_id, script):
    """A few scripts need the seed's own text; build those here."""
    out = []
    for step in script:
        if step == "flags_all_on":
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
def wrong_answers() -> dict[str, list[str]]:
    """task -> the wrong final answers its trap scripts give, from this file and every other tests/test_task_traps*.py
    (later batches keep their traps in files of their own). Gathered when a test runs, not at import, because those
    files import helpers from this one; a script may be a list of turns or a function returning one."""
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
    out: dict[str, list[str]] = {}
    for entry in traps:                  # (task, trap, script, must_fail[, recipe]): later files add a recipe
        task, script = entry[0], entry[2]
        for step in (script() if callable(script) else script):
            if isinstance(step, dict) and "content" in step:
                out.setdefault(task, []).append(step["content"])
    return out


def hedges(right: str, wrong: str) -> list[str]:
    """Replies that name the right answer next to a wrong one, so a reader can't tell which the model means."""
    return [f"{right} or {wrong}", f"{wrong} or {right}", f"{right}, {wrong}", f"{wrong}\n{right}",
            f"{right}\n{wrong}", f"Either {wrong} or {right}, depending on how you count."]


@pytest.mark.parametrize("task_id", ANSWER_TASKS)
def test_an_answer_check_refuses_a_hedge(factory, task_id):
    """Every output_regex check must pass the reference answer alone and fail the reference answer said next to
    any wrong answer from this file's trap scripts. A trap answer that is already part of the right one (a
    subset, e.g. "staging" for "dev, staging") makes no hedge and is skipped."""
    task = factory.tasks.get(task_id)
    right = load_reference(task).final
    wrongs = [w for w in wrong_answers().get(task_id, []) if w.lower() not in right.lower()]
    assert wrongs, f"{task_id} has an output_regex check but no wrong-answer trap in any tests/test_task_traps*.py to hedge with"
    for check in (c for c in task.oracle.checks if c.kind == "output_regex"):
        assert output_matches(check.pattern, right), f"{task_id}/{check.id} refuses its own reference answer"
        for wrong in wrongs:
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
