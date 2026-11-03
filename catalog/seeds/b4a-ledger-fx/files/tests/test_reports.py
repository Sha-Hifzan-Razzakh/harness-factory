import datetime as dt
from decimal import Decimal

from ledger import reports


def test_q3_revenue_by_region():
    assert reports.revenue_by_region(dt.date(2026, 7, 1), dt.date(2026, 9, 30)) == {
        "Abu Dhabi": Decimal("4245046.95"),
        "Dubai": Decimal("8452136.62"),
        "Export": Decimal("3562780.07"),
        "Northern Emirates": Decimal("2273670.13"),
        "Sharjah": Decimal("2564676.16"),
    }


def test_top_customers_first_half():
    assert reports.top_customers(dt.date(2026, 1, 1), dt.date(2026, 6, 30), 3) == [
        ("C-1077", Decimal("1037201.01")),
        ("C-1025", Decimal("795510.86")),
        ("C-1127", Decimal("688770.06")),
    ]


def test_march_revenue():
    assert reports.monthly_revenue(2026)[3] == Decimal("6712220.58")


def test_net_of_vat():
    assert reports.net_of_vat("105.00") == Decimal("100.00")
    assert reports.net_of_vat("1") == Decimal("0.95")
