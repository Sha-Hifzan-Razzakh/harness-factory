from decimal import Decimal

from ledger.money import D, fmt, round_money


def test_round_half_away_from_zero():
    assert round_money("2.345") == Decimal("2.35")
    assert round_money("-2.345") == Decimal("-2.35")


def test_floats_go_through_str():
    assert D(0.1) == Decimal("0.1")


def test_fmt():
    assert fmt(1234.5) == "1,234.50"
