"""HTTP routes of the billing API. docs/api.md documents the public ones."""

from billing_api import handlers as h
from billing_api.app import router

router.add("GET", "/v1/invoices", h.list_invoices)
router.add("GET", "/v1/invoices/{invoice_id}", h.get_invoice)
router.add("POST", "/v1/invoices/{invoice_id}/void", h.void_invoice)
router.add("GET", "/v1/invoices/{invoice_id}/pdf", h.invoice_pdf)
router.add("POST", "/v1/credit-notes", h.create_credit_note)
router.add("PUT", "/v1/accounts/{account_id}/plan", h.change_plan)
router.add("GET", "/v1/accounts/{account_id}/usage", h.seat_usage)

# Removed in 3.2: invoices are emailed by the notifications service now, not by this API.
# router.add("POST", "/v1/invoices/{invoice_id}/send", h.send_invoice)

# Used by the nightly billing job to apply scheduled plan changes.
router.add("POST", "/internal/plan-changes/apply", h.apply_plan_changes)
