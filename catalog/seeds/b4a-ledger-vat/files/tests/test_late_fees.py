from decimal import Decimal

from ledger.late_fees import late_fee


def test_one_started_period():
    assert late_fee("1000", 1) == Decimal("50.00")
    assert late_fee("1000", 30) == Decimal("50.00")


def test_two_started_periods():
    assert late_fee("1000", 31) == Decimal("100.00")


def test_nothing_when_not_overdue():
    assert late_fee("1000", 0) == Decimal("0.00")
