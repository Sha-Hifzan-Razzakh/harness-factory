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


def read(factory, task_id, path) -> str:
    return (factory.tasks.get(task_id).source_dir / "../../seeds/service/files" / path).resolve().read_text()


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
