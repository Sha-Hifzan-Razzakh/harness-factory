from decimal import Decimal

from ledger import tax


def test_vat_is_five_percent():
    assert tax.vat("100") == Decimal("5.00")
    assert tax.vat("0.10") == Decimal("0.01")


def test_with_vat():
    assert tax.with_vat("40") == Decimal("42.00")
