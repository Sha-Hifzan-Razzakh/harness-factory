# Seat policy

Every plan includes a number of seats (`plans.seats_included`). Accounts can buy extra seats as add-ons
(`seat_addons`); add-on seats are permanent once purchased.

## Overage

Seat usage is measured every Monday (`seat_usage`).

- An account's **allowance** is its plan's included seats plus every add-on seat it has bought.
- An account is **over its allowance** for a month when its peak measured seat count in that month (the
  highest measurement taken in the month) is greater than its allowance. Using exactly the allowance is fine.
- Only active accounts are billed for overage. Suspended and closed accounts are skipped, and internal
  accounts (`is_internal = 1`) are never billed.

Overage is billed on the next month's invoice at 45.00 per seat over the allowance.
