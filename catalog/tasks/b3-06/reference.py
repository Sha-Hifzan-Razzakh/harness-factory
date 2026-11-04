"""Reference solution: the send route is commented out (removed), the plan change is PUT now, the PDF route is new,
and the /internal route stays undocumented as the doc's header says."""

PLAN = [{"tool": "fs.read_file", "args": {"path": "src/billing_api/routes.py"}},
        {"tool": "fs.read_file", "args": {"path": "docs/api.md"}},
        {"tool": "fs.write_file",
         "args": {"path": "docs/api.md",
                  "content": "# Billing API\n"
                             "\n"
                             "Base URL: `https://billing.internal/`. Every endpoint needs a bearer token with the "
                             "`billing` scope.\n"
                             "Routes under `/internal` are used by our own jobs and are not documented here.\n"
                             "\n"
                             "## Endpoints\n"
                             "\n"
                             "| Method | Path | Description |\n"
                             "|---|---|---|\n"
                             "| GET | /v1/invoices | List invoices, newest first. |\n"
                             "| GET | /v1/invoices/{invoice_id} | One invoice with its lines. |\n"
                             "| POST | /v1/invoices/{invoice_id}/void | Void an open invoice. |\n"
                             "| GET | /v1/invoices/{invoice_id}/pdf | Download the invoice as a PDF. |\n"
                             "| POST | /v1/credit-notes | Issue a credit note against an invoice. |\n"
                             "| PUT | /v1/accounts/{account_id}/plan | Change the account's plan. |\n"
                             "| GET | /v1/accounts/{account_id}/usage | Seat usage measurements for the account. |\n"
                             "\n"
                             "## Errors\n"
                             "\n"
                             "Errors return JSON with `code` and `message`. A 409 means the invoice is not in a "
                             "state that allows the\n"
                             "action (for example voiding a paid invoice).\n"}}]

FINAL = "docs/api.md: added GET /v1/invoices/{invoice_id}/pdf, plan change is PUT, removed the send endpoint."
