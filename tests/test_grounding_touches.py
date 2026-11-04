"""tool_called `touches` (grading/touches.py): grounding counts only a call that touched the task's data."""

import json

import pytest

from harness_factory.grading.touches import (
    grounded_on_data,
    span_args,
    sql_identifiers,
    touched,
    workspace_path,
)
from harness_factory.observability.span import Span, SpanKind
from harness_factory.substrate.tools import ARGS_CHARS
from harness_factory.tasks.model import WeightedCheck


def span(name: str, args: dict | str, status: str = "ok", **attrs) -> Span:
    raw = args if isinstance(args, str) else json.dumps(args)[:ARGS_CHARS]
    return Span(trace_id="t", span_id="s", kind=SpanKind.tool_call, name=name, run_key="k", start_ns=0,
                status=status, attributes={"args": raw, **attrs})


@pytest.mark.parametrize(("sql", "names"), [
    ("SELECT COUNT(*) FROM customers", {"customers"}),
    ("select * from Orders o join ORDER_ITEMS i on i.order_id = o.id", {"orders", "order_items"}),
    ('SELECT * FROM "order items"', {"order items"}),
    ("SELECT * FROM `refunds`", {"refunds"}),
    ("SELECT * FROM [orders]", {"orders"}),
    ("SELECT * FROM main.orders", {"main", "orders"}),
    ('SELECT * FROM "a""b"', {'a"b'}),
])
def test_sql_identifiers_are_found_bare_quoted_and_qualified(sql, names):
    assert names <= sql_identifiers(sql)


@pytest.mark.parametrize("sql", [
    "SELECT 'customers'",                       # a string, not a table
    "SELECT 'it''s customers'",
    "SELECT 1 -- FROM customers",
    "SELECT 1 /* FROM customers */",
    "SELECT 1 /* unterminated FROM customers",
    "SELECT customers_backup FROM x",           # word boundary: another identifier
    "SELECT 1e5",
])
def test_strings_comments_and_longer_names_name_no_table(sql):
    assert "customers" not in sql_identifiers(sql)


def test_a_cut_argument_string_is_closed_again():
    long = "SELECT * FROM orders WHERE note = '" + "x" * 3000 + "'"
    s = span("sql.query", {"sql": long})
    assert len(s.attributes["args"]) == ARGS_CHARS and span_args(s)["sql"].startswith("SELECT * FROM orders")
    assert span_args(span("sql.query", "not json")) is None
    unrecorded = Span(trace_id="t", span_id="s", kind=SpanKind.tool_call, name="biz.inv_list_invoices", run_key="k",
                      start_ns=0)
    assert not grounded_on_data("biz.*", ["customer_id=C-1004"], [unrecorded])[0]   # no args, no free pass


def test_workspace_paths_are_normalised(tmp_path):
    assert workspace_path("./logs/../logs/a.log", None) == "logs/a.log"
    assert workspace_path("logs\\a.log", None) == "logs/a.log"
    assert workspace_path(str(tmp_path / "logs/a.log"), tmp_path) == "logs/a.log"
    assert workspace_path("/elsewhere/logs/a.log", tmp_path) == "/elsewhere/logs/a.log"
    assert workspace_path("", None) == "."


def test_entries_by_call():
    assert touched({"sql": "SELECT * FROM Orders"}, ["orders"]) == "orders"
    assert touched({"sql": "SELECT * FROM orders"}, ['"orders"']) == '"orders"'
    assert touched({"sql": "SELECT 1"}, ["orders"]) is None
    assert touched({"path": "service/config/dev.ini"}, ["service/config/*.ini"]) == "service/config/*.ini"
    assert touched({"path": "service/README.md"}, ["service/config/*.ini"]) is None
    assert touched({"src": "a.txt", "dst": "b.txt"}, ["b.txt"]) == "b.txt"
    assert touched({"customer_id": "C-1004"}, ["customer_id=C-1004"]) == "customer_id=C-1004"
    assert touched({"customer_id": "C-1001"}, ["customer_id=C-1004"]) is None
    assert touched({"status": "open"}, ["customer_id=C-1004"]) == "customer_id=C-1004"   # not narrowed by it
    assert touched({}, ["orders"]) is None                                               # nothing named


def test_only_successful_matching_calls_that_ran_count():
    data = {"sql": "SELECT * FROM orders"}
    assert grounded_on_data("sql.query", ["orders"], [span("sql.query", data)])[0]
    assert not grounded_on_data("sql.query", ["orders"], [span("sql.query", data, status="error")])[0]
    assert not grounded_on_data("sql.query", ["orders"], [span("sql.execute", data)])[0]
    assert not grounded_on_data("sql.query", ["orders"], [span("sql.query", data, fault="empty")])[0]
    assert grounded_on_data("sql.query", ["orders"], [span("sql.query", data, fault="garbled")])[0]
    ok, detail = grounded_on_data("sql.query", ["orders"], [span("sql.query", {"sql": "SELECT 1"})])
    assert not ok and "none touched orders" in detail
    assert grounded_on_data("sql.query", ["orders"], [])[1] == "no successful call matching sql.query"


def test_touches_is_only_for_tool_called_and_never_empty():
    WeightedCheck(id="g", kind="tool_called", pattern="sql.query", weight=0.25, touches=["orders"])
    with pytest.raises(ValueError, match="touches is for tool_called"):
        WeightedCheck(id="g", kind="output_regex", pattern="x", weight=0.25, touches=["orders"])
    with pytest.raises(ValueError, match="touches is for tool_called"):
        WeightedCheck(id="g", kind="tool_called", pattern="sql.query", weight=0.25, touches=[" "])
    with pytest.raises(ValueError):
        WeightedCheck(id="g", kind="tool_called", pattern="sql.query", weight=0.25, touches=[])


def test_setting_touches_is_a_new_task(factory):
    """Unset, the field is left out of the hash (tests/test_graders_depth.py checks every catalog task); set, it
    is part of the task's identity."""
    task = factory.tasks.get("sql-01")
    checks = [c.model_copy(update={"touches": None}) for c in task.oracle.checks]
    bare = task.model_copy(update={"oracle": task.oracle.model_copy(update={"checks": checks})})
    assert bare.content_hash() != task.content_hash()
