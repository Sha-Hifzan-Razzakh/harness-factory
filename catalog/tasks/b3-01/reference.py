"""Reference solution: successful payments received in October (timestamps, so the upper bound is exclusive)."""

PLAN = [{"tool": "sql.query",
         "args": {"sql": "SELECT ROUND(SUM(p.amount), 2) FROM payments p JOIN invoices i ON i.id = p.invoice_id "
                         "JOIN accounts a ON a.id = i.account_id WHERE p.result = 'succeeded' AND a.is_internal = 0 "
                         "AND p.received_at >= '2026-10-01' AND p.received_at < '2026-11-01'"}}]

FINAL = "9960.00 AED collected in October 2026."
