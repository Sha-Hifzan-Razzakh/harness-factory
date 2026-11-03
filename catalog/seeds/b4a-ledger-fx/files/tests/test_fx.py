import datetime as dt
from decimal import Decimal

import pytest

from ledger import fx


def test_aed_is_one():
    assert fx.rate_on("AED", dt.date(2026, 3, 4)) == Decimal("1")


def test_midweek_days_use_that_weeks_rate():
    assert fx.rate_on("USD", dt.date(2026, 3, 4)) == Decimal("3.6856")
    assert fx.rate_on("EUR", dt.date(2026, 6, 18)) == Decimal("4.0284")


def test_sunday_uses_the_rate_of_the_monday_before():
    assert fx.rate_on("GBP", dt.date(2026, 8, 30)) == Decimal("4.4531")


def test_convert_rounds_to_the_fils():
    assert fx.convert(Decimal("1250.40"), "USD", dt.date(2026, 3, 4)) == Decimal("4608.47")


def test_unknown_currency():
    with pytest.raises(KeyError):
        fx.rate_on("JPY", dt.date(2026, 3, 4))


def test_no_rate_before_the_first_publication():
    with pytest.raises(LookupError):
        fx.rate_on("USD", dt.date(2025, 12, 1))
