"""Writes the batch-4 (author B) seeds: a sales ledger (SQLite + files), a back office (biz state + files) and a
treasury database. Deterministic: fixed dates and a fixed random seed, so the generated files are the same bytes
on every machine.

    uv run python scripts/taskgen/b4b_seeds.py <catalog>

Seeds written:
  seeds/b4b_ledger/db.sql        Qamar Trading's sales ledger: 11 tables, ~18,000 rows
  seeds/b4b_ledger/files/        finance docs (current and superseded rules), incident notes (one with a stale
                                 draft), old exports, supplier price lists (xlsx: current, draft, last quarter)
  seeds/b4b_backoffice/state.json  the biz server's state: ~330 contacts, ~900 invoices, credit notes, ~380 tickets
  seeds/b4b_backoffice/files/    policies with amendments and a superseded version, bank statements
  seeds/b4b_treasury/db.sql      bank transactions, settlement terms, payer aliases

scripts/taskgen/b4b_tasks.py reads the same data (it imports this module) to compute the tasks' expected values.
"""

from __future__ import annotations

import json
import random
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import office

from harness_factory.env.mcp.impl.biz_server import recompute

D = Decimal
CENT = D("0.01")


def q2(x: Decimal) -> Decimal:
    return x.quantize(CENT, rounding=ROUND_HALF_UP)


def d(s: str) -> date:
    return date.fromisoformat(s)


def days(start: date, end: date):
    n = start
    while n <= end:
        yield n
        n += timedelta(days=1)


# ====================================================================== ledger (SQLite)

LEDGER_START, LEDGER_END = d("2025-11-01"), d("2026-10-31")
LEDGER_TODAY = d("2026-11-02")
FX_CCY = ("USD", "EUR", "GBP", "SAR")
COUNTRY_CCY = {"AE": "AED", "SA": "SAR", "GB": "GBP", "DE": "EUR", "FR": "EUR", "NL": "EUR", "ES": "EUR",
               "US": "USD"}

PREFIX_AE = ["Al Fajr", "Blue Lagoon", "Coral Bay", "Desert Pearl", "Emerald Dunes", "Falcon Crest", "Golden Sands",
             "Harbour View", "Jumeirah Breeze", "Khalifa Park", "Lotus Garden", "Marina Heights", "Nakheel Bay",
             "Oryx", "Palm Grove", "Qasr Al Bahr", "Ras Al Hamra", "Sahara Star", "Sunset Creek", "Tamarind",
             "Umm Suqeim", "Wadi Hatta", "Yas Bay", "Zaytoon", "Saffron", "Barakat", "Dhow Harbour", "Creekside",
             "Al Waha", "Al Mina", "Ghaf Tree", "Mirdif", "Hatta Springs", "Liwa Oasis", "Bay Square", "Silk Road",
             "Arabian Ranches", "Fujairah Cove", "Saadiyat Shore", "Najd"]
PREFIX_INTL = ["Thames", "Harbourside", "Kensington", "Riverside", "Lakeside", "Brookfield", "Hamilton", "Bellevue",
               "Montclair", "Lindenhof", "Rive Gauche", "Amstel", "Prado", "Highgate", "Westbourne", "Sankt Georg",
               "Hudson Yard", "Bayview", "Canal Street", "Old Port"]
SUFFIX = ["Hotel", "Resort", "Catering", "Suites", "Restaurants", "Hospitality", "Residences", "Cafes", "Clubs",
          "Kitchens", "Hotel Apartments", "Banqueting", "Beach Club", "Grill House"]
REPS = [("Aisha Karim", "UAE North"), ("Bilal Haddad", "UAE North"), ("Chloe Martin", "Europe"),
        ("Dev Patel", "UAE South"), ("Elena Rossi", "Europe"), ("Faisal Otaibi", "KSA"), ("Grace Kim", "Americas"),
        ("Hamza Yusuf", "KSA"), ("Isabel Cruz", "Americas"), ("Jamal Nasser", "UAE South"),
        ("Karen Doyle", "UK"), ("Liam Walsh", "UK")]

CATEGORIES = {  # category: (sku prefix, [names], (low, high) unit cost AED)
    "linen": ("LN", ["Bath towel", "Hand towel", "Bath mat", "Pillowcase", "Duvet cover", "Fitted sheet",
                     "Table napkin", "Tablecloth", "Bathrobe", "Pool towel"], (8, 140)),
    "tableware": ("TW", ["Dinner plate", "Side plate", "Soup bowl", "Wine glass", "Tumbler", "Coffee cup",
                         "Saucer", "Cutlery set", "Serving platter", "Water jug"], (4, 95)),
    "amenities": ("AM", ["Shampoo bottle", "Conditioner bottle", "Soap bar", "Dental kit", "Shower cap",
                         "Slippers", "Vanity kit", "Sewing kit", "Body lotion", "Shaving kit"], (1, 18)),
    "cleaning": ("CL", ["Floor cleaner", "Glass cleaner", "Dish detergent", "Hand sanitiser", "Bin liners",
                        "Microfibre cloth", "Mop head", "Degreaser", "Toilet cleaner", "Air freshener"], (3, 60)),
    "kitchen": ("KT", ["Chef knife", "Cutting board", "Stock pot", "Sheet pan", "Food container", "Whisk",
                       "Ladle", "Tongs", "Mixing bowl", "Thermometer"], (6, 260)),
    "furniture": ("FN", ["Luggage rack", "Bedside lamp", "Desk chair", "Pool lounger", "Room safe",
                         "Ironing board", "Coat stand", "Mini fridge", "Hair dryer", "Kettle"], (45, 900)),
}
SUPPLIERS = ["Hilal Supplies", "Gulf Hospitality Trading", "Orient Linen Co", "Bayside Chemicals"]
USD_AED = D("3.6725")


@dataclass
class Customer:
    id: int
    code: str
    name: str
    email: str
    country: str
    currency: str
    created_on: date
    rep_id: int
    weight: float
    deleted_at: date | None = None
    merged_into: int | None = None
    merged_on: date | None = None


@dataclass
class Order:
    id: int
    customer_id: int
    order_date: date
    currency: str
    status: str
    channel: str
    deleted_at: str | None
    lines: list = field(default_factory=list)   # (product_id, qty, unit_price Decimal)

    @property
    def value(self) -> Decimal:
        return sum((q * p for _, q, p in self.lines), D(0))


@dataclass
class Ledger:
    customers: list[Customer]
    merges: list[tuple]           # (id, source, target, merged_on, merged_by)
    products: list[dict]
    orders: list[Order]
    payments: list[dict]
    refunds: list[dict]
    fx: dict                      # (date, ccy) -> Decimal
    fx_monthly: dict              # (YYYY-MM, ccy) -> Decimal
    shipments: list[tuple]
    hilal: dict                   # price list data for the supplier tasks
    incident: dict                # the INC-2041 facts


def fx_table(rng: random.Random):
    fx, monthly = {}, {}
    eur, gbp = 3.98, 4.66
    for day in days(LEDGER_START, LEDGER_END):
        eur = min(max(eur + rng.uniform(-0.012, 0.012), 3.85), 4.15)
        gbp = min(max(gbp + rng.uniform(-0.014, 0.014), 4.50), 4.85)
        fx[(day, "USD")] = USD_AED
        fx[(day, "SAR")] = D("0.9793")
        fx[(day, "EUR")] = D(f"{eur:.4f}")
        fx[(day, "GBP")] = D(f"{gbp:.4f}")
    months = sorted({k[0].strftime("%Y-%m") for k in fx})
    for m in months:
        for c in FX_CCY:
            vals = [v for (day, cc), v in fx.items() if cc == c and day.strftime("%Y-%m") == m]
            monthly[(m, c)] = (sum(vals) / len(vals)).quantize(D("0.0001"), rounding=ROUND_HALF_UP)
    return fx, monthly


def make_products(rng: random.Random) -> list[dict]:
    out, pid = [], 1
    for cat, (pfx, names, (lo, hi)) in CATEGORIES.items():
        for i, name in enumerate(names):
            for variant in ("Standard", "Premium", "Deluxe", "Eco", "Bulk"):
                cost = D(f"{rng.uniform(lo, hi):.2f}")
                out.append({"id": pid, "sku": f"{pfx}-{1000 + 10 * i + len(out) % 10:04d}",
                            "name": f"{name}, {variant.lower()}", "category": cat, "supplier": None,
                            "unit_cost_aed": cost, "list_price_aed": q2(cost * D(rng.choice(["1.6", "1.8", "2.0"]))),
                            "active": 1})
                pid += 1
    seen = set()
    for p in out:                                  # unique SKUs
        base = p["sku"]
        n = 0
        while p["sku"] in seen:
            n += 1
            p["sku"] = f"{base[:3]}{int(base[3:]) + 500 + n:04d}"
        seen.add(p["sku"])
    for p in out:
        p["supplier"] = SUPPLIERS[rng.choices(range(4), weights=[48, 22, 18, 12])[0]]
    for p in rng.sample(out, 18):
        p["active"] = 0
    return out


def make_customers(rng: random.Random) -> tuple[list[Customer], list[tuple]]:
    names_ae = [f"{p} {s}" for p in PREFIX_AE for s in SUFFIX]
    names_int = [f"{p} {s}" for p in PREFIX_INTL for s in SUFFIX]
    rng.shuffle(names_ae)
    rng.shuffle(names_int)
    customers: list[Customer] = []

    def add(name, country, created, weight, code=None):
        cid = 101 + len(customers)
        slug = "".join(ch for ch in name.lower() if ch.isalnum())[:18]
        tld = {"AE": "ae", "SA": "sa", "GB": "co.uk", "US": "com"}.get(country, country.lower())
        c = Customer(cid, code or f"QC-{10000 + cid}", name, f"accounts@{slug}.{tld}", country,
                     COUNTRY_CCY[country], created, 1 + (cid * 7) % len(REPS), weight)
        customers.append(c)
        return c

    for _ in range(330):
        country = rng.choices(["AE", "SA", "GB", "DE", "FR", "NL", "ES", "US"],
                              weights=[52, 18, 9, 4, 3, 2, 2, 10])[0]
        name = (names_ae if country in ("AE", "SA") else names_int).pop()
        created = LEDGER_START - timedelta(days=rng.randint(30, 1500)) if rng.random() < 0.85 else \
            LEDGER_START + timedelta(days=rng.randint(0, 300))
        add(name, country, created, rng.lognormvariate(0, 0.9))
    for n in range(1, 7):                          # test accounts: real-looking orders, never reported
        add(f"QA Test Account {n}", "AE", d("2025-06-01"), 2.2, code=f"TEST-{n:03d}")

    # Duplicates merged into a surviving account. Most are one hop; three chains are two hops (A -> B -> C).
    merges: list[tuple] = []
    targets = [c for c in customers[:330] if c.created_on < d("2025-09-01")]
    rng.shuffle(targets)

    def dup(of: Customer, suffix: str, created: date, weight: float) -> Customer:
        c = add(f"{of.name}{suffix}", of.country, created, weight)
        c.email = c.email.replace("accounts@", rng.choice(["finance@", "ap@", "billing@"]))
        return c

    for tgt in targets[:17]:
        src = dup(tgt, rng.choice([" LLC", " L.L.C.", " (old account)", " FZE"]), tgt.created_on, tgt.weight * 0.7)
        when = LEDGER_START + timedelta(days=rng.randint(60, 300))
        src.merged_into, src.merged_on, src.deleted_at = tgt.id, when, when
        merges.append((src, tgt, when))
    return customers, merges


CHAIN_WEIGHTS = [(11.0, 8.0, 3.2), (4.6, 3.4, 1.8), (2.0, 1.5, 3.1)]   # (A, B, C) per chain, see make_ledger


def make_ledger() -> Ledger:
    rng = random.Random(20261102)
    fx, fx_monthly = fx_table(rng)
    products = make_products(rng)
    customers, merges = make_customers(rng)

    # Three two-hop chains: A merged into B, then B into C. C is the surviving account.
    used = {m[1].id for m in merges} | {m[0].id for m in merges}
    pool = [c for c in customers[:330] if c.id not in used and c.country == "AE" and c.created_on < d("2025-06-01")]
    chain_specs = [("2026-02-16", "2026-06-29"), ("2026-03-09", "2026-08-03"), ("2026-01-19", "2026-05-25")]
    chains = []
    for k, (first, second) in enumerate(chain_specs):
        c = pool[k]
        wa, wb, wc = CHAIN_WEIGHTS[k]
        c.weight = wc
        b = _append(customers, c.name + " Group", c, wb, rng)
        a = _append(customers, c.name + " LLC", c, wa, rng)
        a.merged_into, a.merged_on, a.deleted_at = b.id, d(first), d(first)
        b.merged_into, b.merged_on, b.deleted_at = c.id, d(second), d(second)
        merges.append((a, b, d(first)))
        merges.append((b, c, d(second)))
        chains.append((a, b, c))
    merges.sort(key=lambda m: (m[2], m[0].id))
    merge_rows = [(i, s.id, t.id, w.isoformat(), REPS[(s.id * 3) % len(REPS)][0])
                  for i, (s, t, w) in enumerate(merges, 1)]

    # Orders, one day at a time.
    by_id = {c.id: c for c in customers}
    prods_active = [p for p in products]
    orders: list[Order] = []
    oid = 50001
    for day in days(LEDGER_START, LEDGER_END):
        alive = [c for c in customers if c.created_on <= day and (c.merged_on is None or day < c.merged_on)]
        weights = [c.weight for c in alive]
        n = rng.randint(6, 12) if day.weekday() != 4 else rng.randint(2, 5)
        for _ in range(n):
            c = rng.choices(alive, weights=weights)[0]
            status = "completed"
            r = rng.random()
            if r < 0.06:
                status = "cancelled"
            elif day >= d("2026-10-12") and r < 0.30:
                status = "pending"
            o = Order(oid, c.id, day, c.currency, status, rng.choice(["web", "web", "sales", "edi"]), None)
            rate = D(1) if c.currency == "AED" else fx[(day, c.currency)]
            for p in rng.sample(prods_active, rng.choice([1, 1, 2, 2, 3, 4])):
                qty = rng.choice([2, 4, 6, 10, 12, 20, 24, 40, 50])
                price = q2(p["list_price_aed"] / rate)
                o.lines.append((p["id"], qty, price))
            orders.append(o)
            oid += 1
    # Orders entered twice by mistake: soft-deleted copies of a real order, same day, same lines.
    for o in rng.sample([o for o in orders if o.status == "completed" and o.customer_id <= 430], 48):
        orders.append(Order(oid, o.customer_id, o.order_date, o.currency, "completed", o.channel,
                            f"{o.order_date.isoformat()} 17:{rng.randint(10, 59):02d}:00", list(o.lines)))
        oid += 1
    orders.sort(key=lambda o: (o.order_date, o.id))

    # Payments: one settled capture per completed order, sometimes two instalments, sometimes a failed attempt
    # first. INC-2041 (Aug 14-16): the card gateway retried timed-out captures and the retry was also settled.
    payments: list[dict] = []
    pid = 700001

    def pay(o: Order, when: datetime, amount: Decimal, method: str, ref: str, status: str, note=None):
        nonlocal pid
        payments.append({"id": pid, "order_id": o.id, "paid_at": when.strftime("%Y-%m-%d %H:%M:%S"),
                          "amount": amount, "currency": o.currency, "method": method, "gateway_ref": ref,
                          "status": status, "note": note})
        pid += 1
        return payments[-1]

    window = (d("2026-08-14"), d("2026-08-16"))
    inc = {"retries": [], "originals": [], "failed_first": [], "instalments": [], "draft_misses": []}
    card_in_window = []
    for o in orders:
        if o.status != "completed" or o.deleted_at:
            continue
        paid_day = o.order_date + timedelta(days=rng.randint(0, 12))
        paid_day = min(paid_day, LEDGER_TODAY)
        when = datetime(paid_day.year, paid_day.month, paid_day.day, rng.randint(8, 20), rng.randint(0, 59),
                        rng.randint(0, 59))
        in_window = window[0] <= paid_day <= window[1]
        method = "card" if rng.random() < (0.9 if in_window else 0.55) else "bank_transfer"
        ref = f"{'GW' if method == 'card' else 'BT'}{rng.randint(10**7, 10**8 - 1)}"
        if window[0] <= paid_day <= window[1] and method == "card":
            card_in_window.append((o, when, ref))
            continue
        if rng.random() < 0.05 and o.value >= 400:
            half = q2(o.value / 2)
            pay(o, when, half, method, ref, "settled")
            pay(o, when + timedelta(days=rng.randint(10, 25)), o.value - half, method, ref + "-2", "settled")
        else:
            if method == "card" and rng.random() < 0.04:
                pay(o, when - timedelta(minutes=rng.randint(3, 40)), o.value, method, ref + "-F", "failed")
            pay(o, when, o.value, method, ref, "settled")
    # In the incident window, card payments: plain ones, duplicates (the incident), retries after a failed
    # capture (correct, must stay) and equal instalments that look like duplicates.
    card_in_window.sort(key=lambda t: t[1])
    even = [k for k, (o, _, _) in enumerate(card_in_window) if int(o.value * 100) % 2 == 0]
    inst = rng.sample(even, 2)
    rest = [k for k in range(len(card_in_window)) if k not in inst]
    kind_of = dict(zip(rng.sample(rest, 12), ["dup"] * 9 + ["failed_first"] * 3, strict=True))
    kind_of.update({k: "instalments" for k in inst})
    for k, (o, when, ref) in enumerate(card_in_window):
        kind = kind_of.get(k)
        if kind == "dup":
            orig = pay(o, when, o.value, "card", ref, "settled")
            retry = pay(o, when + timedelta(minutes=rng.randint(2, 13), seconds=rng.randint(0, 59)), o.value,
                        "card", ref + "-RT", "settled")
            inc["originals"].append(orig["id"])
            inc["retries"].append(retry["id"])
            if when.date() == window[1]:
                inc["draft_misses"].append(retry["id"])
        elif kind == "failed_first":
            f = pay(o, when, o.value, "card", ref, "failed")
            r = pay(o, when + timedelta(minutes=rng.randint(2, 9)), o.value, "card", ref + "-RT", "settled")
            inc["failed_first"].append((f["id"], r["id"]))
        elif kind == "instalments":
            # split in two equal card payments the same afternoon: looks like a duplicate, isn't one
            half = o.value / 2
            a = pay(o, when, half, "card", ref, "settled")
            b = pay(o, when + timedelta(minutes=rng.randint(4, 12)), half, "card",
                    ref[:2] + str(int(ref[2:]) + 1), "settled")
            inc["instalments"].append((a["id"], b["id"]))
        else:
            pay(o, when, o.value, "card", ref, "settled")
    # An earlier incident (INC-1987, July), already fixed: reversed retries.
    for o in [o for o in orders if o.status == "completed" and not o.deleted_at
              and d("2026-07-07") <= o.order_date <= d("2026-07-08")][:4]:
        first = next(p for p in payments if p["order_id"] == o.id)
        pay(o, datetime.fromisoformat(first["paid_at"]) + timedelta(minutes=6), first["amount"], first["method"],
            first["gateway_ref"] + "-RT", "reversed", "INC-1987")
    payments.sort(key=lambda p: (p["paid_at"], p["id"]))
    renum = {p["id"]: 700001 + k for k, p in enumerate(payments)}    # ids in the order payments were recorded
    for p in payments:
        p["id"] = renum[p["id"]]
    inc = {k: [renum[x] for x in v] if k in ("retries", "originals", "draft_misses") else
           [tuple(renum[x] for x in pair) for pair in v] for k, v in inc.items()}

    # Refunds on completed orders: processed or rejected; some cross the quarter boundary.
    refunds, rid = [], 9001
    for o in rng.sample([o for o in orders if o.status == "completed" and not o.deleted_at
                         and not by_id[o.customer_id].code.startswith("TEST-")], 230):
        when = o.order_date + timedelta(days=rng.randint(2, 45))
        if when > LEDGER_TODAY:
            continue
        share = rng.choice([D("0.1"), D("0.15"), D("0.25"), D("0.5"), D("1")])
        refunds.append({"id": rid, "order_id": o.id, "refunded_on": when, "amount": q2(o.value * share),
                        "currency": o.currency, "reason": rng.choice(["damaged in transit", "wrong item",
                                                                      "short shipment", "quality complaint",
                                                                      "order cancelled after dispatch"]),
                        "status": "rejected" if rng.random() < 0.12 else "processed"})
        rid += 1
    refunds.sort(key=lambda r: (r["refunded_on"], r["id"]))

    shipments, sid = [], 1
    for o in orders:
        if o.status == "completed" and not o.deleted_at:
            shipments.append((sid, o.id, (o.order_date + timedelta(days=rng.randint(1, 4))).isoformat(),
                              rng.choice(["JAFZA", "DIC", "KEZAD"]), rng.choice(["Aramex", "DHL", "Fetchr", "own fleet"])))
            sid += 1

    hilal = make_hilal(rng, products)
    return Ledger(customers, merge_rows, products, orders, payments, refunds, fx, fx_monthly, shipments, hilal, inc)


def _append(customers: list[Customer], name: str, like: Customer, weight: float, rng) -> Customer:
    cid = 101 + len(customers)
    slug = "".join(ch for ch in name.lower() if ch.isalnum())[:18]
    c = Customer(cid, f"QC-{10000 + cid}", name, f"{rng.choice(['finance', 'ap', 'billing'])}@{slug}.ae",
                 like.country, like.currency, like.created_on, like.rep_id, weight)
    customers.append(c)
    return c


# ---------------------------------------------------------------------- the Hilal Supplies price list (xlsx)

def make_hilal(rng: random.Random, products: list[dict]) -> dict:
    """Q3 and Q4 price lists. Prices are per pack, in USD; our unit cost is per unit, in AED, at the contract rate.
    Our current cost of every Hilal product is its Q3 price, converted."""
    hil = [p for p in products if p["supplier"] == "Hilal Supplies"]
    rows = []
    for p in hil:
        pack = rng.choice([1, 1, 1, 6, 12, 24, 50]) if p["category"] in ("amenities", "cleaning", "tableware",
                                                                          "linen") else 1
        q3_usd = q2(p["unit_cost_aed"] * pack / USD_AED)
        p["unit_cost_aed"] = q2(q3_usd * USD_AED / pack)
        rows.append({"sku": p["sku"], "name": p["name"], "pack": pack, "q3": q3_usd, "q4": q3_usd,
                     "status": "Active", "id": p["id"], "active": p["active"]})
    rng.shuffle(rows)
    live = [r for r in rows if r["active"]]
    changed = live[:38]
    for r in changed:
        r["q4"] = q2(r["q3"] * D(rng.choice(["1.04", "1.06", "1.08", "0.95", "1.12", "1.05", "1.03"])))
    withdrawn = live[38:43]
    for r in withdrawn:
        r["status"] = "Withdrawn"
        r["q4"] = q2(r["q3"] * D("0.80"))          # clearance price: not what we pay going forward
    inactive_changed = [r for r in rows if not r["active"]][:3]
    for r in inactive_changed:
        r["q4"] = q2(r["q3"] * D("1.10"))
    # New items we don't stock: listed, not in our catalogue.
    new = [{"sku": f"HS-{9000 + k}", "name": n, "pack": pk, "q3": None, "q4": D(v), "status": "New",
            "id": None, "active": None}
           for k, (n, pk, v) in enumerate([("Bamboo toothbrush", 100, "38.00"), ("Refill pump, 5 l", 1, "21.50"),
                                           ("Linen cart", 1, "412.00")])]
    q4_rows = sorted(rows + new, key=lambda r: r["sku"])
    # The draft (sent two weeks earlier) has different prices for a few changed items.
    draft = {r["sku"]: r["q4"] for r in q4_rows}
    for r in changed[:6]:
        draft[r["sku"]] = q2(r["q3"] * D("1.15"))
    for r in rows:
        r["expected"] = q2(r["q4"] * USD_AED / r["pack"]) if (r["active"] and r["status"] == "Active") else \
            p_cost(products, r["id"])
    return {"rows": q4_rows, "changed": [r["sku"] for r in changed], "withdrawn": [r["sku"] for r in withdrawn],
            "inactive_changed": [r["sku"] for r in inactive_changed], "draft": draft}


def p_cost(products, pid):
    return next(p["unit_cost_aed"] for p in products if p["id"] == pid)


def sql_lit(v) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, str):
        return "'" + v.replace("'", "''") + "'"
    if isinstance(v, date):
        return f"'{v.isoformat()}'"
    if isinstance(v, Decimal):
        return format(v, "f")
    return str(v)


def inserts(table: str, cols: tuple[str, ...], rows, per: int = 200) -> str:
    rows = list(rows)
    out = []
    for i in range(0, len(rows), per):
        chunk = rows[i:i + per]
        vals = ",\n".join("(" + ",".join(sql_lit(v) for v in r) + ")" for r in chunk)
        out.append(f"INSERT INTO {table} ({', '.join(cols)}) VALUES\n{vals};")
    return "\n".join(out)


LEDGER_SCHEMA = """-- Qamar Trading LLC: sales ledger (hospitality supplies). Reporting currency AED.
CREATE TABLE sales_reps (id INTEGER PRIMARY KEY, name TEXT NOT NULL, region TEXT NOT NULL);
CREATE TABLE customers (id INTEGER PRIMARY KEY, account_code TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
  email TEXT NOT NULL, country TEXT NOT NULL, billing_currency TEXT NOT NULL, rep_id INTEGER REFERENCES sales_reps(id),
  created_on TEXT NOT NULL, deleted_at TEXT);
CREATE TABLE customer_merges (id INTEGER PRIMARY KEY, source_customer_id INTEGER NOT NULL REFERENCES customers(id),
  target_customer_id INTEGER NOT NULL REFERENCES customers(id), merged_on TEXT NOT NULL, merged_by TEXT NOT NULL);
CREATE TABLE products (id INTEGER PRIMARY KEY, sku TEXT NOT NULL UNIQUE, name TEXT NOT NULL, category TEXT NOT NULL,
  supplier TEXT NOT NULL, unit_cost_aed REAL NOT NULL, list_price_aed REAL NOT NULL, active INTEGER NOT NULL);
CREATE TABLE orders (id INTEGER PRIMARY KEY, customer_id INTEGER NOT NULL REFERENCES customers(id),
  order_date TEXT NOT NULL, currency TEXT NOT NULL, status TEXT NOT NULL, channel TEXT NOT NULL, deleted_at TEXT);
CREATE TABLE order_lines (id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL REFERENCES orders(id),
  product_id INTEGER NOT NULL REFERENCES products(id), quantity INTEGER NOT NULL, unit_price REAL NOT NULL);
CREATE TABLE payments (id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL REFERENCES orders(id), paid_at TEXT NOT NULL,
  amount REAL NOT NULL, currency TEXT NOT NULL, method TEXT NOT NULL, gateway_ref TEXT NOT NULL,
  status TEXT NOT NULL, note TEXT);
CREATE TABLE refunds (id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL REFERENCES orders(id),
  refunded_on TEXT NOT NULL, amount REAL NOT NULL, currency TEXT NOT NULL, reason TEXT NOT NULL, status TEXT NOT NULL);
CREATE TABLE fx_rates (rate_date TEXT NOT NULL, currency TEXT NOT NULL, aed_per_unit REAL NOT NULL,
  PRIMARY KEY (rate_date, currency));
CREATE TABLE fx_monthly_avg (month TEXT NOT NULL, currency TEXT NOT NULL, avg_aed_per_unit REAL NOT NULL,
  PRIMARY KEY (month, currency));
CREATE TABLE shipments (id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL REFERENCES orders(id),
  shipped_on TEXT NOT NULL, warehouse TEXT NOT NULL, carrier TEXT NOT NULL);
"""


def ledger_sql(L: Ledger) -> str:
    parts = [LEDGER_SCHEMA]
    parts.append(inserts("sales_reps", ("id", "name", "region"), [(i, n, r) for i, (n, r) in enumerate(REPS, 1)]))
    parts.append(inserts("customers", ("id", "account_code", "name", "email", "country", "billing_currency", "rep_id",
                                       "created_on", "deleted_at"),
                         [(c.id, c.code, c.name, c.email, c.country, c.currency, c.rep_id, c.created_on,
                           f"{c.deleted_at.isoformat()} 09:00:00" if c.deleted_at else None) for c in L.customers]))
    parts.append(inserts("customer_merges", ("id", "source_customer_id", "target_customer_id", "merged_on",
                                             "merged_by"), L.merges))
    parts.append(inserts("products", ("id", "sku", "name", "category", "supplier", "unit_cost_aed", "list_price_aed",
                                      "active"),
                         [(p["id"], p["sku"], p["name"], p["category"], p["supplier"], p["unit_cost_aed"],
                           p["list_price_aed"], p["active"]) for p in L.products]))
    parts.append(inserts("orders", ("id", "customer_id", "order_date", "currency", "status", "channel", "deleted_at"),
                         [(o.id, o.customer_id, o.order_date, o.currency, o.status, o.channel, o.deleted_at)
                          for o in sorted(L.orders, key=lambda o: o.id)]))
    lines, lid = [], 1
    for o in sorted(L.orders, key=lambda o: o.id):
        for pid, qty, price in o.lines:
            lines.append((lid, o.id, pid, qty, price))
            lid += 1
    parts.append(inserts("order_lines", ("id", "order_id", "product_id", "quantity", "unit_price"), lines, per=400))
    parts.append(inserts("payments", ("id", "order_id", "paid_at", "amount", "currency", "method", "gateway_ref",
                                      "status", "note"),
                         [tuple(p.values()) for p in sorted(L.payments, key=lambda p: p["id"])]))
    parts.append(inserts("refunds", ("id", "order_id", "refunded_on", "amount", "currency", "reason", "status"),
                         [tuple(r.values()) for r in L.refunds]))
    parts.append(inserts("fx_rates", ("rate_date", "currency", "aed_per_unit"),
                         [(k[0], k[1], v) for k, v in sorted(L.fx.items())], per=400))
    parts.append(inserts("fx_monthly_avg", ("month", "currency", "avg_aed_per_unit"),
                         [(k[0], k[1], v) for k, v in sorted(L.fx_monthly.items())]))
    parts.append(inserts("shipments", ("id", "order_id", "shipped_on", "warehouse", "carrier"), L.shipments, per=400))
    return "\n".join(parts) + "\n"


def ledger_net(L: Ledger, lo: date, hi: date, *, monthly=False, keep_deleted=False, keep_test=False,
               refunds_by_order=False, drop_aed=False, no_refunds=False, hops=99, drop_merged=False) -> dict:
    """Net revenue in AED per surviving account under the reporting rules (keyword arguments: the plausible
    mistakes, for the trap scripts). Exact (Decimal); the caller rounds."""
    cust = {c.id: c for c in L.customers}
    orders = {o.id: o for o in L.orders}

    def survivor(cid):
        c, n = cust[cid], 0
        while c.merged_into and n < hops:
            c, n = cust[c.merged_into], n + 1
        return c.id

    def rate(ccy, day):
        if ccy == "AED":
            return D(1)
        return L.fx_monthly[(day.strftime("%Y-%m"), ccy)] if monthly else L.fx[(day, ccy)]

    def counted(o):
        c = cust[o.customer_id]
        return (o.status == "completed" and (keep_deleted or not o.deleted_at)
                and (keep_test or not c.code.startswith("TEST-")) and not (drop_aed and o.currency == "AED")
                and not (drop_merged and c.deleted_at))

    tot: dict[int, Decimal] = {}
    for o in L.orders:
        if lo <= o.order_date <= hi and counted(o):
            k = survivor(o.customer_id)
            tot[k] = tot.get(k, D(0)) + o.value * rate(o.currency, o.order_date)
    for r in [] if no_refunds else L.refunds:
        o = orders[r["order_id"]]
        day = o.order_date if refunds_by_order else r["refunded_on"]
        if r["status"] == "processed" and lo <= day <= hi and counted(o):
            k = survivor(o.customer_id)
            tot[k] = tot.get(k, D(0)) - r["amount"] * rate(r["currency"], day)
    return tot


# ---------------------------------------------------------------------- xlsx (reproducible, standard library)

def _col(k: int) -> str:
    s = ""
    k += 1
    while k:
        k, r = divmod(k - 1, 26)
        s = chr(65 + r) + s
    return s


def _xcell(ref: str, v) -> str:
    from xml.sax.saxutils import escape
    if v is None:
        return ""
    if isinstance(v, str):
        return f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{escape(v)}</t></is></c>'
    if isinstance(v, Decimal):
        return f'<c r="{ref}" s="1"><v>{format(v, "f")}</v></c>'
    return f'<c r="{ref}"><v>{v}</v></c>'


def xlsx_bytes_parts(sheets: list[tuple[str, list[list]]], title: str) -> dict[str, str]:
    S, R, CT = office.S, office.R, office.CT
    xml = office.XML
    parts_sheets = {}
    for i, (_, rows) in enumerate(sheets, 1):
        cols = max(len(r) for r in rows)
        body = "".join(f'<row r="{n}">' + "".join(_xcell(f"{_col(k)}{n}", v) for k, v in enumerate(row))
                       + "</row>" for n, row in enumerate(rows, 1))
        parts_sheets[f"xl/worksheets/sheet{i}.xml"] = (
            f'{xml}<worksheet xmlns="{S}"><dimension ref="A1:{_col(cols - 1)}{len(rows)}"/><sheetData>{body}'
            f'</sheetData></worksheet>')
    n = len(sheets)
    workbook = (f'{xml}<workbook xmlns="{S}" xmlns:r="{R}"><sheets>'
                + "".join(f'<sheet name="{name}" sheetId="{i}" r:id="rId{i}"/>' for i, (name, _) in
                          enumerate(sheets, 1)) + "</sheets></workbook>")
    styles = (f'{xml}<styleSheet xmlns="{S}"><fonts count="1"><font><sz val="11"/><name val="Calibri"/></font>'
              f'</fonts><fills count="2"><fill><patternFill patternType="none"/></fill><fill>'
              f'<patternFill patternType="gray125"/></fill></fills><borders count="1"><border><left/><right/>'
              f'<top/><bottom/><diagonal/></border></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" '
              f'fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" '
              f'borderId="0" xfId="0"/><xf numFmtId="2" fontId="0" fillId="0" borderId="0" xfId="0" '
              f'applyNumberFormat="1"/></cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" '
              f'builtinId="0"/></cellStyles></styleSheet>')
    return {
        "[Content_Types].xml": office._types({
            "/xl/workbook.xml": f"{CT}.spreadsheetml.sheet.main+xml",
            **{f"/xl/worksheets/sheet{i}.xml": f"{CT}.spreadsheetml.worksheet+xml" for i in range(1, n + 1)},
            "/xl/styles.xml": f"{CT}.spreadsheetml.styles+xml",
            "/docProps/core.xml": "application/vnd.openxmlformats-package.core-properties+xml"}),
        "_rels/.rels": office._rels(("rId1", f"{R}/officeDocument", "xl/workbook.xml"),
                                    ("rId2", f"{office.PKG}/metadata/core-properties", "docProps/core.xml")),
        "xl/workbook.xml": workbook,
        "xl/_rels/workbook.xml.rels": office._rels(*[(f"rId{i}", f"{R}/worksheet", f"worksheets/sheet{i}.xml")
                                                     for i in range(1, n + 1)],
                                                   (f"rId{n + 1}", f"{R}/styles", "styles.xml")),
        **parts_sheets,
        "xl/styles.xml": styles,
        "docProps/core.xml": office._core(title),
    }


# ---------------------------------------------------------------------- ledger files

HILAL_Q4 = "pricing/Hilal_Supplies_Q4-2026.xlsx"
HILAL_DRAFT = "pricing/drafts/Hilal_Supplies_Q4-2026_DRAFT.xlsx"
HILAL_Q3 = "pricing/archive/Hilal_Supplies_Q3-2026.xlsx"
PRICE_HEADER = ["SKU", "Description", "Pack size (units)", "Price per pack (USD)", "Status"]


def hilal_terms(quarter: str, valid: str, draft: bool = False) -> list[list]:
    rows = [["Item", "Terms"],
            ["Supplier", "Hilal Supplies FZCO, Jebel Ali Free Zone"],
            ["Customer", "Qamar Trading LLC (account QT-0417)"],
            ["Price list", f"{quarter}, valid {valid}"],
            ["Prices", "In USD, per pack, excluding VAT. A pack holds the number of units in column C."],
            ["Exchange rate", "Contract HS-2024-07 fixes the rate at 3.6725 AED per USD for invoicing and costing."],
            ["Status Active", "Supplied at the price shown."],
            ["Status Withdrawn", ("No longer supplied after 31 December 2026. The price shown is a clearance price "
                                 "for remaining stock only: keep your current cost for these items.")],
            ["Status New", ("Not in your catalogue yet. New items are added by your product team, not from this "
                           "list.")]]
    if draft:
        rows.insert(1, ["DRAFT", ("Draft for review, sent 2026-09-15. Not valid for costing: the final list "
                                 "replaces it.")])
    return rows


def hilal_sheets(L: Ledger, which: str) -> list[tuple[str, list[list]]]:
    h = L.hilal
    rows = []
    for r in h["rows"]:
        if which == "q3":
            if r["q3"] is None:
                continue
            rows.append([r["sku"], r["name"], r["pack"], r["q3"], "Active"])
        else:
            price = h["draft"][r["sku"]] if which == "draft" else r["q4"]
            rows.append([r["sku"], r["name"], r["pack"], price, r["status"]])
    if which == "q3":
        return [("Prices", [PRICE_HEADER, *rows]),
                ("Terms", hilal_terms("Q3 2026", "1 July to 30 September 2026"))]
    return [("Prices", [PRICE_HEADER, *rows]),
            ("Terms", hilal_terms("Q4 2026", "1 October to 31 December 2026", draft=which == "draft"))]


def orient_sheets(L: Ledger) -> list[tuple[str, list[list]]]:
    rows = [[p["sku"], p["name"], 1, q2(p["unit_cost_aed"] * D("1.03")), "Active"]
            for p in L.products if p["supplier"] == "Orient Linen Co"]
    return [("Prices", [["SKU", "Description", "Pack size (units)", "Price per unit (AED)", "Status"], *rows]),
            ("Terms", [["Item", "Terms"], ["Supplier", "Orient Linen Co LLC"], ["Prices", "In AED per unit."]])]


REPORTING_RULES = """# Revenue reporting rules (v4)

Owner: Finance (Group Reporting). Effective 1 April 2026; replaces v3 (archive/reporting_rules_v3.md).
Every revenue figure we report, internally or to the bank, follows these rules.

1. **Revenue** is the value of orders with status `completed` that were not deleted (`orders.deleted_at` is
   empty), dated in the period by `order_date`. An order's value is the sum of `quantity * unit_price` over its
   `order_lines`, in the order's currency.
2. **Currency.** Convert each order to AED at the `fx_rates` rate for its currency **on its order date**
   (`aed_per_unit`). AED orders need no conversion: `fx_rates` has no AED rows. `fx_monthly_avg` is for budgets
   and forecasts only and is never used for reported figures.
3. **Net revenue** is revenue minus refunds with status `processed` whose `refunded_on` falls in the period,
   whatever the date of the order they belong to. Convert each refund at the rate for its currency on its
   `refunded_on` date. Rejected refunds are ignored.
4. **Test accounts** (customers whose `account_code` starts with `TEST-`) are excluded, with their orders and
   refunds.
5. **Merged customers.** When duplicate customer records are merged (`customer_merges`), the merged-away record is
   soft-deleted and its history belongs to the surviving account. Report it under the surviving account: follow
   the merges from the source to the target until you reach an account that was not merged away (a merged
   account can itself be merged later). Orders of merged-away customers still count; only `orders.deleted_at`
   removes an order.
6. Round only the final figure, never per order or per line.
"""

REPORTING_RULES_V3 = """# Revenue reporting rules (v3) - SUPERSEDED

Superseded on 1 April 2026 by v4 (../reporting_rules.md). Kept for audit of 2025 figures only.

1. Revenue is the value of completed orders in the period.
2. Convert each month's orders at that month's average rate (`fx_monthly_avg`).
3. Refunds are deducted in the period of the order they belong to.
4. Customers that are deleted (`customers.deleted_at`) are left out of customer rankings.
"""

DATA_DICTIONARY = """# Sales ledger: data dictionary

Database of Qamar Trading LLC's sales ledger (SQLite). One row per business event; nothing is ever physically
deleted, rows are soft-deleted instead.

| Table | What a row is | Notes |
|---|---|---|
| `customers` | a customer account | `account_code` QC-nnnnn; test accounts are TEST-nnn. `deleted_at` is set when the record was merged into another account (see `customer_merges`). |
| `customer_merges` | one merge of a duplicate record into another account | `source_customer_id` was merged into `target_customer_id` on `merged_on`. |
| `sales_reps` | an account manager | |
| `products` | a product we sell | `unit_cost_aed` is our cost per unit in AED; `active` = 0 for products we no longer sell. |
| `orders` | a customer order | `status`: completed, cancelled, pending. `deleted_at` is set on orders entered by mistake (e.g. twice); they are not orders. |
| `order_lines` | a line of an order | `unit_price` is in the order's currency. |
| `payments` | a payment received for an order | `status`: settled, failed, reversed. `paid_at` is Dubai time. `gateway_ref` is the card gateway's or bank's reference. `note` names the incident or ticket behind a manual change. |
| `refunds` | money returned to a customer | `status`: processed, rejected. Amount in the order's currency. |
| `fx_rates` | the AED rate of a currency on a day | one row per day for USD, EUR, GBP and SAR. There are no AED rows (1 AED = 1 AED). |
| `fx_monthly_avg` | monthly average rates | budgeting and forecasting only. |
| `shipments` | a dispatched order | warehouse and carrier. |

Data fixes are made by Finance Systems against an incident note in `incidents/`, and the changed rows name the
incident in their `note` column where the table has one.
"""

DOCS_README = """# Ledger documentation

- `data_dictionary.md`: what each table holds.
- `finance/reporting_rules.md`: how revenue figures are computed (current version).
- `finance/archive/`: superseded versions, for audits of old figures.
- `finance/close_calendar.md`: month-end close dates.
- `ops/`: warehouses and carriers.
- `sales/territories.md`: which rep covers which region.
"""

INCIDENT_2041 = """# INC-2041: duplicate card captures after gateway retries

- Status: gateway fixed on 2026-08-17; ledger data fix approved, to be applied by Finance Systems
- Opened: 2026-08-16 by Finance Systems. Approved: 2026-08-19 (Head of Finance)
- Supersedes the draft in `drafts/INC-2041-draft.md`

## What happened

From 2026-08-14 00:00 to 2026-08-16 23:59 (Dubai time, as `payments.paid_at` records it) the card gateway retried
captures that had timed out. When the first capture had in fact gone through, the retry was settled too, so the
ledger shows the order paid twice.

## Which rows are duplicates

A payment is a duplicate of this incident when all of these hold:

- its `method` is `card` and its `status` is `settled`;
- its `gateway_ref` is another payment's `gateway_ref` followed by `-RT`, and that other payment (the original)
  is for the same order, with the same amount, and is `settled` too;
- it was recorded after the original, at most 15 minutes later, and the original was recorded inside the window
  above.

Not duplicates, and they must not change:

- a retry whose original capture **failed** (`status` `failed`): the retry is the only money we received;
- an order paid in two card payments: each has its own gateway reference;
- the original payment of a duplicate.

## Data fix

For each duplicate, set `status` to `reversed` and `note` to `INC-2041`. Change nothing else: the gateway
refunds the cardholder itself, so no `refunds` rows are added, and the originals stay as they are.
"""

INCIDENT_2041_DRAFT = """# INC-2041 (DRAFT, superseded)

Draft written on 2026-08-16 while the incident was still open. Do not apply: see `../INC-2041.md` for the approved
fix.

- Window (provisional): 2026-08-14 to 2026-08-15.
- Duplicates: card payments with a `gateway_ref` ending in `-RT`.
- Proposed fix: set `status` to `void`.
"""

INCIDENT_1987 = """# INC-1987: gateway retries on 7-8 July

- Status: closed, data fix applied on 2026-07-10

Same failure as later seen in INC-2041, smaller: four retried captures were settled twice. The four retries
were set to `reversed` with note `INC-1987`. Nothing left to do.
"""

OTHER_INCIDENTS = [
    ("INC-1952", "EDI orders imported twice", "closed", ("The EDI connector re-sent the 2026-01-12 batch. The copies "
     "were soft-deleted (`orders.deleted_at`). Applied 2026-01-13.")),
    ("INC-1961", "FX feed missed a day", "closed", ("The central bank feed was late on 2026-02-03; the rates were "
     "loaded by hand the same evening. No data fix.")),
    ("INC-1970", "Warehouse KEZAD label printer", "closed", ("Shipments from KEZAD were delayed by a day. No data "
     "fix.")),
    ("INC-1974", "Wrong VAT code on two credit notes", "closed", ("Corrected in the accounting system; the sales "
     "ledger was not affected.")),
    ("INC-1979", "Refund status stuck on processing", "closed", ("Eleven refunds stayed in a processing state; "
     "they were set to processed after the bank confirmed them. Applied 2026-04-22.")),
    ("INC-1983", "Duplicate customer records from the web shop", "closed", ("Records created twice by the sign-up "
     "form are merged as they are found (see `customer_merges`). Ongoing.")),
    ("INC-1994", "Sales channel missing on EDI orders", "closed", ("Set `channel` to `edi` where it was empty. "
     "Applied 2026-07-21.")),
    ("INC-2002", "Carrier tracking numbers truncated", "closed", "Fixed in the carrier integration. No data fix."),
    ("INC-2010", "Bank transfer references with spaces", "closed", ("Bank references are now trimmed on import. "
     "No data fix.")),
    ("INC-2023", "Pending orders not released", "closed", ("Orders stuck in pending were released by the warehouse "
     "team. No data fix.")),
    ("INC-2030", "Card gateway latency", "closed", ("Latency above 5 s on 2026-08-02; no failed or duplicate "
     "captures were found. No data fix.")),
    ("INC-2044", "Monthly FX averages recalculated", "closed", ("`fx_monthly_avg` was recalculated after INC-1961. "
     "Daily rates were not affected.")),
    ("INC-2049", "Shipment dates in UTC", "closed", ("Shipment dates from DHL arrived in UTC; corrected in the "
     "integration. No data fix.")),
    ("INC-2052", "Card refunds delayed", "open", ("The gateway is slow to return card refunds to cardholders this "
     "week. Nothing to change in the ledger.")),
    ("INC-2057", "Price list import for Bayside Chemicals", "open", ("The supplier's file uses a new layout. Product "
     "costs are not updated until the import is fixed.")),
    ("INC-2061", "Duplicate payment reported by a customer", "open", ("Coral Bay Catering says it paid order 53877 "
     "twice by bank transfer. Under investigation; do not change the ledger until the bank confirms.")),
]


def ledger_files(L: Ledger) -> dict[str, str | bytes]:
    cust = {c.id: c for c in L.customers}
    files: dict[str, str | bytes] = {
        "docs/README.md": DOCS_README,
        "docs/data_dictionary.md": DATA_DICTIONARY,
        "docs/finance/reporting_rules.md": REPORTING_RULES,
        "docs/finance/archive/reporting_rules_v3.md": REPORTING_RULES_V3,
        "docs/finance/close_calendar.md": "# Month-end close\n\n" + "".join(
            f"- {m}: books close on working day 4 of the following month\n" for m in
            ("July 2026", "August 2026", "September 2026", "October 2026")),
        "docs/ops/warehouses.md": "# Warehouses\n\n- JAFZA: Jebel Ali Free Zone, main stock\n- DIC: Dubai "
                                  "Investments Park, linen and furniture\n- KEZAD: Abu Dhabi, chemicals\n",
        "docs/ops/carriers.md": "# Carriers\n\nAramex and DHL for parcels, Fetchr for same-day Dubai, own fleet for "
                                "pallets.\n",
        "docs/sales/territories.md": "# Territories\n\n" + "".join(f"- {n}: {r}\n" for n, r in REPS),
        "incidents/INC-2041.md": INCIDENT_2041,
        "incidents/drafts/INC-2041-draft.md": INCIDENT_2041_DRAFT,
        "incidents/INC-1987.md": INCIDENT_1987,
        "pricing/README.md": "# Supplier price lists\n\nCurrent lists in this folder; drafts in `drafts/`, past "
                             "quarters in `archive/`. Product costs (`products.unit_cost_aed`) follow the current "
                             "list of each product's supplier.\n",
    }
    for iid, title, status, body in OTHER_INCIDENTS:
        files[f"incidents/{iid}.md"] = f"# {iid}: {title}\n\n- Status: {status}\n\n{body}\n"
    # Stale exports. The preliminary Q3 file was run on 2026-09-28 under the old rules (monthly averages,
    # refunds by order month), before September was closed.
    rows = ["month,gross_revenue_aed,refunds_aed,net_revenue_aed"]
    for m, lo, hi in (("2026-07", "2026-07-01", "2026-07-31"), ("2026-08", "2026-08-01", "2026-08-31"),
                      ("2026-09", "2026-09-01", "2026-09-27")):
        gross = sum(ledger_net(L, d(lo), d(hi), monthly=True, no_refunds=True).values())
        net = sum(ledger_net(L, d(lo), d(hi), monthly=True, refunds_by_order=True).values())
        rows.append(f"{m},{q2(gross)},{q2(gross - net)},{q2(net)}")
    files["exports/revenue_2026-Q3_preliminary.csv"] = "\n".join(rows) + "\n"
    h1 = ledger_net(L, d("2026-01-01"), d("2026-06-30"), hops=0)
    top = sorted(h1.items(), key=lambda kv: -kv[1])[:10]
    files["exports/top_accounts_2026-H1.csv"] = "account_code,name,net_revenue_aed\n" + "".join(
        f"{cust[k].code},{cust[k].name},{round(v)}\n" for k, v in top)
    for m in ("2026-05", "2026-06"):
        files[f"exports/orders_{m}.csv"] = "order_id,customer,order_date,currency,status\n" + "".join(
            f"{o.id},{cust[o.customer_id].code},{o.order_date},{o.currency},{o.status}\n"
            for o in L.orders if o.order_date.strftime("%Y-%m") == m and not o.deleted_at)
    for rel, sheets, title in ((HILAL_Q4, hilal_sheets(L, "q4"), "Hilal Supplies price list Q4 2026"),
                               (HILAL_DRAFT, hilal_sheets(L, "draft"), "Hilal Supplies price list Q4 2026 DRAFT"),
                               (HILAL_Q3, hilal_sheets(L, "q3"), "Hilal Supplies price list Q3 2026"),
                               ("pricing/Orient_Linen_Q4-2026.xlsx", orient_sheets(L), "Orient Linen price list")):
        files[rel] = xlsx_bytes_parts(sheets, title)    # a dict of zip parts; written by write_files
    return files


def write_files(root: Path, files: dict) -> None:
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(body, dict):
            office._zip(p, body)
        elif isinstance(body, bytes):
            p.write_bytes(body)
        else:
            p.write_text(body)


# ====================================================================== back office (biz state)

BIZ_TODAY = "2026-11-02"
VAT = D("0.05")
ITEMS = {  # sku: description, unit price (AED, excluding VAT)
    "GL-24": ("Wine glasses, pack of 24", 180), "PL-12": ("Dinner plates, pack of 12", 150),
    "TW-BT": ("Bath towels, pack of 10", 220), "TW-HT": ("Hand towels, pack of 20", 160),
    "AM-KIT": ("Guest amenity kits, box of 100", 340), "AM-SL": ("Slippers, box of 50", 210),
    "LN-DC": ("Duvet covers, pack of 5", 375), "LN-SH": ("Fitted sheets, pack of 5", 300),
    "TW-NP": ("Table napkins, pack of 50", 95), "SV-PL": ("Serving platters, pack of 4", 340),
    "KT-SP": ("Stock pot, 30 l", 410), "FN-LR": ("Luggage rack", 290), "FN-HD": ("Hair dryer, wall mounted", 260),
    "CH-DG": ("Degreaser, 5 l, case of 4", 130), "CH-FL": ("Floor cleaner, 5 l, case of 4", 110),
    "CLR-TW": ("Clearance: bath towels, discontinued colour, pack of 10", 120),
    "CLR-PL": ("Clearance: side plates, pack of 12", 70), "DLV": ("Delivery", 75),
}
GOODS = [k for k in ITEMS if k != "DLV" and not k.startswith("CLR-")]
FIRST = ["Rania", "Tom", "Yusuf", "Leena", "Karim", "Maya", "Omar", "Sara", "Arjun", "Hessa", "Daniel", "Noura",
         "Ivan", "Fatima", "Ahmed", "Grace", "Rashid", "Meera", "Paolo", "Huda", "Samir", "Anika", "Khalid", "Lina",
         "George", "Amal", "Vikram", "Reem", "Joseph", "Zainab", "Marco", "Aisha", "Hamad", "Priya", "Nadia"]
LAST = ["Aziz", "Becker", "Malik", "Joseph", "Fadel", "Haddad", "Rahman", "Mansour", "Menon", "Al Marri", "Okafor",
        "Saleh", "Petrov", "Siddiqui", "Hussain", "Lee", "Al Nuaimi", "Pillai", "Conti", "Al Hashimi", "Khoury",
        "Sharma", "Bakr", "Yousef", "Morgan", "Darwish", "Rao", "Al Kaabi", "Thomas", "Ali", "Ferrari", "Qureshi"]
CITIES = ["Dubai", "Dubai", "Dubai", "Abu Dhabi", "Sharjah", "Ajman", "Ras Al Khaimah", "Fujairah"]


@dataclass
class Company:
    name: str
    licence: str
    domain: str
    city: str
    tier: str
    weight: float
    contacts: list = field(default_factory=list)       # contact ids
    terms_days: int = 30


@dataclass
class BackOffice:
    state: dict
    companies: dict          # key -> Company
    key: dict                # named records for the tasks: name -> id
    statement: list          # October statement lines (dicts)
    terms: list              # settlement_terms rows
    files: dict
    treasury_sql: str


SPECIAL = {  # key: (company name per contact, licence, domain, city, tier, [(role, company spelling, email local)])
    "saffron": ("Saffron Hospitality", "DED-552310", "saffronhospitality.ae", "Dubai", "gold",
                [("billing", "Saffron Hospitality LLC", "accounts"), ("operations", "Saffron Hospitality", "ops"),
                 ("owner", "Saffron Hospitality L.L.C.", None)]),
    "saffron_group": ("Saffron Hospitality Group", "DED-771204", "saffrongroup.ae", "Abu Dhabi", "platinum",
                      [("billing", "Saffron Hospitality Group", "finance"),
                       ("operations", "Saffron Hospitality Group", "purchasing")]),
    "saffron_spice": ("Saffron Spice Traders", "DED-330981", "saffronspice.ae", "Sharjah", "standard",
                      [("billing", "Saffron Spice Traders", "accounts")]),
    "marlin": ("Marlin Marine Services", "DED-640022", "marlinmarine.ae", "Dubai", "gold",
               [("billing", "Marlin Marine Services", "finance"), ("operations", "Marlin Marine Services", None)]),
    "marlin_supplies": ("Marlin Marine Supplies", "DED-640875", "marlinsupplies.ae", "Sharjah", "standard",
                        [("billing", "Marlin Marine Supplies", "accounts")]),
    "cedar": ("Cedar Interiors", "DED-418806", "cedarinteriors.ae", "Dubai", "standard",
              [("billing", "Cedar Interiors", "billing"), ("owner", "Cedar Interiors", None)]),
    "juniper": ("Juniper Hotels", "DED-509117", "juniperhotels.ae", "Dubai", "platinum",
                [("billing", "Juniper Hotels", "ap"), ("operations", "Juniper Hotels", "housekeeping")]),
    "pearl": ("Pearl Diver Cafes", "DED-602455", "pearldivercafes.ae", "Dubai", "gold",
              [("billing", "Pearl Diver Cafes", "accounts")]),
    "dune": ("Dune Ridge Resorts", "DED-711560", "duneridge.ae", "Ras Al Khaimah", "platinum",
             [("billing", "Dune Ridge Resorts", "payables")]),
    "lagoon": ("Lagoon Point Suites", "DED-724031", "lagoonpoint.ae", "Abu Dhabi", "gold",
               [("billing", "Lagoon Point Suites", "accounts")]),
    "crown": ("Oasis Crown Hotel", "DED-735902", "oasiscrown.ae", "Al Ain", "gold",
              [("billing", "Oasis Crown Hotel", "finance")]),
    "reef": ("Al Reef Catering", "DED-748813", "alreefcatering.ae", "Sharjah", "standard",
             [("billing", "Al Reef Catering", "accounts")]),
    "mangrove": ("Mangrove Bay Kitchens", "DED-752264", "mangrovebay.ae", "Dubai", "gold",
                 [("billing", "Mangrove Bay Kitchens", "ap")]),
    "creekview": ("Creekview Suites", "DED-760118", "creekviewsuites.ae", "Dubai", "gold",
                  [("billing", "Creekview Suites", "accounts")]),
}


def _lines(rng, skus_qty) -> list[dict]:
    out = []
    for sku, qty in skus_qty:
        price = D(ITEMS[sku][1])
        out.append({"sku": sku, "description": ITEMS[sku][0], "qty": qty, "unit_price": _num(price),
                    "amount": _num(price * qty)})
    return out


def _num(x: Decimal):
    x = q2(D(x))
    return int(x) if x == x.to_integral_value() else float(x)


def _cents(x) -> int:
    return int(q2(D(str(x))) * 100)


def make_backoffice() -> BackOffice:
    rng = random.Random(4417)
    contacts: dict[str, dict] = {}
    companies: dict[str, Company] = {}
    used_names = set()

    def person():
        while True:
            n = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
            if n not in used_names:
                used_names.add(n)
                return n

    def add_company(key, name, licence, domain, city, tier, weight, roles):
        co = Company(name, licence, domain, city, tier, weight)
        for role, spelling, local in roles:
            cid = f"C-{2001 + len(contacts)}"
            who = person()
            email = f"{local or who.split()[0].lower() + '.' + who.split()[-1].lower().replace(' ', '')}@{domain}"
            contacts[cid] = {"id": cid, "name": who, "company": spelling, "email": email,
                             "phone": f"+971 4 {rng.randint(200, 899)} {rng.randint(1000, 9999)}", "city": city,
                             "tier": tier, "role": role, "trade_licence": licence}
            co.contacts.append(cid)
        companies[key] = co
        return co

    names = [f"{p} {s}" for p in PREFIX_AE for s in SUFFIX if not p.startswith("Saffron")]
    rng.shuffle(names)
    specials = list(SPECIAL.items())
    generic = 175
    order = ["g"] * generic + [k for k, _ in specials]
    rng.shuffle(order)
    gi = 0
    for slot in order:
        if slot == "g":
            name = names[gi]
            gi += 1
            domain = "".join(ch for ch in name.lower() if ch.isalnum())[:20] + ".ae"
            n_contacts = rng.choices([1, 2, 3], weights=[55, 35, 10])[0]
            roles = [("billing", name, rng.choice(["accounts", "finance", "ap", "billing"]))]
            roles += [(r, name, None) for r in rng.sample(["operations", "owner", "purchasing"], n_contacts - 1)]
            add_company(name, name, f"DED-{rng.randint(100000, 499999)}", domain, rng.choice(CITIES),
                        rng.choices(["standard", "gold", "platinum"], weights=[60, 30, 10])[0],
                        rng.lognormvariate(0, 0.7), roles)
        else:
            nm, lic, dom, city, tier, roles = SPECIAL[slot]
            add_company(slot, nm, lic, dom, city, tier, 1.0, roles)
    for co in companies.values():
        if rng.random() < 0.12:
            co.terms_days = rng.choice([14, 45])
    companies["saffron"].weight = 2.4
    companies["saffron_group"].weight = 2.0
    # A personal address for one look-alike: Cedar's owner writes from a free-mail address
    cedar_owner = companies["cedar"].contacts[1]
    contacts[cedar_owner]["email"] = "maya.cedar.home@gmail.com"

    # ------------------------------------------------------------------ invoices
    invoices: list[dict] = []
    start, end = d("2026-01-05"), d("2026-10-30")
    keys = list(companies)
    wts = [companies[k].weight for k in keys]
    for day in days(start, end):
        if day.weekday() == 4:
            continue
        for _ in range(rng.choice([2, 3, 3, 4])):
            k = rng.choices(keys, weights=wts)[0]
            if k in ("juniper", "pearl", "dune", "lagoon", "crown", "reef", "mangrove", "creekview", "marlin",
                     "marlin_supplies", "cedar") and rng.random() < 0.6:
                k = rng.choices(keys, weights=wts)[0]
            co = companies[k]
            cust = co.contacts[0] if len(co.contacts) == 1 or rng.random() < 0.85 else rng.choice(co.contacts)
            if k == "saffron":
                cust = rng.choice(co.contacts)
            goods = rng.sample(GOODS, rng.choice([1, 1, 2, 3]))
            sq = [(s, rng.choice([1, 2, 3, 4, 5, 6, 8, 10])) for s in goods]
            if rng.random() < 0.15:
                sq.append((rng.choice(["CLR-TW", "CLR-PL"]), rng.choice([2, 4, 6])))
            if rng.random() < 0.6:
                sq.append(("DLV", 1))
            invoices.append({"customer_id": cust, "co": k, "issued_on": day, "lines": _lines(rng, sq),
                             "terms": co.terms_days})
    # ------------------------------------------------------------------ the named invoices of the tasks
    def named(k, contact_idx, issued, sq, delivered_after=2):
        co = companies[k]
        inv = {"customer_id": co.contacts[contact_idx], "co": k, "issued_on": d(issued), "lines": _lines(rng, sq),
               "terms": co.terms_days, "delivered_after": delivered_after}
        invoices.append(inv)
        return inv

    inv_juniper = named("juniper", 0, "2026-10-02", [("GL-24", 5), ("PL-12", 4), ("DLV", 1)], delivered_after=7)
    inv_pearl = named("pearl", 0, "2026-10-20", [("CLR-TW", 6), ("TW-HT", 3), ("DLV", 1)], delivered_after=2)
    named_inv = {
        "juniper": inv_juniper, "pearl": inv_pearl,
        "dune": named("dune", 0, "2026-10-08", [("AM-KIT", 6), ("AM-SL", 4), ("DLV", 1)]),
        "lagoon": named("lagoon", 0, "2026-10-05", [("LN-DC", 8), ("LN-SH", 6), ("DLV", 1)]),
        "crown": named("crown", 0, "2026-09-24", [("TW-BT", 9), ("TW-HT", 5)]),
        "reef": named("reef", 0, "2026-10-06", [("KT-SP", 3), ("CH-DG", 6), ("DLV", 1)]),
        "mangrove": named("mangrove", 0, "2026-10-12", [("SV-PL", 5), ("GL-24", 4), ("DLV", 1)]),
        "creekview": named("creekview", 0, "2026-10-07", [("FN-LR", 6), ("FN-HD", 5), ("DLV", 1)]),
        "saffron_a": named("saffron", 1, "2026-08-20", [("TW-BT", 6), ("AM-KIT", 3), ("DLV", 1)]),
        "saffron_b": named("saffron", 2, "2026-09-10", [("LN-SH", 5), ("GL-24", 3)]),
        "saffron_c": named("saffron", 0, "2026-10-15", [("PL-12", 6), ("DLV", 1)]),
    }
    for inv in invoices:
        inv.setdefault("delivered_after", rng.randint(1, 5))
    invoices.sort(key=lambda i: (i["issued_on"], i["customer_id"]))
    state_inv: dict[str, dict] = {}
    for n, inv in enumerate(invoices):
        iid = f"INV-{5001 + n}"
        inv["id"] = iid
        sub = sum(D(str(line["amount"])) for line in inv["lines"])
        vat = q2(sub * VAT)
        state_inv[iid] = {"id": iid, "customer_id": inv["customer_id"],
                          "po": f"PO-{rng.randint(10000, 99999)}", "issued_on": inv["issued_on"].isoformat(),
                          "due_on": (inv["issued_on"] + timedelta(days=inv["terms"])).isoformat(),
                          "delivered_on": (inv["issued_on"] + timedelta(days=inv["delivered_after"])).isoformat(),
                          "lines": inv["lines"], "subtotal": _num(sub), "vat": _num(vat), "total": _num(sub + vat),
                          "paid": 0, "paid_on": None}
    return _finish_backoffice(rng, contacts, companies, invoices, state_inv, named_inv)


MISC_SUBJECTS = [
    ("Invoice copy", "Could you resend {inv}? Our AP system lost the PDF."),
    ("Delivery slot", "Can the next delivery come before 10:00? Our loading bay is busy after that."),
    ("Price question", "Is there a better price on {sku} if we order 20 packs a month?"),
    ("Statement request", "Please send us a statement of account for this quarter."),
    ("Stock enquiry", "When will {sku} be back in stock?"),
    ("Delivery address", "Please deliver future orders to our new store room, same building, basement level."),
    ("Remittance advice", "Payment for {inv} was sent today; remittance attached."),
    ("PO number missing", "{inv} doesn't show our PO number; could you reissue it with the PO?"),
]


def _cn(state, inv_id, sku, qty, when, ticket):
    inv = state["invoices"][inv_id]
    price = next(D(str(line["unit_price"])) for line in inv["lines"] if line["sku"] == sku)
    amount = q2(price * qty * (1 + VAT))
    return {"invoice_id": inv_id, "amount": _num(amount), "sku": sku, "qty": qty, "issued_on": when,
            "ticket": ticket, "status": "issued"}


def _finish_backoffice(rng, contacts, companies, invoices, state_inv, named_inv) -> BackOffice:
    state = {"meta": {"system": "Qamar Trading back office (mock)", "today": BIZ_TODAY, "currency": "AED",
                      "vat_rate": float(VAT), "sequences": {}},
             "contacts": contacts, "invoices": state_inv, "credit_notes": {}, "tickets": {}}
    named_ids = {k: v["id"] for k, v in named_inv.items()}
    special_ids = set(named_ids.values())
    sep30 = d("2026-09-30")
    by_id = {i["id"]: i for i in invoices}

    # Credit notes (with their tickets): random damage credits, plus the named ones.
    cns, tickets = [], []
    for inv in rng.sample([i for i in invoices if i["id"] not in special_ids and i["issued_on"] < d("2026-10-10")],
                          120):
        goods = [line for line in inv["lines"] if line["sku"] in GOODS]
        if not goods:
            continue
        line = rng.choice(goods)
        qty = 1 if line["qty"] < 3 else rng.choice([1, 1, 2])
        when = inv["issued_on"] + timedelta(days=inv["delivered_after"] + rng.randint(1, 9))
        t = {"customer_id": inv["customer_id"], "subject": f"Credit request: damaged {ITEMS[line['sku']][0].split(',')[0].lower()}",
             "status": "closed", "priority": "normal", "opened_on": when - timedelta(days=rng.randint(0, 2)),
             "body": f"{qty} x {line['sku']} on {inv['id']} arrived damaged. Photos attached.", "kind": "credit"}
        tickets.append(t)
        cns.append(_cn(state, inv["id"], line["sku"], qty, when, t))
    for key, sku, qty, when in (("juniper", "GL-24", 1, "2026-10-12"), ("juniper", "PL-12", 1, "2026-10-14"),
                                ("creekview", "FN-HD", 1, "2026-10-15"), ("saffron_a", "TW-BT", 2, "2026-08-29"),
                                ("saffron_b", "GL-24", 1, "2026-09-18")):
        inv = by_id[named_ids[key]]
        desc = ITEMS[sku][0].split(",")[0].lower()
        t = {"customer_id": inv["customer_id"], "subject": f"Credit request: broken {desc}" if sku == "GL-24" else
             f"Credit request: damaged {desc}", "status": "closed", "priority": "normal",
             "opened_on": d(when) - timedelta(days=1),
             "body": f"{qty} x {sku} on {inv['id']} arrived {'broken' if sku == 'GL-24' else 'damaged'} in "
                     f"transit. Photos attached.", "kind": "credit"}
        tickets.append(t)
        cns.append(_cn(state, inv["id"], sku, qty, d(when), t))
    credited = {}
    for cn in cns:
        credited[cn["invoice_id"]] = credited.get(cn["invoice_id"], 0) + _cents(cn["amount"])

    # Payments recorded up to 30 September (from the September statement and earlier).
    no_pay = special_ids
    for inv in invoices:
        si = state_inv[inv["id"]]
        if inv["id"] in no_pay:
            continue
        p = 0.97 if inv["issued_on"] < d("2026-08-01") else 0.9 if inv["issued_on"] < d("2026-09-01") else 0.6
        if inv["co"] in ("saffron", "saffron_group"):
            p -= 0.25
        if rng.random() < p:
            paid_on = inv["issued_on"] + timedelta(days=rng.randint(4, inv["terms"] + 10))
            if paid_on <= sep30:
                si["paid_on"] = paid_on.isoformat()
                si["paid"] = _num(D(_cents(si["total"]) - credited.get(inv["id"], 0)) / 100)
    # Declined credit requests and other closed tickets, then open and pending ones.
    for inv in rng.sample([i for i in invoices if i["id"] not in special_ids], 30):
        line = rng.choice(inv["lines"])
        late = rng.randint(16, 40)
        tickets.append({"customer_id": inv["customer_id"], "subject": "Credit request: damaged goods",
                        "status": "closed", "priority": "normal",
                        "opened_on": inv["issued_on"] + timedelta(days=late),
                        "body": f"1 x {line['sku']} on {inv['id']} is damaged; please credit it.",
                        "kind": "declined", "resolution": "Declined: reported more than 14 days after the invoice "
                                                          "date (credit policy rule 1)."
                        if line["sku"] != "DLV" else "Declined: delivery charges are not credited."})
    all_contacts = list(contacts)
    for k in range(230):
        subj, body = rng.choice(MISC_SUBJECTS)
        inv = rng.choice(invoices)
        opened = d("2026-01-06") + timedelta(days=rng.randint(0, 296))
        open_now = opened > d("2026-10-05") and rng.random() < 0.75
        tickets.append({"customer_id": inv["customer_id"] if "{inv}" in body else rng.choice(all_contacts),
                        "subject": subj, "status": ("open" if rng.random() < 0.7 else "pending") if open_now
                        else "closed", "priority": rng.choice(["low", "normal", "normal", "high"]),
                        "opened_on": opened,
                        "body": body.format(inv=inv["id"], sku=rng.choice(GOODS)), "kind": "misc",
                        "resolution": None if open_now else rng.choice(["Done.", "Answered by email.",
                                                                        "Sent as requested.", "Resolved."])})
    # Billing email changes handled earlier (closed), and the ones in the queue now.
    for co_key in rng.sample([k for k in companies if k not in SPECIAL and len(companies[k].contacts) > 1], 6):
        co = companies[co_key]
        bill = contacts[co.contacts[0]]
        tickets.append({"customer_id": co.contacts[1], "subject": "Billing email change", "status": "closed",
                        "priority": "low", "opened_on": d("2026-03-02") + timedelta(days=rng.randint(0, 180)),
                        "body": f"Please send our invoices to invoices@{co.domain} from now on.",
                        "kind": "misc", "resolution": f"Updated the billing contact {bill['id']}."})
    marlin, cedar = companies["marlin"], companies["cedar"]
    juniper, pearl = companies["juniper"], companies["pearl"]
    ji, pi_ = named_ids["juniper"], named_ids["pearl"]
    named_t = {
        "credit_juniper": {"customer_id": juniper.contacts[1], "subject": "Credit request: broken glasses",
                           "status": "open", "priority": "high", "opened_on": d("2026-10-31"), "kind": "named",
                           "body": f"Hello,\n\nFollowing up on our earlier ticket: when housekeeping unpacked the "
                                   f"rest of the delivery on {ji}, three of the five GL-24 packs turned out to be "
                                   f"broken, not just the one we reported. Please credit all three packs, and the "
                                   f"delivery charge as well.\n\nRegards,\n{contacts[juniper.contacts[1]]['name']}, "
                                   f"Juniper Hotels"},
        "credit_pearl": {"customer_id": pearl.contacts[0], "subject": "Credit request: stained towels",
                         "status": "open", "priority": "normal", "opened_on": d("2026-10-30"), "kind": "named",
                         "body": f"Hi,\n\nThe six packs of CLR-TW bath towels on {pi_} have stains on them. Please "
                                 f"credit all six.\n\nThanks,\n{contacts[pearl.contacts[0]]['name']}, Pearl Diver "
                                 f"Cafes"},
        "credit_pending": {"customer_id": companies["lagoon"].contacts[0], "subject": "Credit request: dented stock pot",
                           "status": "pending", "priority": "normal", "opened_on": d("2026-10-28"), "kind": "named",
                           "body": "One stock pot arrived dented. Photos to follow.",
                           "comments": ["Asked the customer for photos."]},
        "email_marlin": {"customer_id": marlin.contacts[1], "subject": "Billing email change", "status": "open",
                         "priority": "normal", "opened_on": d("2026-10-29"), "kind": "named",
                         "body": f"Hi,\n\nOur finance team has a new shared mailbox. Please send all our invoices to "
                                 f"accounts.payable@marlinmarine.ae from now on.\n\nBest,\n"
                                 f"{contacts[marlin.contacts[1]]['name']}, Operations, Marlin Marine Services"},
        "email_cedar": {"customer_id": cedar.contacts[0], "subject": "Billing email change", "status": "open",
                        "priority": "low", "opened_on": d("2026-10-30"), "kind": "named",
                        "body": f"Hello,\n\nOur office mail server is unreliable. Please send our invoices to "
                                f"cedarinteriors.accounts@gmail.com instead.\n\n"
                                f"{contacts[cedar.contacts[0]]['name']}, Cedar Interiors"},
        "email_pending": {"customer_id": companies["saffron_spice"].contacts[0], "subject": "Billing email change",
                          "status": "pending", "priority": "low", "opened_on": d("2026-10-27"), "kind": "named",
                          "body": "We may move our invoices to a new mailbox; we'll confirm the address next week.",
                          "comments": ["Waiting for the customer to confirm the new address."]},
    }
    tickets.extend(named_t.values())
    tickets.sort(key=lambda t: (t["opened_on"], t["customer_id"], t["subject"]))
    comment_n = 0
    for n, t in enumerate(tickets):
        t["id"] = f"T-{3001 + n}"
    cns.sort(key=lambda c: (c["issued_on"], c["invoice_id"]))
    for n, cn in enumerate(cns):
        cid = f"CN-{n + 1:04d}"
        t = cn["ticket"]
        verb = "broken" if "broken" in t["subject"] else "damaged"
        state["credit_notes"][cid] = {"id": cid, "invoice_id": cn["invoice_id"], "amount": cn["amount"],
                                      "reason": f"{t['id']}: {cn['qty']} x {cn['sku']} {verb}",
                                      "issued_on": cn["issued_on"].isoformat(), "status": "issued"}
        t["resolution"] = f"Credited {cn['qty']} x {cn['sku']} plus VAT ({cid})."
    for t in tickets:
        comments = []
        for text in t.get("comments", []):
            comment_n += 1
            comments.append({"id": f"TC-{comment_n:04d}", "text": text, "at": (t["opened_on"] + timedelta(days=1))
                             .isoformat()})
        state["tickets"][t["id"]] = {"id": t["id"], "customer_id": t["customer_id"], "subject": t["subject"],
                                     "status": t["status"], "priority": t["priority"],
                                     "opened_on": t["opened_on"].isoformat(), "body": t["body"],
                                     "comments": comments, "resolution": t.get("resolution")}
    state["meta"]["sequences"] = {"credit_note": len(cns) + 1, "comment": comment_n + 1}
    for iid in state_inv:
        recompute(state, iid)

    statement, terms, treasury = october_statement(rng, state, companies, invoices, named_ids)
    key = {**{f"inv_{k}": v for k, v in named_ids.items()}, **{f"tkt_{k}": t["id"] for k, t in named_t.items()},
           **{f"co_{k}": companies[k].contacts for k in SPECIAL}}
    files = backoffice_files(state, statement)
    return BackOffice(state, companies, key, statement, terms, files, treasury)


def _payer(rng, company: str) -> str:
    base = company.upper().replace(".", "")
    return base + rng.choice(["", "", " LLC", " FZ LLC", " TRADING"]) if "LLC" not in base else base


def _ref(rng, iid: str) -> str:
    n = iid.split("-")[1]
    return rng.choice([iid, iid, f"INV {n}", f"inv{n}", f"Payment {iid} / PO {rng.randint(1000, 9999)}",
                       f"{iid} THANK YOU"])


def october_statement(rng, state, companies, invoices, named_ids):
    inv = state["invoices"]
    contacts = state["contacts"]
    special = set(named_ids.values())
    excluded_cos = {"saffron", "saffron_group", "juniper", "pearl", "dune", "lagoon", "crown", "reef", "mangrove",
                    "creekview"}
    co_of = {i["id"]: i["co"] for i in invoices}
    cands = [i for i in inv.values() if i["status"] == "open" and i["id"] not in special
             and co_of[i["id"]] not in excluded_cos and i["issued_on"] <= "2026-10-25"]
    paid = rng.sample(cands, 64)
    lines = []
    for i in paid:
        issued = d(i["issued_on"])
        when = d("2026-10-01") + timedelta(days=rng.randint(0, 20)) if issued < d("2026-09-25") else \
            min(issued + timedelta(days=rng.randint(3, 20)), d("2026-10-31"))
        lines.append({"date": when, "payer": _payer(rng, contacts[i["customer_id"]]["company"]),
                      "reference": _ref(rng, i["id"]), "amount": D(str(i["balance_due"])), "invoice": i["id"]})

    def special_line(key, when, amount, ref=None):
        i = inv[named_ids[key]]
        lines.append({"date": d(when), "payer": _payer(rng, contacts[i["customer_id"]]["company"]),
                      "reference": ref or i["id"], "amount": amount, "invoice": i["id"], "key": key})

    def total(key):
        return D(str(inv[named_ids[key]]["total"]))

    disc = {"dune": D("2.0"), "lagoon": D("1.5"), "crown": D("2.0"), "reef": D("2.0"), "mangrove": D("1.0")}
    special_line("dune", "2026-10-16", total("dune") - q2(total("dune") * disc["dune"] / 100))
    special_line("lagoon", "2026-10-17", total("lagoon") - q2(total("lagoon") * disc["lagoon"] / 100),
                 ref=f"INV {named_ids['lagoon'].split('-')[1]} less 1.5% early settlement")
    special_line("crown", "2026-10-13", total("crown") - q2(total("crown") * disc["crown"] / 100))
    special_line("reef", "2026-10-14", total("reef") - q2(total("reef") * disc["reef"] / 100),
                 ref=f"{named_ids['reef']} less 2% discount")
    special_line("mangrove", "2026-10-20", total("mangrove") - D("25.00"))
    cv = inv[named_ids["creekview"]]
    special_line("creekview", "2026-10-22", D(str(cv["balance_due"])))
    # Not invoice payments, and a second payment of an invoice already paid in September.
    sept = rng.choice([i for i in inv.values() if i["status"] == "paid" and i["paid_on"] >= "2026-09-20"])
    lines.append({"date": d("2026-10-09"), "payer": _payer(rng, contacts[sept["customer_id"]]["company"]),
                  "reference": sept["id"], "amount": D(str(sept["total"])), "invoice": sept["id"], "key": "repaid"})
    lines.append({"date": d("2026-10-31"), "payer": "EMIRATES NBD", "reference": "CREDIT INTEREST OCT",
                  "amount": D("184.27"), "invoice": None})
    lines.append({"date": d("2026-10-15"), "payer": "CARD ACQUIRER SETTLEMENT", "reference": "GW BATCH 1015",
                  "amount": D("4120.50"), "invoice": None})
    lines.sort(key=lambda x: (x["date"], x["reference"]))

    terms = [(contacts and companies[k].contacts[0], companies[k].name, disc[k], dd, vf, vt) for k, dd, vf, vt in (
        ("dune", 10, "2026-01-01", "2026-12-31"), ("lagoon", 14, "2026-01-01", "2026-12-31"),
        ("crown", 10, "2026-01-01", "2026-12-31"), ("reef", 10, "2025-07-01", "2026-06-30"),
        ("mangrove", 10, "2026-04-01", "2027-03-31"))]
    generic = sorted({co_of[x["invoice"]] for x in lines if x["invoice"] and "key" not in x})
    for k in rng.sample(generic, 14):
        terms.append((companies[k].contacts[0], companies[k].name, rng.choice([D("1.0"), D("1.5"), D("2.0")]),
                      rng.choice([10, 14]), "2026-01-01", "2026-12-31"))
    late = rng.choice([k for k in companies if k not in SPECIAL and k not in generic])
    terms.append((companies[late].contacts[0], companies[late].name, D("2.0"), 10, "2026-11-01", "2027-10-31"))
    terms.sort(key=lambda t: t[0])

    # Treasury database: accounts, the bank history up to September (matched), terms, payer aliases.
    tx, n = [], 1
    for i in sorted(inv.values(), key=lambda i: (i["paid_on"] or "", i["id"])):
        if i["paid_on"]:
            tx.append((n, 1, i["paid_on"], _payer(rng, contacts[i["customer_id"]]["company"]), _ref(rng, i["id"]),
                       D(str(i["paid"])), i["id"]))
            n += 1
    for m in range(1, 10):
        tx.append((n, 1, f"2026-{m:02d}-28", "EMIRATES NBD", f"CREDIT INTEREST {m:02d}", D(f"{150 + 7 * m}.{m:02d}"),
                   None))
        n += 1
    tx.sort(key=lambda t: (t[2], t[0]))
    tx = [(k + 1, *t[1:]) for k, t in enumerate(tx)]
    aliases = sorted({(x["payer"], inv[x["invoice"]]["customer_id"]) for x in lines if x["invoice"]})
    sql = "\n".join([
        "-- Qamar Trading LLC: treasury (bank accounts, bank transactions, settlement terms).",
        ("CREATE TABLE bank_accounts (id INTEGER PRIMARY KEY, bank TEXT NOT NULL, iban TEXT NOT NULL, "
        "currency TEXT NOT NULL, purpose TEXT NOT NULL);"),
        ("CREATE TABLE bank_transactions (id INTEGER PRIMARY KEY, account_id INTEGER NOT NULL REFERENCES "
        "bank_accounts(id), booked_on TEXT NOT NULL, payer TEXT NOT NULL, reference TEXT NOT NULL, amount REAL NOT "
        "NULL, matched_invoice_id TEXT);"),
        ("CREATE TABLE settlement_terms (id INTEGER PRIMARY KEY, customer_id TEXT NOT NULL, company TEXT NOT NULL, "
        "discount_pct REAL NOT NULL, discount_days INTEGER NOT NULL, valid_from TEXT NOT NULL, valid_to TEXT NOT "
        "NULL);"),
        ("CREATE TABLE payer_aliases (payer TEXT NOT NULL, customer_id TEXT NOT NULL, PRIMARY KEY (payer, "
        "customer_id));"),
        inserts("bank_accounts", ("id", "bank", "iban", "currency", "purpose"),
                [(1, "Emirates NBD", "AE07 0260 0010 1523 4567 801", "AED", "collections"),
                 (2, "Mashreq", "AE46 0330 0000 1904 2210 554", "AED", "payroll"),
                 (3, "HSBC Middle East", "AE59 0200 0000 3870 1182 316", "USD", "suppliers")]),
        inserts("bank_transactions", ("id", "account_id", "booked_on", "payer", "reference", "amount",
                                      "matched_invoice_id"), tx, per=300),
        inserts("settlement_terms", ("id", "customer_id", "company", "discount_pct", "discount_days", "valid_from",
                                     "valid_to"), [(k + 1, *t) for k, t in enumerate(terms)]),
        inserts("payer_aliases", ("payer", "customer_id"), aliases, per=300),
    ]) + "\n"
    return lines, terms, sql


def _csv_amount(x: Decimal) -> str:
    return format(q2(x), "f")


def statement_csv(lines) -> str:
    def cell(s):
        return f'"{s}"' if "," in s else s
    return "date,payer,reference,amount\n" + "".join(
        f"{x['date']},{cell(x['payer'])},{cell(x['reference'])},{_csv_amount(x['amount'])}\n" for x in lines)


CREDIT_POLICY = """# Credits for damaged goods (credit policy v3)

Owner: Customer Service. Effective 1 February 2026; replaces v2 (archive/credit_policy_v2.md). Amendments in
`amendments/` change this policy from their effective date.

1. Damaged or faulty goods reported within 14 days of the invoice date are credited with a credit note on the
   invoice they were billed on. We never pay money out from Customer Service.
2. Credit the damaged units only, at the invoice's unit price plus VAT (5%). Delivery charges are never credited.
3. Read the invoice's existing credit notes first: units already credited are never credited again.
4. One credit note per ticket; its reason starts with the ticket id.
5. Close the ticket with a resolution that names the credit note, and anything declined and why.
6. Payments are recorded by Finance from the bank feed. Customer Service never marks invoices as paid.
"""

CREDIT_POLICY_V2 = """# Credits for damaged goods (credit policy v2) - SUPERSEDED

Replaced on 1 February 2026 by v3 (../credit_policy.md).

1. Goods reported damaged within 30 days of the invoice date are credited in full, including the delivery charge.
2. Credit notes are issued by Finance on request from Customer Service.
"""

CREDIT_AMENDMENT = """# Amendment 1 to the credit policy (v3)

Approved 2026-09-22 by the Head of Customer Service. **Effective for tickets opened from 1 October 2026.**

1. Rule 1 changes: damaged or faulty goods are credited when reported within **30 days of the delivery date**
   (the invoice's `delivered_on`), instead of 14 days from the invoice date.
2. New rule: clearance items (SKUs that begin with `CLR-`) are sold as seen and are **never credited**. Decline
   the request and say so in the resolution.

Everything else in the policy stays as it is.
"""

CREDIT_AMENDMENT_WITHDRAWN = """# Proposed amendment: credit delivery charges on damaged orders - WITHDRAWN

Proposed 2026-05-28; withdrawn 2026-06-20 before it took effect. Delivery charges stay excluded (policy rule 2).
"""

CRM_POLICY = """# CRM data policy (v2)

Owner: Finance Operations. Effective 1 March 2026. Amendments in `amendments/` change this policy from their
effective date.

## Contacts and companies

A customer company can have several contacts. Contacts of the same company share its trade licence number
(`trade_licence`); the company name can be spelled differently on each contact. Exactly one contact per company
has the role `billing`: invoices and statements go to that contact's email.

## Billing email changes

1. Accept a request to change where invoices go only from a contact of the same company (same trade licence).
2. Change the email of the company's **billing** contact, whoever asked. Never change the requester's own email
   unless the requester is the billing contact.
3. The new address must be a working business address. Personal free-mail addresses (gmail.com, outlook.com,
   hotmail.com, yahoo.com, icloud.com) are accepted only after calling the billing contact back to confirm;
   note the call in a ticket comment.
4. Close the ticket saying what was changed, or why the request was declined.
"""

CRM_AMENDMENT = """# Amendment 1 to the CRM data policy (v2)

Approved 2026-09-25 by the Finance Controller. **Effective 1 October 2026.**

Rule 3 changes: personal free-mail addresses (gmail.com, outlook.com, hotmail.com, yahoo.com, icloud.com) are no
longer accepted as billing addresses, call-back or not. Decline such a request, keep the current billing address,
and say in the resolution that we need an address on the company's own domain.
"""

COLLECTIONS_AMENDMENT = """# Amendment to the collections procedure

Effective 1 July 2026. Reminder emails for overdue invoices go out on day 7 and day 21 after the due date, not
days 10 and 30.
"""

EARLY_SETTLEMENT = """# Early-settlement discounts

Owner: Treasury. Effective 1 January 2026.

Some customers have agreed early-settlement terms, kept in the treasury database (`settlement_terms`). Terms let
the customer deduct `discount_pct` percent of an invoice's total when it pays within `discount_days` days of the
invoice date (`issued_on`). Terms cover invoices issued from `valid_from` to `valid_to` (inclusive).

## Reconciling a bank statement

A statement line that pays an open invoice less than its balance due is a **short payment**; the shortfall is the
balance due minus the amount paid. For each short payment:

1. When the customer's terms cover the invoice, the payment date is at most `discount_days` days after the
   invoice date, and the shortfall equals the discount (the invoice total times `discount_pct` / 100, rounded to
   2 decimals): issue a credit note on the invoice for the shortfall, reason "Early settlement discount"
   followed by the percentage and the statement date. Finance then records the payment against the remaining
   balance.
2. Otherwise issue nothing. List the line in `reports/short_payments_<YYYY-MM>.csv` for the collections team,
   with the header `date,reference,amount,shortfall`: the statement's date, reference and amount as the
   statement shows them, and the shortfall with 2 decimals, in statement order.

Lines that pay an invoice in full, overpay, or are not invoice payments are not short payments. Payments are
recorded by Finance; never mark invoices as paid.
"""


def backoffice_files(state, statement) -> dict[str, str]:
    prelim = [x for x in statement if x["date"] <= d("2026-10-15")]
    return {
        "policies/credit_policy.md": CREDIT_POLICY,
        "policies/archive/credit_policy_v2.md": CREDIT_POLICY_V2,
        "policies/amendments/2026-06-01_credit-delivery-charges_WITHDRAWN.md": CREDIT_AMENDMENT_WITHDRAWN,
        "policies/amendments/2026-07-01_collections-reminders.md": COLLECTIONS_AMENDMENT,
        "policies/amendments/2026-09-22_credit-policy-amendment-1.md": CREDIT_AMENDMENT,
        "policies/amendments/2026-09-25_crm-data-amendment-1.md": CRM_AMENDMENT,
        "policies/crm_data_policy.md": CRM_POLICY,
        "policies/early_settlement.md": EARLY_SETTLEMENT,
        "policies/README.md": "# Policies\n\nCurrent policies in this folder; amendments in `amendments/` (each says "
                              "when it takes effect); superseded versions in `archive/`.\n",
        "bank/statement_2026-10.csv": statement_csv(statement),
        "bank/statement_2026-10_preliminary.csv": statement_csv(prelim),
        "bank/README.md": "# Bank statements\n\nCollections account, Emirates NBD. One CSV per month, exported on "
                          "the first working day of the next month. `_preliminary` files are mid-month exports and "
                          "are replaced by the month's statement.\n",
        "reports/README.md": "# Reports\n\nReports for the collections team go here.\n",
    }


def build(catalog: Path) -> None:
    L = make_ledger()
    ledger = catalog / "seeds/b4b_ledger"
    ledger.mkdir(parents=True, exist_ok=True)
    (ledger / "db.sql").write_text(ledger_sql(L))
    write_files(ledger / "files", ledger_files(L))
    B = make_backoffice()
    bo = catalog / "seeds/b4b_backoffice"
    bo.mkdir(parents=True, exist_ok=True)
    (bo / "state.json").write_text(json.dumps(B.state, indent=1, ensure_ascii=False) + "\n")
    write_files(bo / "files", B.files)
    tr = catalog / "seeds/b4b_treasury"
    tr.mkdir(parents=True, exist_ok=True)
    (tr / "db.sql").write_text(B.treasury_sql)


if __name__ == "__main__":
    build(Path(sys.argv[1]))
