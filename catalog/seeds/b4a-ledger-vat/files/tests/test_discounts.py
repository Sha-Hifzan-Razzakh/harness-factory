from decimal import Decimal

from ledger.discounts import bulk_discount


def test_bulk_orders_get_five_percent():
    assert bulk_discount("200", 10) == Decimal("10.00")
    assert bulk_discount("200", 9) == Decimal("0.00")
