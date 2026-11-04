"""Reference solution: look at the candidates' balances, then one UPDATE.

Balance = amount - successful payments - credit notes. Failed payments don't count, a credit note can close the gap,
and an invoice due today is not past due yet. Drafts and void invoices are not open."""

_BALANCE = ("amount - COALESCE((SELECT SUM(p.amount) FROM payments p WHERE p.invoice_id = invoices.id AND "
            "p.result = 'succeeded'), 0) - COALESCE((SELECT SUM(c.amount) FROM credit_notes c WHERE "
            "c.invoice_id = invoices.id), 0)")

PLAN = [{"tool": "sql.query",
         "args": {"sql": f"SELECT id, due_on, status, {_BALANCE} AS balance FROM invoices "
                         "WHERE status = 'open' ORDER BY id"}},
        {"tool": "sql.execute",
         "args": {"sql": f"UPDATE invoices SET status = 'overdue' WHERE status = 'open' AND due_on < '2026-11-02' "
                         f"AND {_BALANCE} > 0"}}]

FINAL = "Marked 4 invoices overdue: 5009, 5102, 5103, 5109."
