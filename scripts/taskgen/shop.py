"""Builds catalog/seeds/shop/db.sql. Every number is hand-placed so each trap gives a different answer."""

CUSTOMERS = [
    (1, "Al Noor Trading", "accounts@alnoor.ae", "Dubai", "2024-02-11"),
    (2, "Harbor Logistics", "finance@harborlog.ae", "Abu Dhabi", "2024-05-03"),
    (3, "Falcon Systems", "billing@falconsys.ae", "Dubai", "2025-03-02"),
    (4, "Oasis Foods", "ap@oasisfoods.ae", "Sharjah", "2024-09-19"),
    (5, "Creek Analytics", "hello@creekanalytics.ae", "Dubai", "2025-01-27"),
    (6, "Desert Rose Interiors", "orders@desertrose.ae", "Ajman", "2025-06-08"),
    (7, "Marina Fitness", "admin@marinafit.ae", "Dubai", "2025-07-21"),
    (8, "Palm Pharmacy", "purchasing@palmpharmacy.ae", "Abu Dhabi", "2025-10-02"),
    (9, "Gulf Print House", "jobs@gulfprint.ae", "Sharjah", "2026-01-15"),
    (10, "Sandstone Cafe", "owner@sandstonecafe.ae", "Dubai", "2026-03-09"),
    (11, "Falcon Systems", "accounts@falconsys.ae", "Dubai", "2026-08-14"),      # duplicate of 3
    (12, "QA Test Account", "qa1@example.test", "Dubai", "2026-02-01"),
    (13, "Load Test Bot", "load@example.test", "Dubai", "2026-02-01"),
]

# id, sku, name, category; current price comes from CURRENT below
PRODUCTS = [
    (1, "KB-100", "Wireless Keyboard", "accessories"),
    (2, "MS-210", "Ergonomic Mouse", "accessories"),
    (3, "HD-330", "USB-C Hub", "accessories"),
    (4, "LT-900", "Laptop Stand", "accessories"),
    (5, "MN-27", "27-inch Monitor", "displays"),
    (6, "MN-32", "32-inch Monitor", "displays"),
    (7, "DK-500", "Standing Desk", "furniture"),
    (8, "CH-750", "Office Chair", "furniture"),
    (9, "SW-OFF", "Office Suite License", "software"),
    (10, "SW-SEC", "Security Suite License", "software"),
]

PRICE_HISTORY = [  # product_id, price, effective_on
    (1, 120.00, "2025-01-01"), (1, 135.00, "2026-04-01"),
    (2, 80.00, "2025-01-01"), (2, 85.00, "2026-12-01"),          # scheduled, not yet in effect
    (3, 150.00, "2025-01-01"), (3, 140.00, "2026-07-01"),        # price went DOWN: max(price) is wrong
    (4, 95.00, "2025-06-01"), (4, 110.00, "2026-02-01"),
    (5, 1100.00, "2025-01-01"), (5, 1050.00, "2026-05-01"),
    (6, 1650.00, "2025-01-01"),
    (7, 1900.00, "2025-01-01"),
    (8, 780.00, "2025-01-01"), (8, 820.00, "2026-03-01"),
    (9, 400.00, "2025-01-01"),
    (10, 250.00, "2025-01-01"), (10, 300.00, "2026-09-01"),
]
IN_EFFECT = {1: 135.0, 2: 80.0, 3: 140.0, 4: 110.0, 5: 1050.0, 6: 1650.0, 7: 1900.0, 8: 820.0, 9: 400.0, 10: 300.0}
# the bug: the accessories price list was applied twice
CURRENT = {p: (v * 2 if PRODUCTS[p - 1][3] == "accessories" else v) for p, v in IN_EFFECT.items()}

ORDERS = [  # id, customer, ordered_on, status, [(product, qty, unit_price)]
    (2001, 1, "2025-11-20", "paid", [(5, 2, 1100)]),
    (2002, 2, "2025-12-05", "paid", [(7, 3, 1900)]),
    (2003, 3, "2026-01-12", "paid", [(1, 5, 120), (2, 5, 80)]),
    (2004, 4, "2026-01-25", "paid", [(9, 10, 400)]),
    (2005, 5, "2026-02-03", "cancelled", [(6, 2, 1650)]),
    (2006, 7, "2026-02-17", "paid", [(7, 4, 1900), (8, 4, 780)]),
    (2007, 2, "2026-03-02", "paid", [(5, 3, 1100), (3, 3, 150)]),
    (2008, 12, "2026-03-05", "paid", [(1, 1, 120)]),
    (2009, 6, "2026-03-18", "paid", [(8, 6, 820)]),
    (2010, 1, "2026-04-07", "paid", [(6, 1, 1650), (4, 2, 110)]),
    (2011, 9, "2026-04-22", "pending", [(10, 5, 250)]),
    (2012, 2, "2026-05-09", "paid", [(7, 2, 1900), (1, 2, 135)]),
    (2013, 13, "2026-05-11", "paid", [(2, 3, 80)]),
    (2014, 8, "2026-05-30", "paid", [(9, 6, 400), (10, 6, 250)]),
    (2015, 10, "2026-06-14", "paid", [(5, 1, 1050), (2, 1, 80)]),
    (2016, 7, "2026-06-28", "refunded", [(6, 2, 1650)]),
    (2017, 4, "2026-07-03", "paid", [(3, 4, 140), (4, 4, 110)]),
    (2018, 3, "2026-07-15", "paid", [(9, 8, 400)]),
    (2019, 2, "2026-07-29", "paid", [(6, 2, 1650), (2, 2, 80)]),
    (2020, 5, "2026-08-04", "paid", [(1, 3, 135), (3, 3, 140)]),
    (2021, 11, "2026-08-20", "paid", [(5, 2, 1050)]),
    (2022, 7, "2026-08-26", "paid", [(8, 2, 820), (4, 2, 110)]),
    (2023, 6, "2026-09-03", "paid", [(10, 4, 300)]),
    (2024, 1, "2026-09-10", "cancelled", [(7, 1, 1900)]),
    (2025, 9, "2026-09-19", "paid", [(2, 6, 80), (1, 6, 135)]),
    (2026, 12, "2026-09-22", "paid", [(3, 1, 140)]),
    (2027, 11, "2026-09-30", "paid", [(4, 3, 110)]),
    (2028, 2, "2026-10-01", "paid", [(10, 3, 300)]),
    (2029, 4, "2026-10-04", "paid", [(5, 1, 1050), (1, 1, 135)]),
    (2030, 8, "2026-10-08", "pending", [(6, 1, 1650)]),
    (2031, 10, "2026-10-12", "paid", [(2, 4, 80), (3, 2, 140)]),
    (2032, 7, "2026-10-15", "cancelled", [(9, 3, 400)]),
    (2033, 5, "2026-10-19", "paid", [(8, 1, 820)]),
    (2034, 3, "2026-10-21", "paid", [(1, 2, 135)]),
    (2035, 13, "2026-10-22", "paid", [(4, 1, 110)]),
    (2036, 6, "2026-10-24", "paid", [(7, 1, 1900)]),
    (2037, 11, "2026-10-26", "pending", [(2, 2, 80)]),
]

REFUNDS = [  # id, order_id, amount, refunded_on, reason
    (1, 2006, 6000.00, "2026-03-01", "chairs returned"),
    (2, 2014, 300.00, "2026-06-10", "license overcharge"),
    (3, 2019, 160.00, "2026-08-11", "damaged item"),
]


def q(v):
    return "'" + v.replace("'", "''") + "'" if isinstance(v, str) else (f"{v:.2f}" if isinstance(v, float) else str(v))


def rows(values):
    return ",\n".join("  (" + ", ".join(q(x) for x in r) + ")" for r in values) + ";\n"


def build() -> str:
    out = ["-- Shop seed: customers, a product catalog with price history, orders, items and refunds.\n"]
    out.append("CREATE TABLE customers (\n  id INTEGER PRIMARY KEY,\n  name TEXT NOT NULL,\n  email TEXT NOT NULL,\n"
               "  city TEXT NOT NULL,\n  created_on TEXT NOT NULL\n);\nINSERT INTO customers VALUES\n" + rows(CUSTOMERS))
    out.append("\nCREATE TABLE products (\n  id INTEGER PRIMARY KEY,\n  sku TEXT NOT NULL UNIQUE,\n  name TEXT NOT NULL,\n"
               "  category TEXT NOT NULL,\n  price REAL NOT NULL\n);\nINSERT INTO products VALUES\n"
               + rows([(*p, float(CURRENT[p[0]])) for p in PRODUCTS]))
    out.append("\nCREATE TABLE price_history (\n  product_id INTEGER NOT NULL REFERENCES products(id),\n"
               "  price REAL NOT NULL,\n  effective_on TEXT NOT NULL\n);\nINSERT INTO price_history VALUES\n"
               + rows(PRICE_HISTORY))
    out.append("\nCREATE TABLE orders (\n  id INTEGER PRIMARY KEY,\n  customer_id INTEGER NOT NULL REFERENCES customers(id),\n"
               "  ordered_on TEXT NOT NULL,\n  status TEXT NOT NULL CHECK (status IN ('pending', 'paid', 'cancelled', 'refunded'))\n"
               ");\nINSERT INTO orders VALUES\n" + rows([o[:4] for o in ORDERS]))
    out.append("\nCREATE TABLE order_items (\n  order_id INTEGER NOT NULL REFERENCES orders(id),\n"
               "  product_id INTEGER NOT NULL REFERENCES products(id),\n  quantity INTEGER NOT NULL,\n"
               "  unit_price REAL NOT NULL\n);\nINSERT INTO order_items VALUES\n"
               + rows([(o[0], p, n, float(u)) for o in ORDERS for p, n, u in o[4]]))
    out.append("\nCREATE TABLE refunds (\n  id INTEGER PRIMARY KEY,\n  order_id INTEGER NOT NULL REFERENCES orders(id),\n"
               "  amount REAL NOT NULL,\n  refunded_on TEXT NOT NULL,\n  reason TEXT NOT NULL\n);\nINSERT INTO refunds VALUES\n"
               + rows(REFUNDS))
    return "".join(out)


if __name__ == "__main__":
    import sys
    from pathlib import Path
    Path(sys.argv[1]).write_text(build())
