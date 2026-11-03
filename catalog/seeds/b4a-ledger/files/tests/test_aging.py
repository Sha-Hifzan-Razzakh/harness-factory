import datetime as dt
from decimal import Decimal

from ledger import aging


def test_buckets():
    assert [aging.bucket(n) for n in (-3, 0, 1, 30, 31, 60, 61, 90, 91)] == [
        "current", "current", "1-30", "1-30", "31-60", "31-60", "61-90", "61-90", "90+"]


def test_aed_aging_end_of_october():
    assert aging.aging(dt.date(2026, 10, 31))["AED"] == {
        "current": Decimal("787344.40"),
        "1-30": Decimal("1110824.63"),
        "31-60": Decimal("1010642.76"),
        "61-90": Decimal("828842.67"),
        "90+": Decimal("5211971.20"),
    }
