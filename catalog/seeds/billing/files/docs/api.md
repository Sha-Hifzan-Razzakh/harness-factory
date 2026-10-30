# Billing API

Base URL: `https://billing.internal/`. Every endpoint needs a bearer token with the `billing` scope.
Routes under `/internal` are used by our own jobs and are not documented here.

## Endpoints

| Method | Path | Description |
|---|---|---|
| GET | /v1/invoices | List invoices, newest first. |
| GET | /v1/invoices/{invoice_id} | One invoice with its lines. |
| POST | /v1/invoices/{invoice_id}/void | Void an open invoice. |
| POST | /v1/invoices/{invoice_id}/send | Email the invoice to the account's billing address. |
| POST | /v1/credit-notes | Issue a credit note against an invoice. |
| POST | /v1/accounts/{account_id}/plan | Change the account's plan. |
| GET | /v1/accounts/{account_id}/usage | Seat usage measurements for the account. |

## Errors

Errors return JSON with `code` and `message`. A 409 means the invoice is not in a state that allows the
action (for example voiding a paid invoice).
