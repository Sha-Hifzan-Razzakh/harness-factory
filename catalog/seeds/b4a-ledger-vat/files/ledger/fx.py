"""Exchange rates to AED.

Treasury publishes one rate per currency every Monday (ledger/data/fx_rates.csv). A rate applies from its
publication date, inclusive, until the next publication. AED is always 1.
"""

import bisect
import csv
import datetime as dt
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

from ledger.money import D, round_money

RATES = Path(__file__).parent / "data" / "fx_rates.csv"


@lru_cache(maxsize=1)
def _table() -> dict[str, list[tuple[dt.date, Decimal]]]:
    by_ccy: dict[str, list[tuple[dt.date, Decimal]]] = {}
    with RATES.open(newline="") as f:
        for row in csv.DictReader(f):
            by_ccy.setdefault(row["currency"], []).append(
                (dt.date.fromisoformat(row["published_on"]), D(row["aed_per_unit"])))
    for rows in by_ccy.values():
        rows.sort()
    return by_ccy


def currencies() -> list[str]:
    return ["AED", *sorted(_table())]


def rate_on(currency: str, day: dt.date) -> Decimal:
    """AED per unit of currency on a day: the latest rate published on or before that day."""
    if currency == "AED":
        return Decimal(1)
    table = _table().get(currency)
    if not table:
        raise KeyError(f"no rates for {currency}")
    days = [d for d, _ in table]
    i = bisect.bisect_right(days, day)
    if i == 0:
        raise LookupError(f"no {currency} rate published on or before {day}")
    return table[i - 1][1]


def convert(amount, currency: str, day: dt.date) -> Decimal:
    """An amount in currency, in AED at the rate of the day, rounded to the fils."""
    return round_money(D(amount) * rate_on(currency, day))
