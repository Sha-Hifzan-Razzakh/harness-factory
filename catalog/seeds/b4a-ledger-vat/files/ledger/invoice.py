"""Invoices (ledger/data/invoices.csv). Amounts are in the invoice currency; *_aed in dirhams."""

import csv
import datetime as dt
from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

from ledger import fx
from ledger.money import D, round_money

DATA = Path(__file__).parent / "data" / "invoices.csv"
STATUSES = ("issued", "paid", "void")


@dataclass(frozen=True)
class Invoice:
    number: str
    customer_id: str
    issued_on: dt.date
    currency: str
    net: Decimal
    status: str = "issued"

    @property
    def vat(self) -> Decimal:
        """VAT on the net amount, in the invoice currency."""
        return round_money(self.net * Decimal("0.05"))

    @property
    def gross(self) -> Decimal:
        return self.net + self.vat

    @property
    def counts(self) -> bool:
        """Void invoices never count towards revenue or balances."""
        return self.status != "void"

    def net_aed(self) -> Decimal:
        return fx.convert(self.net, self.currency, self.issued_on)

    def gross_aed(self) -> Decimal:
        return fx.convert(self.gross, self.currency, self.issued_on)


def parse(row: dict) -> Invoice:
    if row["status"] not in STATUSES:
        raise ValueError(f"{row['number']}: unknown status {row['status']!r}")
    return Invoice(row["number"], row["customer_id"], dt.date.fromisoformat(row["issued_on"]), row["currency"],
                   D(row["net"]), row["status"])


@lru_cache(maxsize=1)
def _all() -> tuple[Invoice, ...]:
    with DATA.open(newline="") as f:
        return tuple(parse(r) for r in csv.DictReader(f))


def load(start: dt.date | None = None, end: dt.date | None = None) -> list[Invoice]:
    """Invoices issued between start and end, both inclusive."""
    return [i for i in _all() if (start is None or i.issued_on >= start) and (end is None or i.issued_on <= end)]
