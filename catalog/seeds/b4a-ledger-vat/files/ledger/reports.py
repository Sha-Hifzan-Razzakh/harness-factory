"""Revenue reports, in AED, net of VAT. Void invoices are left out."""

import datetime as dt
from collections import defaultdict
from decimal import Decimal

from ledger import customers, invoice
from ledger.money import D, round_money


def revenue_by_region(start: dt.date, end: dt.date) -> dict[str, Decimal]:
    """Net revenue in AED per customer region for invoices issued between start and end (inclusive)."""
    totals: dict[str, Decimal] = defaultdict(Decimal)
    for inv in invoice.load(start, end):
        if inv.counts:
            totals[customers.get(inv.customer_id).region] += inv.net_aed()
    return {region: round_money(v) for region, v in sorted(totals.items())}


def revenue_by_customer(start: dt.date, end: dt.date) -> dict[str, Decimal]:
    totals: dict[str, Decimal] = defaultdict(Decimal)
    for inv in invoice.load(start, end):
        if inv.counts:
            totals[inv.customer_id] += inv.net_aed()
    return {cid: round_money(v) for cid, v in sorted(totals.items())}


def top_customers(start: dt.date, end: dt.date, n: int = 5) -> list[tuple[str, Decimal]]:
    """The n customers with the most net revenue, largest first (ties by customer id)."""
    ranked = sorted(revenue_by_customer(start, end).items(), key=lambda kv: (-kv[1], kv[0]))
    return ranked[:n]


def monthly_revenue(year: int) -> dict[int, Decimal]:
    out = {}
    for month in range(1, 13):
        start = dt.date(year, month, 1)
        end = (dt.date(year + (month == 12), month % 12 + 1, 1) - dt.timedelta(days=1))
        out[month] = round_money(sum((i.net_aed() for i in invoice.load(start, end) if i.counts), Decimal(0)))
    return out


def net_of_vat(gross) -> Decimal:
    """The net part of a VAT-inclusive amount."""
    return round_money(D(gross) / Decimal("1.05"))
