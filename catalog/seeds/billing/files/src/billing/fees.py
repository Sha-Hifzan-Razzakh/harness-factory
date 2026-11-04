"""Fees and discounts applied when an invoice is settled. Amounts are in AED."""

LATE_FEE_RATE = 0.02            # share of the open balance charged once an invoice is overdue
LATE_FEE_MIN = 25.00            # the late fee is never lower than this
EARLY_PAYMENT_DISCOUNT = 0.02   # share taken off when an invoice is paid within 5 days of issue


def late_fee(balance: float) -> float:
    """Late fee on an overdue balance, never below LATE_FEE_MIN."""
    return round(max(balance * LATE_FEE_RATE, LATE_FEE_MIN), 2)


def early_discount(amount: float) -> float:
    """Discount for paying within 5 days of issue."""
    return round(amount * EARLY_PAYMENT_DISCOUNT, 2)
