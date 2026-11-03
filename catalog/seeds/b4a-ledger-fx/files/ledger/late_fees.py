"""Late payment fees: 5% of the overdue balance for every started 30 days past the due date."""

import math

from ledger.money import D, round_money

LATE_FEE_RATE = 0.05
PERIOD_DAYS = 30


def late_fee(balance, days_overdue: int):
    if days_overdue <= 0 or D(balance) <= 0:
        return round_money(0)
    periods = math.ceil(days_overdue / PERIOD_DAYS)
    return round_money(D(balance) * D(LATE_FEE_RATE) * periods)
