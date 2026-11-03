"""Writes catalog/seeds/biz: the business state (state.json) the biz server works on, and the policy files.

Invoice totals, credited amounts, balances and statuses are computed here (with the server's own `recompute`),
never typed by hand, so the seed is always consistent with what the server would have written."""

import json
import sys
from pathlib import Path

from harness_factory.env.mcp.impl.biz_server import recompute

TODAY = "2026-10-20"
VAT = 0.05

CONTACTS = [  # id, name, company, email, phone, city, tier
    ("C-1001", "Layla Haddad", "Al Noor Trading", "accounts@alnoor.ae", "+971 4 330 1101", "Dubai", "gold"),
    ("C-1002", "Omar Siddiqui", "Harbor Logistics", "finance@harborlog.ae", "+971 4 881 2290", "Dubai", "standard"),
    ("C-1003", "Priya Nair", "Falcon Systems", "billing@falconsys.ae", "+971 4 450 7712", "Dubai", "platinum"),
    ("C-1004", "Hassan Karimi", "Oasis Foods", "ap@oasisfoods.ae", "+971 6 554 3010", "Sharjah", "standard"),
    ("C-1005", "Mariam Al Suwaidi", "Creek Analytics", "hello@creekanalytics.ae", "+971 4 227 9034", "Dubai",
     "gold"),
    ("C-1006", "Daniel Okafor", "Marina Fitness", "admin@marinafit.ae", "+971 4 399 6620", "Dubai", "standard"),
    ("C-1007", "Sara Mansour", "Desert Rose Studio", "orders@desertrose.ae", "+971 2 641 5508", "Abu Dhabi",
     "standard"),
    ("C-1008", "Ravi Menon", "Palm Pharmacy", "purchasing@palmpharmacy.ae", "+971 6 742 1187", "Ajman", "gold"),
    ("C-1009", "Fatima Rahman", "Oasis Foods Trading", "accounts@oasisfoodstrading.ae", "+971 9 222 4471",
     "Fujairah", "standard"),
    ("C-1010", "QA Robot", "QA Sandbox", "qa1@example.test", "+971 4 000 0000", "Dubai", "standard"),
    ("C-1011", "Khalid Bakr", "Oasis Foods", "khalid.bakr@oasisfoods.ae", "+971 6 554 3022", "Sharjah",
     "standard"),
    ("C-1012", "Noor Saleh", "Gulf Print House", "jobs@gulfprint.ae", "+971 4 266 0193", "Dubai", "standard"),
]

ITEMS = {  # sku: description, unit price
    "YM-10": ("Yoga mat, 6 mm", 150.0),
    "RB-05": ("Resistance band set", 90.0),
    "FR-01": ("Foam roller", 70.0),
    "KB-12": ("Kettlebell, 12 kg", 210.0),
    "DLV": ("Delivery", 60.0),
    "OS-L": ("Office suite licence, 1 year", 400.0),
    "TN-5": ("Printer toner, 5-pack", 375.0),
    "CF-B": ("Coffee beans, 5 kg", 250.0),
    "SP-R": ("Spice rack refill", 110.0),
    "PK-M": ("Packaging materials", 200.0),
    "CS-H": ("Consulting, per hour", 550.0),
}

INVOICES = [  # id, customer, po, issued_on, due_on, [(sku, qty)], paid_on
    ("INV-1031", "C-1004", "OF-2207", "2026-08-10", "2026-09-09", [("CF-B", 8)], "2026-09-05"),
    ("INV-1032", "C-1001", "ANT-551", "2026-08-14", "2026-09-13", [("OS-L", 3), ("DLV", 1)], "2026-09-10"),
    ("INV-1033", "C-1003", "FS-0981", "2026-08-21", "2026-09-20", [("CS-H", 6)], "2026-09-18"),
    ("INV-1034", "C-1008", "PP-3340", "2026-08-28", "2026-09-27", [("PK-M", 4), ("DLV", 1)], None),
    ("INV-1035", "C-1012", "GPH-77", "2026-08-30", "2026-09-29", [("TN-5", 2)], "2026-09-25"),
    ("INV-1036", "C-1004", "OF-2231", "2026-09-01", "2026-10-01", [("CF-B", 6)], None),
    ("INV-1037", "C-1006", "PO-7702", "2026-09-03", "2026-10-03", [("FR-01", 10), ("DLV", 1)], "2026-09-30"),
    ("INV-1038", "C-1002", "HL-9020", "2026-09-08", "2026-10-08", [("PK-M", 7)], None),
    ("INV-1039", "C-1011", "OF-2240", "2026-09-12", "2026-10-12", [("SP-R", 5), ("PK-M", 1)], None),
    ("INV-1040", "C-1009", "OFT-118", "2026-09-15", "2026-10-15", [("CF-B", 8), ("SP-R", 2)], None),
    ("INV-1041", "C-1005", "CA-4410", "2026-09-29", "2026-10-29", [("CS-H", 4)], None),
    ("INV-1042", "C-1006", "PO-7731", "2026-10-08", "2026-11-07", [("YM-10", 10), ("RB-05", 5), ("DLV", 1)], None),
    ("INV-1043", "C-1010", "QA-0001", "2026-10-09", "2026-11-08", [("OS-L", 1)], None),
    ("INV-1044", "C-1004", "OF-2252", "2026-10-05", "2026-11-04", [("SP-R", 5), ("CF-B", 1)], None),
]

CREDIT_NOTES = [  # id, invoice, amount, reason, issued_on
    ("CN-0001", "INV-1033", 577.5, "T-2019: one consulting hour billed twice", "2026-09-02"),
    ("CN-0002", "INV-1036", 525.0, "T-2027: two bags of coffee beans arrived spoiled", "2026-09-10"),
    ("CN-0003", "INV-1042", 157.5, "T-2033: 1 yoga mat (YM-10) arrived torn", "2026-10-12"),
]

TICKETS = [  # id, customer, subject, status, priority, opened_on, body, comments, resolution
    ("T-2019", "C-1003", "Consulting hour billed twice", "closed", "normal", "2026-08-30",
     "INV-1033 lists six consulting hours but our timesheet shows five. Please correct it.\n\nPriya, Falcon Systems",
     [], "Credited one hour plus VAT (CN-0001)."),
    ("T-2027", "C-1004", "Spoiled coffee beans", "closed", "high", "2026-09-09",
     "Two of the six bags on INV-1036 arrived spoiled. Photos attached.\n\nHassan, Oasis Foods",
     [], "Credited two bags plus VAT (CN-0002)."),
    ("T-2033", "C-1006", "Torn yoga mat", "closed", "normal", "2026-10-11",
     "One of the yoga mats delivered with INV-1042 has a tear along the edge.\n\nDaniel, Marina Fitness",
     [{"id": "TC-0001", "text": "Photo checks out; crediting one mat.", "at": "2026-10-12"}],
     "Credited 1 yoga mat plus VAT (CN-0003)."),
    ("T-2036", "C-1002", "Invoice copy", "pending", "low", "2026-10-13",
     "Could you resend INV-1038? Our AP system lost the PDF.\n\nOmar, Harbor Logistics",
     [{"id": "TC-0002", "text": "Asked Finance for a fresh PDF.", "at": "2026-10-14"}], None),
    ("T-2038", "C-1008", "Packaging overdue reminder", "open", "normal", "2026-10-15",
     "We got a reminder for INV-1034. We'll pay this week; no action needed.\n\nRavi, Palm Pharmacy", [], None),
    ("T-2040", "C-1005", "New billing contact", "open", "low", "2026-10-17",
     "Please send future invoices to billing@creekanalytics.ae.\n\nMariam, Creek Analytics", [], None),
    ("T-2041", "C-1006", "Torn yoga mats and delivery charge", "open", "high", "2026-10-18",
     ("Hi,\n\nTwo of the yoga mats from INV-1042 arrived torn: the one we reported last week and another one we "
      "found when we unpacked the rest. Please refund both, and the delivery charge too, since the parcel was "
      "clearly damaged in transit.\n\nThanks,\nDaniel, Marina Fitness"), [], None),
    ("T-2042", "C-1012", "Toner colour", "open", "low", "2026-10-19",
     "Is the 5-pack toner available in cyan only?\n\nNoor, Gulf Print House", [], None),
]

FILES = {
    "docs/refund_policy.md": """# Refunds and credits (support)

How the support team handles requests for refunds and credits.

1. Damaged or faulty goods reported within 14 days of the invoice date are credited with a credit note on the
   invoice they were billed on. Support never pays out money itself.
2. Credit the damaged units only, at the unit price on the invoice plus VAT (5%). Delivery charges are not
   credited.
3. Check the invoice's existing credit notes first: an item that was already credited is never credited again.
4. Issue one credit note per ticket; its reason starts with the ticket id.
5. Close the ticket with a resolution that names the credit note and anything declined.
6. Payments are recorded by Finance from the bank feed. Support never marks invoices as paid.
""",
}


def cents(x):
    return round(x * 100)


def money(c):
    v = c / 100
    return int(v) if v == int(v) else v


def build() -> dict:
    state = {
        "meta": {"system": "Back office (mock)", "today": TODAY, "currency": "AED", "vat_rate": VAT,
                 "sequences": {"credit_note": len(CREDIT_NOTES) + 1,
                               "comment": 1 + sum(len(t[7]) for t in TICKETS)}},
        "contacts": {c[0]: dict(zip(("id", "name", "company", "email", "phone", "city", "tier"), c, strict=True))
                     for c in CONTACTS},
        "invoices": {},
        "credit_notes": {},
        "tickets": {},
    }
    for iid, cust, po, issued, due, lines, paid_on in INVOICES:
        rows = [{"sku": sku, "description": ITEMS[sku][0], "qty": qty, "unit_price": money(cents(ITEMS[sku][1])),
                 "amount": money(round(qty * cents(ITEMS[sku][1])))} for sku, qty in lines]
        sub = sum(cents(r["amount"]) for r in rows)
        vat = round(sub * VAT)
        state["invoices"][iid] = {"id": iid, "customer_id": cust, "po": po, "issued_on": issued, "due_on": due,
                                  "lines": rows, "subtotal": money(sub), "vat": money(vat), "total": money(sub + vat),
                                  "paid": money(sub + vat) if paid_on else 0, "paid_on": paid_on}
    for cid, iid, amount, reason, issued in CREDIT_NOTES:
        state["credit_notes"][cid] = {"id": cid, "invoice_id": iid, "amount": amount, "reason": reason,
                                      "issued_on": issued, "status": "issued"}
    for iid in state["invoices"]:
        recompute(state, iid)
    for tid, cust, subject, status, prio, opened, body, comments, resolution in TICKETS:
        state["tickets"][tid] = {"id": tid, "customer_id": cust, "subject": subject, "status": status,
                                 "priority": prio, "opened_on": opened, "body": body, "comments": comments,
                                 "resolution": resolution}
    return state


if __name__ == "__main__":
    root = Path(sys.argv[1]) / "seeds/biz"
    root.mkdir(parents=True, exist_ok=True)
    (root / "state.json").write_text(json.dumps(build(), indent=1, ensure_ascii=False) + "\n")
    for rel, body in FILES.items():
        p = root / "files" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
