"""Batch 4 (author A): the `ledger` repository seeds for b4-01, b4-02 and b4-03.

A Python package for a distributor's accounts receivable (invoices, VAT, FX conversion, reports) with ~2,600
invoices, a year of weekly FX rates and ~400 merged pull requests. Three variants share most bytes:

- b4a-ledger-fx:  fx.rate_on picks the previous week's rate on a publication day (bisect_left); the symptom is
                  tests/test_reports.py, two modules away (reports -> invoice -> fx).
- b4a-ledger-vat: the VAT rate is hard-coded in five modules, next to look-alike 5% rates that are not VAT.
- b4a-ledger:     the clean package at 3.1.0, with docs/merged_prs.csv and docs/releases.csv for a release.

Everything is deterministic (fixed dates, random.Random with fixed seeds). Expected test values are computed by
running the correct package in a child process, never typed by hand.
"""

import csv
import datetime as dt
import io
import json
import os
import random
import subprocess
import sys
import tempfile
from pathlib import Path

D0 = dt.date(2026, 1, 1)
CURRENCIES = {"USD": 3.6725, "EUR": 3.98, "GBP": 4.66, "SAR": 0.9793}
REGIONS = ("Dubai", "Abu Dhabi", "Sharjah", "Northern Emirates", "Export")
VERSION = "3.1.0"
NEXT_VERSION = "3.2.0"
RELEASE_DATE = "2026-11-03"


# ------------------------------------------------------------------ data

def fx_rows() -> list[tuple[str, str, str]]:
    """Weekly rates, published every Monday from 2025-12-29 to 2026-10-26."""
    rng = random.Random(4101)
    out, day = [], dt.date(2025, 12, 29)
    level = dict(CURRENCIES)
    while day <= dt.date(2026, 10, 26):
        for ccy in sorted(CURRENCIES):
            if ccy not in ("SAR",):
                level[ccy] = round(level[ccy] * (1 + rng.uniform(-0.012, 0.012)), 4)
            else:
                level[ccy] = round(0.9793 + rng.choice((-0.0002, 0.0, 0.0001)), 4)
            out.append((day.isoformat(), ccy, f"{level[ccy]:.4f}"))
        day += dt.timedelta(days=7)
    return out


COMPANY_WORDS = ["Al Noor", "Harbor", "Falcon", "Oasis", "Desert Rose", "Marina", "Palm", "Gulf", "Sandstone",
                 "Creek", "Coral", "Zenith", "Dune", "Pearl", "Saffron", "Cedar", "Atlas", "Lotus", "Summit",
                 "Meridian", "Orbit", "Sahara", "Jumeirah", "Corniche", "Horizon", "Mirage", "Azure", "Emerald"]
COMPANY_KINDS = ["Trading", "Logistics", "Foods", "Systems", "Pharmacy", "Contracting", "Retail", "Electronics",
                 "Hospitality", "Packaging", "Auto Parts", "Marine Supplies"]


def customers() -> list[tuple[str, str, str, int]]:
    rng = random.Random(4102)
    names, out = set(), []
    for i in range(180):
        while True:
            name = f"{rng.choice(COMPANY_WORDS)} {rng.choice(COMPANY_KINDS)} LLC"
            if name not in names:
                names.add(name)
                break
        region = rng.choices(REGIONS, weights=(40, 22, 16, 10, 12))[0]
        out.append((f"C-{1001 + i}", name, region, rng.choice((25_000, 50_000, 100_000, 250_000))))
    return out


def invoices(custs) -> list[tuple]:
    """(number, customer_id, issued_on, currency, net, status). Export customers mostly bill in foreign currency."""
    rng = random.Random(4103)
    out, n = [], 26000
    day = D0
    while day <= dt.date(2026, 10, 31):
        if day.weekday() < 5 or rng.random() < 0.15:
            for _ in range(rng.randint(5, 12)):
                cid, _, region, _ = rng.choice(custs)
                if region == "Export":
                    ccy = rng.choice(("USD", "USD", "EUR", "GBP", "SAR"))
                else:
                    ccy = rng.choices(("AED", "USD", "EUR", "SAR"), weights=(80, 10, 5, 5))[0]
                net = f"{rng.randint(150, 48000) + rng.choice((0, 0.25, 0.5, 0.75, 0.4, 0.9)):.2f}"
                status = rng.choices(("issued", "paid", "void"), weights=(25, 70, 5))[0]
                n += 1
                out.append((f"INV-{n}", cid, day.isoformat(), ccy, net, status))
        day += dt.timedelta(days=1)
    return out


def table_csv(header, rows) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(header)
    w.writerows(rows)
    return buf.getvalue()


# ------------------------------------------------------------------ pull requests and releases (b4-03)

RELEASES = [("3.0.0", "2026-05-11T10:00:00+04:00"), ("3.0.1", "2026-06-22T16:20:00+04:00"),
            ("3.0.2", "2026-08-04T09:45:00+04:00"), ("3.1.0", "2026-09-15T11:30:00+04:00")]
LABELS = ("feature", "bug", "chore", "docs", "ci", "deps")
AREAS = ["the aging report", "customer statements", "the revenue report", "the CSV export", "the JSON export",
         "receipts", "the invoice loader", "late fees", "the FX lookup", "credit notes", "the customer search",
         "the top customers report", "monthly revenue", "VAT summaries", "the invoice importer", "payment matching"]
FEATURES = ["a region filter to", "a currency column to", "paging to", "a date range to", "sorting to",
            "a totals row to", "an audit trail to", "customer groups to", "a dry-run mode to", "PO numbers to"]
BUGS = ["rounding in", "an off-by-one date in", "a crash on empty input in", "duplicate rows in",
        "case-sensitive matching in", "the sort order of", "void invoices counted in", "a timezone error in",
        "a missing header in", "negative balances in"]
CHORE_TITLES = {"chore": ["Refactor {}", "Tidy imports in {}", "Rename helpers in {}", "Remove dead code in {}"],
                "docs": ["Document {}", "Update the README section on {}", "Add examples for {}"],
                "ci": ["Cache dependencies in CI", "Run tests on Python 3.13", "Split the lint job",
                       "Pin the CI runner image"],
                "deps": ["Bump python-dateutil", "Bump pytest", "Bump ruff", "Bump ledger-schemas test fixtures"]}

# The pull requests after the 3.1.0 cut, hand-placed so the traps are certain (number, title, label, base, merged_at)
AFTER_CUT = [
    (3861, "Add statement period to customer statements", "feature", "main", "2026-09-15T14:05:00+04:00"),
    (3862, "Bump pytest to 8.4.2", "deps", "main", "2026-09-16T09:12:00+04:00"),
    (3864, "Fix VAT rounding on partially credited invoices", "bug", "release/3.1.x", "2026-09-17T10:40:00+04:00"),
    (3865, "Fix VAT rounding on partially credited invoices", "bug", "main", "2026-09-17T11:02:00+04:00"),
    (3867, "Add EUR to the revenue report currency filter", "feature", "main", "2026-09-22T15:30:00+04:00"),
    (3868, "Document the release checklist", "docs", "main", "2026-09-23T08:55:00+04:00"),
    (3870, "Fix aging report for invoices due today", "bug", "main", "2026-09-29T13:10:00+04:00"),
    (3871, "Add credit-note PDF export", "feature", "main", "2026-10-01T12:00:00+04:00"),
    (3873, "Run CI on pull requests from forks", "ci", "main", "2026-10-02T10:20:00+04:00"),
    (3874, "Fix late fee on invoices paid on the due date", "bug", "release/3.1.x", "2026-10-05T09:00:00+04:00"),
    (3876, "Add customer credit limit report", "feature", "main", "2026-10-07T16:45:00+04:00"),
    (3877, "Tidy fixtures in report tests", "chore", "main", "2026-10-08T11:15:00+04:00"),
    (3879, "Fix receipt line wrapping for long item names", "bug", "main", "2026-10-12T14:00:00+04:00"),
    (3881, "Add invoice search by PO number", "feature", "main", "2026-10-14T10:30:00+04:00"),
    (3883, "Bump python-dateutil to 2.9.0.post0", "deps", "main", "2026-10-15T09:05:00+04:00"),
    (3885, "Fix export of invoices with no customer region", "bug", "main", "2026-10-19T15:50:00+04:00"),
    (3886, "Fix FX lookup for SAR before the first publication", "bug", "release/3.1.x",
     "2026-10-20T10:10:00+04:00"),
    (3889, 'Revert "Add credit-note PDF export" (#3871)', "revert", "main", "2026-10-21T17:25:00+04:00"),
    (3890, "Add aging buckets to customer statements", "feature", "main", "2026-10-26T12:40:00+04:00"),
    (3892, "Explain FX rounding in the README", "docs", "main", "2026-10-27T09:30:00+04:00"),
    (3893, "Fix top customers report ignoring void invoices", "bug", "main", "2026-10-29T14:20:00+04:00"),
]
# merged into main on the cut day, before the cut: already in 3.1.0
BEFORE_CUT_SAME_DAY = [
    (3857, "Add quarterly VAT summary export", "feature", "main", "2026-09-15T09:40:00+04:00"),
    (3859, "Fix duplicate invoice numbers in imports", "bug", "main", "2026-09-15T10:55:00+04:00"),
]


def pull_requests() -> list[tuple]:
    """~300 merged PRs from January to the end of October (number, title, label, base, merged_at, author); the
    ones from the day of the 3.1.0 cut on are hand-placed (BEFORE_CUT_SAME_DAY, AFTER_CUT)."""
    rng = random.Random(4104)
    numbers, n = [], 3440
    while True:
        n += rng.choice((1, 1, 2))
        if n >= 3856:
            break
        numbers.append(n)
    start, span = dt.datetime(2026, 1, 5, 9, 0), dt.datetime(2026, 9, 14, 18, 0) - dt.datetime(2026, 1, 5, 9, 0)
    times = sorted(start + span * rng.random() for _ in numbers)
    cut300 = dt.datetime.fromisoformat(RELEASES[0][1]).replace(tzinfo=None)
    authors = ["hadi.k", "mariam.s", "omar.f", "lina.r", "yusuf.a", "sara.m"]
    titles, out = set(), []
    for num, t in zip(numbers, times, strict=True):
        label = rng.choices(LABELS, weights=(22, 22, 18, 12, 10, 16))[0]
        while True:
            area = rng.choice(AREAS)
            if label == "feature":
                title = f"Add {rng.choice(FEATURES)} {area}"
            elif label == "bug":
                title = f"Fix {rng.choice(BUGS)} {area}"
            else:
                title = rng.choice(CHORE_TITLES[label]).format(area)
            if title not in titles or label in ("ci", "deps"):
                titles.add(title)
                break
        base = "main"
        if label == "bug" and rng.random() < 0.3:
            base = "release/3.0.x" if t > cut300 else "release/2.9.x"
        author = "dev-bot" if label == "deps" else rng.choice(authors)
        out.append((num, title, label, base, t.strftime("%Y-%m-%dT%H:%M:00+04:00"), author))
    rng2 = random.Random(4105)
    for num, title, label, base, at in BEFORE_CUT_SAME_DAY + AFTER_CUT:
        author = "dev-bot" if label == "deps" else rng2.choice(authors)
        out.append((num, title, label, base, at, author))
    return sorted(out)


def release_notes() -> list[tuple[str, str, list[str]]]:
    """(group, label, lines) of the 3.2.0 changelog: PRs merged into main after the cut, feature/bug only, minus
    reverted pull requests and their reverts."""
    cut = dt.datetime.fromisoformat(RELEASES[-1][1])
    prs = [p for p in pull_requests() if dt.datetime.fromisoformat(p[4]) > cut and p[3] == "main"]
    reverted = {int(p[1].rsplit("(#", 1)[1].rstrip(")")) for p in prs if p[2] == "revert"}
    keep = [p for p in prs if p[0] not in reverted]
    return [(group, label, [f"- {p[1]} (#{p[0]})" for p in sorted(keep) if p[2] == label])
            for group, label in (("Added", "feature"), ("Fixed", "bug"))]


def changelog() -> str:
    """CHANGELOG.md up to 3.1.0. A minor release lists the main PRs merged since the previous minor cut, a patch
    release the PRs merged into its release branch since the previous cut."""
    prs = pull_requests()
    at = {v: dt.datetime.fromisoformat(t) for v, t in RELEASES}
    windows = [("3.0.0", None, at["3.0.0"], ("main",)),
               ("3.0.1", at["3.0.0"], at["3.0.1"], ("release/3.0.x",)),
               ("3.0.2", at["3.0.1"], at["3.0.2"], ("release/3.0.x",)),
               ("3.1.0", at["3.0.0"], at["3.1.0"], ("main",))]
    sections = []
    for version, lo, hi, bases in reversed(windows):
        sel = [p for p in prs if p[3] in bases and p[2] in ("feature", "bug")
               and (lo is None or dt.datetime.fromisoformat(p[4]) > lo) and dt.datetime.fromisoformat(p[4]) <= hi]
        lines = [f"## [{version}] - {hi.date().isoformat()}"]
        for group, label in (("Added", "feature"), ("Fixed", "bug")):
            items = [f"- {p[1]} (#{p[0]})" for p in sel if p[2] == label]
            if items:
                lines += [f"### {group}", *items]
        sections.append("\n".join(lines))
    head = "# Changelog\n\nAll notable changes to the ledger package, newest release first.\n\n"
    return head + "\n\n".join(sections) + "\n"


def changelog_after_release() -> str:
    """CHANGELOG.md as the b4-03 reference writes it."""
    body = changelog()
    lines = [f"## [{NEXT_VERSION}] - {RELEASE_DATE}"]
    for group, _, items in release_notes():
        lines += [f"### {group}", *items]
    marker = f"## [{VERSION}]"
    return body.replace(marker, "\n".join(lines) + "\n\n" + marker, 1)


# ------------------------------------------------------------------ package source

def src_init(version=VERSION) -> str:
    return ('"""Accounts receivable for Gulf Distribution Co.: invoices, VAT, FX conversion and reports."""\n\n'
            f'__version__ = "{version}"\n')


CONFIG = '''"""Settings shared by the ledger modules."""

BASE_CURRENCY = "AED"
VAT_RATE = 0.05                 # UAE standard rate
PAYMENT_TERMS_DAYS = 30
REGIONS = ("Dubai", "Abu Dhabi", "Sharjah", "Northern Emirates", "Export")
'''

MONEY = '''"""Money helpers: amounts are Decimals in a currency's main unit, rounded to 2 places (fils, cents)."""

from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")


def D(x) -> Decimal:
    """A Decimal from an int, a str or a float (floats go through str, so 0.1 stays 0.1)."""
    return x if isinstance(x, Decimal) else Decimal(str(x))


def round_money(x) -> Decimal:
    """Round to 2 places, halves away from zero."""
    return D(x).quantize(CENT, rounding=ROUND_HALF_UP)


def fmt(x) -> str:
    """1234.5 -> '1,234.50'."""
    return f"{round_money(x):,.2f}"
'''


def src_fx(bug: bool) -> str:
    lookup = "bisect.bisect_left(days, day)" if bug else "bisect.bisect_right(days, day)"
    return f'''"""Exchange rates to AED.

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
    by_ccy: dict[str, list[tuple[dt.date, Decimal]]] = {{}}
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
        raise KeyError(f"no rates for {{currency}}")
    days = [d for d, _ in table]
    i = {lookup}
    if i == 0:
        raise LookupError(f"no {{currency}} rate published on or before {{day}}")
    return table[i - 1][1]


def convert(amount, currency: str, day: dt.date) -> Decimal:
    """An amount in currency, in AED at the rate of the day, rounded to the fils."""
    return round_money(D(amount) * rate_on(currency, day))
'''


CUSTOMERS = '''"""Customers (ledger/data/customers.csv)."""

import csv
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

DATA = Path(__file__).parent / "data" / "customers.csv"


@dataclass(frozen=True)
class Customer:
    id: str
    name: str
    region: str
    credit_limit: int


@lru_cache(maxsize=1)
def all_customers() -> dict[str, Customer]:
    with DATA.open(newline="") as f:
        return {r["id"]: Customer(r["id"], r["name"], r["region"], int(r["credit_limit"])) for r in csv.DictReader(f)}


def get(customer_id: str) -> Customer:
    try:
        return all_customers()[customer_id]
    except KeyError:
        raise KeyError(f"unknown customer {customer_id}") from None


def search(text: str) -> list[Customer]:
    text = text.lower()
    return [c for c in all_customers().values() if text in c.name.lower()]
'''


def src_invoice(vat_literal: bool) -> str:
    vat_expr = 'round_money(self.net * Decimal("0.05"))' if vat_literal else "round_money(self.net * D(config.VAT_RATE))"
    imports = "from ledger import fx" if vat_literal else "from ledger import config, fx"
    return f'''"""Invoices (ledger/data/invoices.csv). Amounts are in the invoice currency; *_aed in dirhams."""

import csv
import datetime as dt
from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

{imports}
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
        return {vat_expr}

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
        raise ValueError(f"{{row['number']}}: unknown status {{row['status']!r}}")
    return Invoice(row["number"], row["customer_id"], dt.date.fromisoformat(row["issued_on"]), row["currency"],
                   D(row["net"]), row["status"])


@lru_cache(maxsize=1)
def _all() -> tuple[Invoice, ...]:
    with DATA.open(newline="") as f:
        return tuple(parse(r) for r in csv.DictReader(f))


def load(start: dt.date | None = None, end: dt.date | None = None) -> list[Invoice]:
    """Invoices issued between start and end, both inclusive."""
    return [i for i in _all() if (start is None or i.issued_on >= start) and (end is None or i.issued_on <= end)]
'''


def src_tax(vat_literal: bool) -> str:
    if vat_literal:
        head = 'from decimal import Decimal\n\nfrom ledger.money import D, round_money\n\nVAT_RATE = Decimal("0.05")\n'
        rate = "VAT_RATE"
    else:
        head = "from ledger import config\nfrom ledger.money import D, round_money\n"
        rate = "D(config.VAT_RATE)"
    return f'''"""VAT on amounts that exclude it."""

{head}

def vat(net) -> "Decimal":
    """VAT due on a net amount, rounded to the fils."""
    return round_money(D(net) * {rate})


def with_vat(net) -> "Decimal":
    return round_money(D(net)) + vat(net)
'''


DISCOUNTS = '''"""Bulk discounts. These are commercial terms, unrelated to VAT."""

from ledger.money import D, round_money

BULK_MIN_ITEMS = 10              # orders with at least this many items get the bulk rate
BULK_RATE = 0.05


def bulk_discount(subtotal, items: int):
    """The discount on an order: BULK_RATE of the subtotal for BULK_MIN_ITEMS items or more, otherwise 0."""
    if items >= BULK_MIN_ITEMS:
        return round_money(D(subtotal) * D(BULK_RATE))
    return round_money(0)
'''

LATE_FEES = '''"""Late payment fees: 5% of the overdue balance for every started 30 days past the due date."""

import math

from ledger.money import D, round_money

LATE_FEE_RATE = 0.05
PERIOD_DAYS = 30


def late_fee(balance, days_overdue: int):
    if days_overdue <= 0 or D(balance) <= 0:
        return round_money(0)
    periods = math.ceil(days_overdue / PERIOD_DAYS)
    return round_money(D(balance) * D(LATE_FEE_RATE) * periods)
'''


def src_reports(vat_literal: bool) -> str:
    net_of_vat = ('round_money(D(gross) / Decimal("1.05"))' if vat_literal
                  else "round_money(D(gross) / (1 + D(config.VAT_RATE)))")
    imports = "from ledger import customers, invoice" if vat_literal else "from ledger import config, customers, invoice"
    return f'''"""Revenue reports, in AED, net of VAT. Void invoices are left out."""

import datetime as dt
from collections import defaultdict
from decimal import Decimal

{imports}
from ledger.money import D, round_money


def revenue_by_region(start: dt.date, end: dt.date) -> dict[str, Decimal]:
    """Net revenue in AED per customer region for invoices issued between start and end (inclusive)."""
    totals: dict[str, Decimal] = defaultdict(Decimal)
    for inv in invoice.load(start, end):
        if inv.counts:
            totals[customers.get(inv.customer_id).region] += inv.net_aed()
    return {{region: round_money(v) for region, v in sorted(totals.items())}}


def revenue_by_customer(start: dt.date, end: dt.date) -> dict[str, Decimal]:
    totals: dict[str, Decimal] = defaultdict(Decimal)
    for inv in invoice.load(start, end):
        if inv.counts:
            totals[inv.customer_id] += inv.net_aed()
    return {{cid: round_money(v) for cid, v in sorted(totals.items())}}


def top_customers(start: dt.date, end: dt.date, n: int = 5) -> list[tuple[str, Decimal]]:
    """The n customers with the most net revenue, largest first (ties by customer id)."""
    ranked = sorted(revenue_by_customer(start, end).items(), key=lambda kv: (-kv[1], kv[0]))
    return ranked[:n]


def monthly_revenue(year: int) -> dict[int, Decimal]:
    out = {{}}
    for month in range(1, 13):
        start = dt.date(year, month, 1)
        end = (dt.date(year + (month == 12), month % 12 + 1, 1) - dt.timedelta(days=1))
        out[month] = round_money(sum((i.net_aed() for i in invoice.load(start, end) if i.counts), Decimal(0)))
    return out


def net_of_vat(gross) -> Decimal:
    """The net part of a VAT-inclusive amount."""
    return {net_of_vat}
'''


AGING = '''"""Accounts receivable aging, per currency, as of a date."""

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
'''


def src_receipt(vat_literal: bool) -> str:
    if vat_literal:
        consts = "VAT_PERCENT = 5\n"
        part = "total * VAT_PERCENT / (100 + VAT_PERCENT)"
        label = 'f"VAT {VAT_PERCENT}% included"'
        imports = "from ledger.money import D, fmt, round_money"
    else:
        consts = ""
        part = "total * D(config.VAT_RATE) / (1 + D(config.VAT_RATE))"
        label = 'f"VAT {(D(config.VAT_RATE) * 100).normalize():f}% included"'
        imports = "from ledger import config\nfrom ledger.money import D, fmt, round_money"
    return f'''"""Point-of-sale receipts. Shelf prices include VAT; the receipt shows the VAT part of the total."""

{imports}

{consts}WIDTH = 40


def receipt_lines(items: list[tuple[str, int, object]]) -> list[str]:
    """items: (name, quantity, unit price including VAT)."""
    lines, total = [], D(0)
    for name, qty, price in items:
        amount = round_money(D(price) * qty)
        total += amount
        left = f"{{qty}} x {{name}}"[: WIDTH - 12]
        lines.append(f"{{left:<{{WIDTH - 12}}}}{{fmt(amount):>12}}")
    vat_part = round_money({part})
    lines.append("-" * WIDTH)
    lines.append(f"{{'Total':<{{WIDTH - 12}}}}{{fmt(total):>12}}")
    lines.append(f"{{{label}:<{{WIDTH - 12}}}}{{fmt(vat_part):>12}}")
    return lines
'''


CSV_EXPORT = '''"""Invoice export as CSV."""

import csv
import io

from ledger.invoice import Invoice


def export_csv(invoices: list[Invoice]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\\n")
    w.writerow(["number", "customer_id", "issued_on", "currency", "net", "vat", "gross", "status"])
    for inv in invoices:
        w.writerow([inv.number, inv.customer_id, inv.issued_on.isoformat(), inv.currency, f"{inv.net:.2f}",
                    f"{inv.vat:.2f}", f"{inv.gross:.2f}", inv.status])
    return buf.getvalue()
'''


def src_json_export(vat_literal: bool) -> str:
    rate = "0.05" if vat_literal else "config.VAT_RATE"
    imports = "" if vat_literal else "from ledger import config\n"
    return f'''"""Invoice export as JSON, for the customer portal."""

{imports}from ledger.invoice import Invoice


def export_invoice(inv: Invoice) -> dict:
    return {{"number": inv.number, "customer_id": inv.customer_id, "issued_on": inv.issued_on.isoformat(),
            "currency": inv.currency, "net": f"{{inv.net:.2f}}", "vat": f"{{inv.vat:.2f}}",
            "gross": f"{{inv.gross:.2f}}", "vat_rate": {rate}, "status": inv.status}}
'''


FX_OLD = '''"""Deprecated: the 2024 daily-rate lookup, kept only for the 2024 audit scripts. Nothing imports it."""

import datetime as dt

DAILY_RATES = {"USD": 3.6725, "EUR": 4.02, "GBP": 4.61}


def rate_for(currency: str, day: dt.date) -> float:
    if currency == "AED":
        return 1.0
    return DAILY_RATES[currency]


def to_aed(amount: float, currency: str, day: dt.date) -> float:
    return round(amount * rate_for(currency, day), 2)
'''

TAX_V1 = '''"""Deprecated: VAT before the ledger package (2024). Kept for the audit scripts; nothing imports it."""

VAT = 0.05


def vat(net: float) -> float:
    return round(net * VAT, 2)
'''

README = '''# ledger

Accounts receivable for Gulf Distribution Co.: invoices, VAT, FX conversion to AED, aging and revenue reports.

- `ledger/invoice.py`: invoices from `ledger/data/invoices.csv`
- `ledger/fx.py`: weekly treasury rates from `ledger/data/fx_rates.csv`
- `ledger/reports.py`: revenue by region, customer and month (AED, net of VAT)
- `ledger/aging.py`: open balances by currency and bucket
- `ledger/pos/receipt.py`: point-of-sale receipts
- `ledger/exporters/`: CSV and JSON export
- `ledger/legacy/`: 2024 code kept for the audit scripts; not used by the package

Run the tests with `python -m pytest tests`. Releases are cut from `main` on a `release/<version>` branch; see
`docs/releases.csv` for past cuts and `docs/merged_prs.csv` for merged pull requests.
'''

ARCH = '''# Architecture

```
reports ──> invoice ──> fx ──> data/fx_rates.csv
   │           │
   │           └──> money (rounding)
   └──> customers ──> data/customers.csv
aging ──> invoice
pos/receipt, exporters ──> invoice / money
```

All amounts are `Decimal`. Every conversion to AED goes through `fx.convert`, which uses the rate in force on the
invoice date. Treasury publishes rates on Mondays; a rate is valid from its publication day until the next one.
'''


def pyproject(version=VERSION) -> str:
    return f'''[project]
name = "ledger"
version = "{version}"
description = "Accounts receivable for Gulf Distribution Co."
requires-python = ">=3.11"
dependencies = [
    "ledger-schemas==3.1.0",
    "python-dateutil>=2.9",
]

[tool.pytest.ini_options]
testpaths = ["tests"]
'''


REQUIREMENTS = "ledger-schemas==3.1.0\npython-dateutil==2.9.0.post0\n"


# ------------------------------------------------------------------ tests (values computed by the correct package)

PROBE = r'''
import datetime as dt, json, sys
from decimal import Decimal
from ledger import aging, config, fx, invoice, reports, tax
from ledger.discounts import bulk_discount
from ledger.late_fees import late_fee
from ledger.pos.receipt import receipt_lines
from ledger.exporters.json_export import export_invoice
d = dt.date
s = lambda x: str(x)
out = {}
out["usd_mid"] = s(fx.rate_on("USD", d(2026, 3, 4)))
out["eur_mid"] = s(fx.rate_on("EUR", d(2026, 6, 18)))
out["gbp_sun"] = s(fx.rate_on("GBP", d(2026, 8, 30)))
out["convert_mid"] = s(fx.convert(Decimal("1250.40"), "USD", d(2026, 3, 4)))
mondays = [d(2026, 3, 2), d(2026, 7, 13), d(2026, 9, 28)]
out["mondays"] = {c: [s(fx.rate_on(c, m)) for m in mondays] for c in ("USD", "EUR", "GBP")}
out["q3_region"] = {k: s(v) for k, v in reports.revenue_by_region(d(2026, 7, 1), d(2026, 9, 30)).items()}
out["h1_top"] = [[k, s(v)] for k, v in reports.top_customers(d(2026, 1, 1), d(2026, 6, 30), 3)]
out["q2_customer"] = {k: s(v) for k, v in reports.revenue_by_customer(d(2026, 4, 1), d(2026, 6, 30)).items()}
out["march"] = s(reports.monthly_revenue(2026)[3])
out["aging"] = {k: {b: s(x) for b, x in v.items()} for k, v in aging.aging(d(2026, 10, 31)).items()}
inv = invoice.load(d(2026, 2, 2), d(2026, 2, 2))
out["first_feb2"] = [inv[0].number, s(inv[0].net), s(inv[0].vat), s(inv[0].gross)]
print(json.dumps(out))
'''


def package_files(variant: str) -> dict[str, str]:
    """variant: 'clean', 'fx' (the lookup bug) or 'vat' (hard-coded VAT)."""
    lit = variant == "vat"
    return {
        "ledger/__init__.py": src_init(),
        "ledger/config.py": CONFIG,
        "ledger/money.py": MONEY,
        "ledger/fx.py": src_fx(bug=variant == "fx"),
        "ledger/customers.py": CUSTOMERS,
        "ledger/invoice.py": src_invoice(lit),
        "ledger/tax.py": src_tax(lit),
        "ledger/discounts.py": DISCOUNTS,
        "ledger/late_fees.py": LATE_FEES,
        "ledger/reports.py": src_reports(lit),
        "ledger/aging.py": AGING,
        "ledger/pos/__init__.py": '"""Point of sale."""\n',
        "ledger/pos/receipt.py": src_receipt(lit),
        "ledger/exporters/__init__.py": '"""Exporters."""\n',
        "ledger/exporters/csv_export.py": CSV_EXPORT,
        "ledger/exporters/json_export.py": src_json_export(lit),
        "ledger/legacy/__init__.py": '"""2024 code kept for the audit scripts. Not imported by the package."""\n',
        "ledger/legacy/fx_old.py": FX_OLD,
        "ledger/legacy/tax_v1.py": TAX_V1,
    }


def data_files() -> dict[str, str]:
    custs = customers()
    return {
        "ledger/data/fx_rates.csv": table_csv(["published_on", "currency", "aed_per_unit"], fx_rows()),
        "ledger/data/customers.csv": table_csv(["id", "name", "region", "credit_limit"], custs),
        "ledger/data/invoices.csv": table_csv(["number", "customer_id", "issued_on", "currency", "net", "status"],
                                              invoices(custs)),
    }


def probe(variant: str = "clean") -> dict:
    """Run the package (as written for a variant) in a child process and return the values the tests assert."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for rel, text in {**package_files(variant), **data_files()}.items():
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text(text)
        out = subprocess.run([sys.executable, "-c", PROBE], cwd=root, capture_output=True, text=True, check=False,
                             env={**os.environ, "PYTHONPATH": str(root), "PYTHONHASHSEED": "0"})
        if out.returncode:
            raise RuntimeError(out.stderr)
        return json.loads(out.stdout)


def _dec_dict(d: dict, indent: int) -> str:
    pad = " " * indent
    return "{\n" + "".join(f'{pad}    "{k}": Decimal("{v}"),\n' for k, v in d.items()) + pad + "}"


def tests(v: dict) -> dict[str, str]:
    return {
        "tests/test_money.py": '''from decimal import Decimal

from ledger.money import D, fmt, round_money


def test_round_half_away_from_zero():
    assert round_money("2.345") == Decimal("2.35")
    assert round_money("-2.345") == Decimal("-2.35")


def test_floats_go_through_str():
    assert D(0.1) == Decimal("0.1")


def test_fmt():
    assert fmt(1234.5) == "1,234.50"
''',
        "tests/test_fx.py": f'''import datetime as dt
from decimal import Decimal

import pytest

from ledger import fx


def test_aed_is_one():
    assert fx.rate_on("AED", dt.date(2026, 3, 4)) == Decimal("1")


def test_midweek_days_use_that_weeks_rate():
    assert fx.rate_on("USD", dt.date(2026, 3, 4)) == Decimal("{v['usd_mid']}")
    assert fx.rate_on("EUR", dt.date(2026, 6, 18)) == Decimal("{v['eur_mid']}")


def test_sunday_uses_the_rate_of_the_monday_before():
    assert fx.rate_on("GBP", dt.date(2026, 8, 30)) == Decimal("{v['gbp_sun']}")


def test_convert_rounds_to_the_fils():
    assert fx.convert(Decimal("1250.40"), "USD", dt.date(2026, 3, 4)) == Decimal("{v['convert_mid']}")


def test_unknown_currency():
    with pytest.raises(KeyError):
        fx.rate_on("JPY", dt.date(2026, 3, 4))


def test_no_rate_before_the_first_publication():
    with pytest.raises(LookupError):
        fx.rate_on("USD", dt.date(2025, 12, 1))
''',
        "tests/test_invoice.py": f'''import datetime as dt
from decimal import Decimal

from ledger import invoice
from ledger.invoice import Invoice


def test_vat_and_gross():
    inv = Invoice("INV-1", "C-1001", dt.date(2026, 3, 4), "AED", Decimal("200.00"))
    assert inv.vat == Decimal("10.00")
    assert inv.gross == Decimal("210.00")


def test_aed_invoices_need_no_conversion():
    inv = Invoice("INV-2", "C-1001", dt.date(2026, 3, 2), "AED", Decimal("99.99"))
    assert inv.net_aed() == Decimal("99.99")


def test_load_by_date():
    first = invoice.load(dt.date(2026, 2, 2), dt.date(2026, 2, 2))[0]
    assert [first.number, str(first.net), str(first.vat), str(first.gross)] == {v['first_feb2']!r}


def test_void_invoices_do_not_count():
    inv = Invoice("INV-3", "C-1001", dt.date(2026, 3, 4), "AED", Decimal("5"), "void")
    assert not inv.counts
''',
        "tests/test_reports.py": f'''import datetime as dt
from decimal import Decimal

from ledger import reports


def test_q3_revenue_by_region():
    assert reports.revenue_by_region(dt.date(2026, 7, 1), dt.date(2026, 9, 30)) == {_dec_dict(v['q3_region'], 4)}


def test_top_customers_first_half():
    assert reports.top_customers(dt.date(2026, 1, 1), dt.date(2026, 6, 30), 3) == [
{"".join(f'        ("{k}", Decimal("{x}")),{chr(10)}' for k, x in v['h1_top'])}    ]


def test_march_revenue():
    assert reports.monthly_revenue(2026)[3] == Decimal("{v['march']}")


def test_net_of_vat():
    assert reports.net_of_vat("105.00") == Decimal("100.00")
    assert reports.net_of_vat("1") == Decimal("0.95")
''',
        "tests/test_aging.py": f'''import datetime as dt
from decimal import Decimal

from ledger import aging


def test_buckets():
    assert [aging.bucket(n) for n in (-3, 0, 1, 30, 31, 60, 61, 90, 91)] == [
        "current", "current", "1-30", "1-30", "31-60", "31-60", "61-90", "61-90", "90+"]


def test_aed_aging_end_of_october():
    assert aging.aging(dt.date(2026, 10, 31))["AED"] == {_dec_dict(v['aging']['AED'], 4)}
''',
        "tests/test_tax.py": '''from decimal import Decimal

from ledger import tax


def test_vat_is_five_percent():
    assert tax.vat("100") == Decimal("5.00")
    assert tax.vat("0.10") == Decimal("0.01")


def test_with_vat():
    assert tax.with_vat("40") == Decimal("42.00")
''',
        "tests/test_discounts.py": '''from decimal import Decimal

from ledger.discounts import bulk_discount


def test_bulk_orders_get_five_percent():
    assert bulk_discount("200", 10) == Decimal("10.00")
    assert bulk_discount("200", 9) == Decimal("0.00")
''',
        "tests/test_late_fees.py": '''from decimal import Decimal

from ledger.late_fees import late_fee


def test_one_started_period():
    assert late_fee("1000", 1) == Decimal("50.00")
    assert late_fee("1000", 30) == Decimal("50.00")


def test_two_started_periods():
    assert late_fee("1000", 31) == Decimal("100.00")


def test_nothing_when_not_overdue():
    assert late_fee("1000", 0) == Decimal("0.00")
''',
        "tests/test_receipt.py": '''from ledger.pos.receipt import receipt_lines


def test_receipt_shows_the_vat_part():
    lines = receipt_lines([("Pallet wrap", 2, "52.50"), ("Tape", 1, "10.50")])
    assert lines[-2].endswith("115.50")
    assert lines[-1].startswith("VAT 5% included")
    assert lines[-1].endswith("5.50")
''',
        "tests/test_export.py": '''import datetime as dt
import json
from decimal import Decimal

from ledger.exporters.csv_export import export_csv
from ledger.exporters.json_export import export_invoice
from ledger.invoice import Invoice

INV = Invoice("INV-9", "C-1001", dt.date(2026, 3, 4), "AED", Decimal("200.00"))


def test_json_export():
    out = export_invoice(INV)
    assert out["vat"] == "10.00" and out["gross"] == "210.00" and out["vat_rate"] == 0.05
    json.dumps(out)


def test_csv_export():
    assert export_csv([INV]).splitlines()[1] == "INV-9,C-1001,2026-03-04,AED,200.00,10.00,210.00,issued"
''',
        "tests/test_version.py": '''import tomllib
from pathlib import Path

import ledger


def test_package_version_matches_pyproject():
    pyproject = Path(ledger.__file__).resolve().parents[1] / "pyproject.toml"
    assert tomllib.loads(pyproject.read_text())["project"]["version"] == ledger.__version__
''',
    }


def common_files() -> dict[str, str]:
    prs = pull_requests()
    return {
        "README.md": README,
        "pyproject.toml": pyproject(),
        "requirements.txt": REQUIREMENTS,
        "CHANGELOG.md": changelog(),
        "docs/architecture.md": ARCH,
        "docs/merged_prs.csv": table_csv(["number", "title", "label", "base", "merged_at", "author"], prs),
        "docs/releases.csv": table_csv(["version", "cut_from_main_at"], RELEASES),
    }


SEEDS = {"b4a-ledger": "clean", "b4a-ledger-fx": "fx", "b4a-ledger-vat": "vat"}


def build(catalog: Path) -> None:
    values = probe("clean")
    data, common, test_files = data_files(), common_files(), tests(values)
    for seed, variant in SEEDS.items():
        root = catalog / "seeds" / seed / "files"
        for rel, text in {**common, **package_files(variant), **data, **test_files}.items():
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text(text)


if __name__ == "__main__":
    build(Path(sys.argv[1]))
