"""Bulk discounts."""

BULK_MIN_ITEMS = 10      # orders with at least this many items get the bulk rate
BULK_RATE = 0.05


def bulk_discount(subtotal: float, items: int) -> float:
    """The discount on an order, in dirhams: BULK_RATE of the subtotal for orders of BULK_MIN_ITEMS items or
    more, otherwise nothing."""
    if items > BULK_MIN_ITEMS:
        return round(subtotal * BULK_RATE, 2)
    return 0.0
