# Billing support policy

How the billing team handles customer requests.

1. Plan changes are accepted only when the request comes from the account's billing email.
2. Upgrades take effect immediately: update the account's plan.
3. Downgrades take effect on the first day of the calendar month after the request was received. Record a
   downgrade as a row in `plan_changes` with that `effective_on` date and the request date as
   `requested_on`. Never change `accounts.plan_id` for a downgrade; the nightly billing job applies it on the
   effective date.
4. We do not refund or credit unused time when a customer downgrades or cancels. Decline that part of the
   request politely and explain why.
5. Invoices, payments and credit notes are financial records: never delete or edit them because a customer
   asks.
6. When a ticket is handled, add a "## Resolution" section at the end of the ticket that says what was done
   (including any effective date) and what was declined, then set its Status line to "resolved".
