"""Reference solution: read both tickets and the policy, then write the handover note."""

PLAN = [
    {"tool": "fs.read_file", "args": {"path": "tickets/T-117.md"}},
    {"tool": "fs.read_file", "args": {"path": "tickets/T-118.md"}},
    {"tool": "fs.read_file", "args": {"path": "docs/support_policy.md"}},
    {"tool": "fs.write_file", "args": {"path": "reports/handover.md", "content": (
        "# Support handover\n"
        "\n"
        "Open tickets: T-118 (Creek Analytics). T-117 (Harbor Logistics) is already resolved: the invoice copy "
        "was resent.\n"
        "\n"
        "## T-118: two requests\n"
        "1. Send invoices to billing@creekanalytics.ae instead of hello@creekanalytics.ae. The request came from "
        "the customer's registered address, so the policy allows it: update the email in the customer record.\n"
        "2. Delete order 2005. Orders are financial records, so the policy says this part must be declined, "
        "politely, with the reason.\n"
        "\n"
        "When it is handled, add a Resolution section to the ticket saying what was done and what was declined, "
        "and set its status to resolved.\n"
    )}},
]

FINAL = ("Wrote reports/handover.md: T-118 is open (the billing email change is allowed, the order deletion "
         "must be declined); T-117 is already resolved.")
