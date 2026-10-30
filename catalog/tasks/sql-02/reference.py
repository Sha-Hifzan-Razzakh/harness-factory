PLAN = [
    {"tool": "sql.describe_table", "args": {"table": "invoices"}},
    {"tool": "sql.execute", "args": {"sql": "UPDATE invoices SET status = 'overdue' "
                                            "WHERE status = 'unpaid' AND issued_on < '2026-01-01'"}},
]
FINAL = "Marked 2 invoices (101, 103) as overdue."
