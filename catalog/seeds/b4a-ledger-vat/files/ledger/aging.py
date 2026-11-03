"""Accounts receivable aging, per currency, as of a date."""

import datetime as dt
from collections import defaultdict
from decimal import Decimal

from ledger import config, invoice
from ledger.money import round_money

BUCKETS = ("current", "1-30", "31-60", "61-90", "90+")


def bucket(days_past_due: int) -> str:
    if days_past_due <= 0:
        return "current"
    if days_past_due <= 30:
        return "1-30"
    if days_past_due <= 60:
        return "31-60"
    if days_past_due <= 90:
        return "61-90"
    return "90+"


def aging(as_of: dt.date) -> dict[str, dict[str, Decimal]]:
    """Open (issued, unpaid) gross balances per currency and bucket, for invoices issued on or before as_of."""
    out: dict[str, dict[str, Decimal]] = defaultdict(lambda: {b: Decimal(0) for b in BUCKETS})
    for inv in invoice.load(None, as_of):
        if inv.status != "issued":
            continue
        due = inv.issued_on + dt.timedelta(days=config.PAYMENT_TERMS_DAYS)
        out[inv.currency][bucket((as_of - due).days)] += inv.gross
    return {ccy: {b: round_money(v) for b, v in row.items()} for ccy, row in sorted(out.items())}
