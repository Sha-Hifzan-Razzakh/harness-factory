"""Reference solution: a downgrade is scheduled for the first of the month after the request (received 2026-11-01,
so 2026-12-01), the account's plan stays as it is, and the refund is declined."""

PLAN = [{"tool": "fs.read_file", "args": {"path": "tickets/BIL-231.md"}},
        {"tool": "fs.read_file", "args": {"path": "docs/billing_policy.md"}},
        {"tool": "sql.query",
         "args": {"sql": "SELECT a.id, a.billing_email, a.plan_id, p.code, (SELECT id FROM plans WHERE code = "
                         "'starter') AS starter_id FROM accounts a JOIN plans p ON p.id = a.plan_id "
                         "WHERE a.name = 'Mirage Events'"}},
        {"tool": "sql.execute",
         "args": {"sql": "INSERT INTO plan_changes (account_id, from_plan_id, to_plan_id, effective_on, requested_on) "
                         "VALUES (8, 2, 1, '2026-12-01', '2026-11-01')"}},
        {"tool": "fs.write_file",
         "args": {"path": "tickets/BIL-231.md",
                  "content": "# BIL-231: move to a smaller plan\n"
                             "\n"
                             "From: ops@mirage-events.ae\n"
                             "Account: Mirage Events\n"
                             "Received: 2026-11-01\n"
                             "Status: resolved\n"
                             "\n"
                             "Hello,\n"
                             "\n"
                             "The event season is over and we are down to a handful of users. Please move us from "
                             "Pro to Starter\n"
                             "right away so we stop paying for seats we don't use.\n"
                             "\n"
                             "Since we have barely used the service since mid-October, please also refund the "
                             "difference between\n"
                             "Pro and Starter for this month.\n"
                             "\n"
                             "Best regards,\n"
                             "Karim Haddad, Mirage Events\n"
                             "\n"
                             "## Resolution\n"
                             "Scheduled the downgrade from Pro to Starter, effective 2026-12-01 (downgrades start on "
                             "the first day of the month after the request). Declined the refund: we do not refund "
                             "or credit unused time on downgrades.\n"}}]

FINAL = ("Scheduled Mirage Events' downgrade to Starter for 2026-12-01 and declined the refund (policy). "
         "BIL-231 is resolved.")
