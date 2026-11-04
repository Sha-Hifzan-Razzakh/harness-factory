"""Reference solution: read the policy (peak measurement, add-ons count, active non-internal only), then one query."""

PLAN = [{"tool": "fs.read_file", "args": {"path": "docs/seat_policy.md"}},
        {"tool": "sql.query",
         "args": {"sql": "SELECT a.name, p.seats_included + COALESCE((SELECT SUM(s.seats) FROM seat_addons s "
                         "WHERE s.account_id = a.id), 0) AS allowance, MAX(u.seats) AS peak FROM accounts a "
                         "JOIN plans p ON p.id = a.plan_id JOIN seat_usage u ON u.account_id = a.id "
                         "WHERE a.status = 'active' AND a.is_internal = 0 AND u.measured_on >= '2026-10-01' "
                         "AND u.measured_on < '2026-11-01' GROUP BY a.id HAVING peak > allowance ORDER BY a.name"}}]

FINAL = "Copperleaf Studio, Tidewater Retail"
