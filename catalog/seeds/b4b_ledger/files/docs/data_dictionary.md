# Sales ledger: data dictionary

Database of Qamar Trading LLC's sales ledger (SQLite). One row per business event; nothing is ever physically
deleted, rows are soft-deleted instead.

| Table | What a row is | Notes |
|---|---|---|
| `customers` | a customer account | `account_code` QC-nnnnn; test accounts are TEST-nnn. `deleted_at` is set when the record was merged into another account (see `customer_merges`). |
| `customer_merges` | one merge of a duplicate record into another account | `source_customer_id` was merged into `target_customer_id` on `merged_on`. |
| `sales_reps` | an account manager | |
| `products` | a product we sell | `unit_cost_aed` is our cost per unit in AED; `active` = 0 for products we no longer sell. |
| `orders` | a customer order | `status`: completed, cancelled, pending. `deleted_at` is set on orders entered by mistake (e.g. twice); they are not orders. |
| `order_lines` | a line of an order | `unit_price` is in the order's currency. |
| `payments` | a payment received for an order | `status`: settled, failed, reversed. `paid_at` is Dubai time. `gateway_ref` is the card gateway's or bank's reference. `note` names the incident or ticket behind a manual change. |
| `refunds` | money returned to a customer | `status`: processed, rejected. Amount in the order's currency. |
| `fx_rates` | the AED rate of a currency on a day | one row per day for USD, EUR, GBP and SAR. There are no AED rows (1 AED = 1 AED). |
| `fx_monthly_avg` | monthly average rates | budgeting and forecasting only. |
| `shipments` | a dispatched order | warehouse and carrier. |

Data fixes are made by Finance Systems against an incident note in `incidents/`, and the changed rows name the
incident in their `note` column where the table has one.
