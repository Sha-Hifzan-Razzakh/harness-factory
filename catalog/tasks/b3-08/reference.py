"""Reference solution: one INSERT ... SELECT over issued October invoices (not Qasr's unissued draft) of eligible
accounts, skipping invoices that already carry an outage credit note (Ironbridge was credited by hand)."""

PLAN = [{"tool": "sql.query",
         "args": {"sql": "SELECT i.id, a.name, p.code, i.status, i.issued_on, i.amount, (SELECT COUNT(*) FROM "
                         "credit_notes c WHERE c.invoice_id = i.id AND c.reason = 'outage-2026-10') AS credited "
                         "FROM invoices i JOIN accounts a ON a.id = i.account_id JOIN plans p ON p.id = a.plan_id "
                         "WHERE i.period = '2026-10' AND a.status = 'active' AND a.is_internal = 0 "
                         "AND p.code IN ('pro', 'business') ORDER BY i.id"}},
        {"tool": "sql.execute",
         "args": {"sql": "INSERT INTO credit_notes (invoice_id, amount, issued_on, reason) "
                         "SELECT i.id, ROUND(i.amount * 0.10, 2), '2026-11-02', 'outage-2026-10' FROM invoices i "
                         "JOIN accounts a ON a.id = i.account_id JOIN plans p ON p.id = a.plan_id "
                         "WHERE i.period = '2026-10' AND i.issued_on IS NOT NULL AND i.status != 'void' "
                         "AND a.status = 'active' AND a.is_internal = 0 AND p.code IN ('pro', 'business') "
                         "AND NOT EXISTS (SELECT 1 FROM credit_notes c WHERE c.invoice_id = i.id "
                         "AND c.reason = 'outage-2026-10')"}}]

FINAL = "Issued 6 outage credit notes (Ironbridge Legal already had one): 5101, 5102, 5103, 5106, 5108, 5112."
