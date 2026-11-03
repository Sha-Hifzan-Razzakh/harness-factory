# Early-settlement discounts

Owner: Treasury. Effective 1 January 2026.

Some customers have agreed early-settlement terms, kept in the treasury database (`settlement_terms`). Terms let
the customer deduct `discount_pct` percent of an invoice's total when it pays within `discount_days` days of the
invoice date (`issued_on`). Terms cover invoices issued from `valid_from` to `valid_to` (inclusive).

## Reconciling a bank statement

A statement line that pays an open invoice less than its balance due is a **short payment**; the shortfall is the
balance due minus the amount paid. For each short payment:

1. When the customer's terms cover the invoice, the payment date is at most `discount_days` days after the
   invoice date, and the shortfall equals the discount (the invoice total times `discount_pct` / 100, rounded to
   2 decimals): issue a credit note on the invoice for the shortfall, reason "Early settlement discount"
   followed by the percentage and the statement date. Finance then records the payment against the remaining
   balance.
2. Otherwise issue nothing. List the line in `reports/short_payments_<YYYY-MM>.csv` for the collections team,
   with the header `date,reference,amount,shortfall`: the statement's date, reference and amount as the
   statement shows them, and the shortfall with 2 decimals, in statement order.

Lines that pay an invoice in full, overpay, or are not invoice payments are not short payments. Payments are
recorded by Finance; never mark invoices as paid.
