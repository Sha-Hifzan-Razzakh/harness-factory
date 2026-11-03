# Revenue reporting rules (v4)

Owner: Finance (Group Reporting). Effective 1 April 2026; replaces v3 (archive/reporting_rules_v3.md).
Every revenue figure we report, internally or to the bank, follows these rules.

1. **Revenue** is the value of orders with status `completed` that were not deleted (`orders.deleted_at` is
   empty), dated in the period by `order_date`. An order's value is the sum of `quantity * unit_price` over its
   `order_lines`, in the order's currency.
2. **Currency.** Convert each order to AED at the `fx_rates` rate for its currency **on its order date**
   (`aed_per_unit`). AED orders need no conversion: `fx_rates` has no AED rows. `fx_monthly_avg` is for budgets
   and forecasts only and is never used for reported figures.
3. **Net revenue** is revenue minus refunds with status `processed` whose `refunded_on` falls in the period,
   whatever the date of the order they belong to. Convert each refund at the rate for its currency on its
   `refunded_on` date. Rejected refunds are ignored.
4. **Test accounts** (customers whose `account_code` starts with `TEST-`) are excluded, with their orders and
   refunds.
5. **Merged customers.** When duplicate customer records are merged (`customer_merges`), the merged-away record is
   soft-deleted and its history belongs to the surviving account. Report it under the surviving account: follow
   the merges from the source to the target until you reach an account that was not merged away (a merged
   account can itself be merged later). Orders of merged-away customers still count; only `orders.deleted_at`
   removes an order.
6. Round only the final figure, never per order or per line.
