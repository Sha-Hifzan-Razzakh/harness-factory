"""Batch 4 (author A): office-document seeds, written with the standard library like office.py (fixed zip
timestamps, stored entries, fixed PDF metadata), so every byte is reproducible.

- b4a-procure (b4-04): a purchase-order tracker (xlsx, ~1,100 lines) and 14 supplier quotations (PDF). One
  quotation is superseded by a revision, one is expired, one quotes per carton, and a look-alike supplier quotes
  the same part number.
- b4a-contracts (b4-05): six services agreements (docx) and a change-request register (xlsx, ~140 requests) with
  approved, withdrawn, pending and rejected requests, some for look-alike contracts.
"""

import datetime as dt
import random
import sys
from pathlib import Path
from xml.sax.saxutils import escape

sys.path.insert(0, str(Path(__file__).parent))
import office

S, R, W, CT = office.S, office.R, office.W, office.CT


# ------------------------------------------------------------------ xlsx / docx writers

def col(n: int) -> str:
    out = ""
    while n:
        n, r = divmod(n - 1, 26)
        out = chr(65 + r) + out
    return out


def _cell(ref: str, value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{escape(value)}</t></is></c>'
    if isinstance(value, float):
        return f'<c r="{ref}" s="1"><v>{value!r}</v></c>'           # 0.00 number format
    return f'<c r="{ref}"><v>{value}</v></c>'


def _sheet(rows: list[list]) -> str:
    cols = max(len(r) for r in rows)
    body = "".join(f'<row r="{n}">' + "".join(_cell(f"{col(k + 1)}{n}", v) for k, v in enumerate(row)) + "</row>"
                   for n, row in enumerate(rows, 1))
    return (f'{office.XML}<worksheet xmlns="{S}"><dimension ref="A1:{col(cols)}{len(rows)}"/><sheetData>{body}'
            f'</sheetData></worksheet>')


def write_xlsx(path: Path, sheets: list[tuple[str, list[list]]], title: str) -> None:
    n = len(sheets)
    workbook = (f'{office.XML}<workbook xmlns="{S}" xmlns:r="{R}"><sheets>'
                + "".join(f'<sheet name="{escape(name)}" sheetId="{i}" r:id="rId{i}"/>'
                          for i, (name, _) in enumerate(sheets, 1)) + "</sheets></workbook>")
    styles = (f'{office.XML}<styleSheet xmlns="{S}"><fonts count="1"><font><sz val="11"/><name val="Calibri"/>'
              f'</font></fonts><fills count="2"><fill><patternFill patternType="none"/></fill><fill>'
              f'<patternFill patternType="gray125"/></fill></fills><borders count="1"><border><left/><right/>'
              f'<top/><bottom/><diagonal/></border></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" '
              f'fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" '
              f'borderId="0" xfId="0"/><xf numFmtId="2" fontId="0" fillId="0" borderId="0" xfId="0" '
              f'applyNumberFormat="1"/></cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" '
              f'builtinId="0"/></cellStyles></styleSheet>')
    path.parent.mkdir(parents=True, exist_ok=True)
    office._zip(path, {
        "[Content_Types].xml": office._types({
            "/xl/workbook.xml": f"{CT}.spreadsheetml.sheet.main+xml",
            **{f"/xl/worksheets/sheet{i}.xml": f"{CT}.spreadsheetml.worksheet+xml" for i in range(1, n + 1)},
            "/xl/styles.xml": f"{CT}.spreadsheetml.styles+xml",
            "/docProps/core.xml": "application/vnd.openxmlformats-package.core-properties+xml"}),
        "_rels/.rels": office._rels(("rId1", f"{R}/officeDocument", "xl/workbook.xml"),
                                    ("rId2", f"{office.PKG}/metadata/core-properties", "docProps/core.xml")),
        "xl/workbook.xml": workbook,
        "xl/_rels/workbook.xml.rels": office._rels(
            *[(f"rId{i}", f"{R}/worksheet", f"worksheets/sheet{i}.xml") for i in range(1, n + 1)],
            (f"rId{n + 1}", f"{R}/styles", "styles.xml")),
        **{f"xl/worksheets/sheet{i}.xml": _sheet(rows) for i, (_, rows) in enumerate(sheets, 1)},
        "xl/styles.xml": styles,
        "docProps/core.xml": office._core(title),
    })


DOCX_STYLES = {"Normal": ("Normal", None), "Heading 1": ("Heading1", "heading 1"), "Heading 2": ("Heading2", "heading 2"),
               "Amendment Note": ("AmendmentNote", "Amendment Note")}


def write_docx(path: Path, paragraphs: list[tuple[str, str]], title: str) -> None:
    paras = "".join(f'<w:p><w:pPr><w:pStyle w:val="{DOCX_STYLES[style][0]}"/></w:pPr>{office._run(text, False)}</w:p>'
                    for style, text in paragraphs)
    document = (f'{office.XML}<w:document xmlns:w="{W}" xmlns:r="{R}"><w:body>{paras}<w:sectPr><w:pgSz w:w="11906" '
                f'w:h="16838"/><w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440" w:header="708" '
                f'w:footer="708" w:gutter="0"/></w:sectPr></w:body></w:document>')
    styles = (f'{office.XML}<w:styles xmlns:w="{W}"><w:docDefaults><w:rPrDefault><w:rPr><w:sz w:val="21"/></w:rPr>'
              f'</w:rPrDefault></w:docDefaults>'
              f'<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:qFormat/>'
              f'</w:style>'
              f'<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/>'
              f'<w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/><w:rPr><w:b/><w:sz w:val="32"/>'
              f'</w:rPr></w:style>'
              f'<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/>'
              f'<w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/><w:rPr><w:b/><w:sz w:val="26"/>'
              f'</w:rPr></w:style>'
              f'<w:style w:type="paragraph" w:customStyle="1" w:styleId="AmendmentNote"><w:name w:val="Amendment Note"/>'
              f'<w:basedOn w:val="Normal"/><w:qFormat/><w:rPr><w:i/><w:sz w:val="18"/></w:rPr></w:style></w:styles>')
    path.parent.mkdir(parents=True, exist_ok=True)
    office._zip(path, {
        "[Content_Types].xml": office._types({
            "/word/document.xml": f"{CT}.wordprocessingml.document.main+xml",
            "/word/styles.xml": f"{CT}.wordprocessingml.styles+xml",
            "/docProps/core.xml": "application/vnd.openxmlformats-package.core-properties+xml"}),
        "_rels/.rels": office._rels(("rId1", f"{R}/officeDocument", "word/document.xml"),
                                    ("rId2", f"{office.PKG}/metadata/core-properties", "docProps/core.xml")),
        "word/document.xml": document,
        "word/_rels/document.xml.rels": office._rels(("rId1", f"{R}/styles", "styles.xml")),
        "word/styles.xml": styles,
        "docProps/core.xml": office._core(title),
    })


# ------------------------------------------------------------------ b4a-procure

TRACKER = "procurement/po_tracker.xlsx"
HEADER = ["Line", "PO", "PO date", "Supplier", "SKU", "Description", "Qty", "Unit", "Unit price (AED)", "Status",
          "Quote"]
AWS, AWT = "Al Waha Industrial Supplies LLC", "Al Waha Industrial Trading LLC"
CPK, DPS = "Coastline Packaging LLC", "Desert Peak Safety Equipment LLC"
CATALOG = {     # supplier -> [(sku, description, unit, base price)]
    AWS: [("AW-3310", "Hydraulic hose 1/2in", "metre", 18.40), ("BRG-6204-2RS", "Bearing 6204-2RS", "each", 9.80),
          ("BRG-6205-2RS", "Bearing 6205-2RS", "each", 11.60), ("AW-1120", "Hose clamp 20-32mm", "each", 2.35),
          ("AW-4402", "Gear oil ISO VG 220, 20 L", "drum", 265.00), ("AW-2210", "V-belt B-52", "each", 24.90),
          ("AW-5508", "Grease cartridge 400 g", "each", 14.75), ("AW-7781", "Pneumatic fitting 8mm", "each", 6.20)],
    AWT: [("BRG-6204-2RS", "Bearing 6204-2RS", "each", 8.90), ("BRG-6206-2RS", "Bearing 6206-2RS", "each", 13.40),
          ("WT-1001", "Wire rope 10mm", "metre", 7.80), ("WT-2040", "Shackle 2 t", "each", 21.00)],
    CPK: [("CP-STRETCH-500", "Stretch film 500mm x 300m", "roll", 25.00), ("CP-TAPE-48", "Packing tape 48mm", "roll",
          3.10), ("CP-BOX-L", "Carton box large, double wall", "each", 4.85), ("CP-BUBBLE-1M", "Bubble wrap 1m x 100m",
          "roll", 68.00), ("CP-STRAP-12", "PP strapping 12mm", "coil", 92.00)],
    DPS: [("DP-GLV-NIT-L", "Nitrile gloves, large, box of 100", "box", 29.40), ("DP-HELM-W", "Safety helmet, white",
          "each", 18.50), ("DP-VEST-HV", "Hi-vis vest", "each", 9.25), ("DP-BOOT-42", "Safety boots size 42", "pair",
          145.00), ("DP-EAR-200", "Ear plugs, box of 200", "box", 56.00)],
    "Gulf Fasteners FZE": [("GF-M10-50", "Hex bolt M10 x 50, box of 100", "box", 48.00),
                           ("GF-M8-NUT", "Hex nut M8, box of 200", "box", 22.50),
                           ("GF-AN-12", "Anchor bolt M12", "each", 3.90)],
    "Marina Office Supply LLC": [("MO-A4-80", "A4 paper 80 g, box of 5 reams", "box", 92.00),
                                 ("MO-TONER-26", "Toner 26A", "each", 310.00), ("MO-FILE-LV", "Lever arch file", "each",
                                                                                 7.50)],
    "Emirates Cleaning Supplies LLC": [("EC-DEG-5", "Degreaser 5 L", "can", 39.00),
                                       ("EC-MOP-IND", "Industrial mop head", "each", 16.00)],
    "Falcon Electrical Trading LLC": [("FE-CBL-2.5", "Cable 2.5 mm2, 100 m", "coil", 185.00),
                                      ("FE-LED-40", "LED panel 40 W", "each", 64.00),
                                      ("FE-MCB-32", "MCB 32 A", "each", 27.50)],
}

# The lines to price, (PO, date, supplier, sku, qty). Their right prices are in AWAITING_PRICES.
AWAITING = [("2026-10-27", AWS, [("AW-3310", 120), ("BRG-6204-2RS", 200)]),
            ("2026-10-28", DPS, [("DP-GLV-NIT-L", 80)]), ("2026-10-29", CPK, [("CP-STRETCH-500", 36)])]
AWAITING_PRICES = {"AW-3310": 17.25, "BRG-6204-2RS": 9.80, "DP-GLV-NIT-L": 31.50, "CP-STRETCH-500": 27.00}
AWAITING_QUOTES = {"AW-3310": "Q-7730", "BRG-6204-2RS": "Q-7730", "DP-GLV-NIT-L": "Q-7725", "CP-STRETCH-500": "Q-7722"}


def _quote_lines(supplier: str, overrides: dict[str, tuple[str, float]] | None = None, drop=()):
    out = []
    for sku, desc, unit, price in CATALOG[supplier]:
        if sku in drop:
            continue
        unit_text, p = (overrides or {}).get(sku, ("each" if unit == "each" else f"per {unit}", price))
        out.append((sku, desc, unit_text, p))
    return out


# (ref, supplier, date, valid until, lines, notes)
QUOTES = [
    ("Q-7690", DPS, "2026-07-20", "2026-08-31",
     _quote_lines(DPS, {"DP-GLV-NIT-L": ("per box", 28.75), "DP-HELM-W": ("each", 17.90)}), []),
    ("Q-7701", "Gulf Fasteners FZE", "2026-08-03", "2026-11-30", _quote_lines("Gulf Fasteners FZE"), []),
    ("Q-7703", "Marina Office Supply LLC", "2026-08-11", "2026-12-31", _quote_lines("Marina Office Supply LLC"), []),
    ("Q-7708", "Emirates Cleaning Supplies LLC", "2026-08-25", "2026-11-30",
     _quote_lines("Emirates Cleaning Supplies LLC"), []),
    ("Q-7711", "Falcon Electrical Trading LLC", "2026-09-02", "2026-10-31", _quote_lines("Falcon Electrical Trading LLC"),
     []),
    ("Q-7714", AWS, "2026-09-28", "2026-12-31", _quote_lines(AWS), []),
    ("Q-7716", CPK, "2026-06-10", "2026-07-31", _quote_lines(CPK), []),
    ("Q-7719", AWT, "2026-10-08", "2026-12-31", _quote_lines(AWT), []),
    ("Q-7722", CPK, "2026-10-02", "2026-12-31",
     _quote_lines(CPK, {"CP-STRETCH-500": ("per carton of 6 rolls", 162.00), "CP-TAPE-48": ("per roll", 3.25)}),
     ["Stretch film is sold in full cartons only (6 rolls per carton)."]),
    ("Q-7725", DPS, "2026-10-05", "2026-12-31",
     _quote_lines(DPS, {"DP-GLV-NIT-L": ("per box", 31.50), "DP-VEST-HV": ("each", 9.60)}), []),
    ("Q-7730", AWS, "2026-10-15", "2027-01-31",
     _quote_lines(AWS, {"AW-3310": ("per metre", 17.25), "AW-4402": ("per drum", 279.00),
                        "AW-5508": ("each", 15.10)}),
     ["This quotation is revision 1 of quotation Q-7714 and supersedes it in full."]),
    ("Q-7733", "Gulf Fasteners FZE", "2026-10-19", "2027-01-31",
     _quote_lines("Gulf Fasteners FZE", {"GF-M10-50": ("per box", 46.50)}), []),
    ("Q-7736", "Falcon Electrical Trading LLC", "2026-10-22", "2027-01-31",
     _quote_lines("Falcon Electrical Trading LLC", {"FE-CBL-2.5": ("per coil", 192.00)}), []),
    ("Q-7739", "Marina Office Supply LLC", "2026-10-26", "2027-02-28",
     _quote_lines("Marina Office Supply LLC", {"MO-TONER-26": ("each", 298.00)}), []),
]


def _long_date(iso: str) -> str:
    d = dt.date.fromisoformat(iso)
    return f"{d.day} {d:%B %Y}"


def quote_pdf(ref, supplier, date, valid, lines, notes) -> bytes:
    page1 = [supplier, "Dubai, United Arab Emirates", "", f"Quotation {ref}", f"Quote date: {_long_date(date)}",
             f"Valid until: {_long_date(valid)}", "Customer: Gulf Distribution Co. LLC, Procurement", "",
             "SKU             Description                          Unit price (AED)"]
    page1 += [f"{sku:<15} {desc:<36} {price:>8.2f} {unit}" for sku, desc, unit, price in lines]
    page1 += ["", "Prices are in AED and exclude VAT (5%)."]
    page2 = ["Terms", "", *notes, *([""] if notes else []),
             "Delivery within 7 working days of the purchase order.", "Payment within 45 days of the invoice date.",
             "Prices are fixed until the date this quotation is valid until.", "",
             f"Contact: sales, {supplier}"]
    return office.pdf_bytes([page1, page2], f"Quotation {ref}", supplier)


def tracker_rows() -> list[list]:
    rng = random.Random(4401)
    rows, po_n = [], 1
    day = dt.date(2026, 1, 4)
    suppliers = sorted(CATALOG)
    old = {"AW-3310": 18.40, "BRG-6204-2RS": 9.80, "DP-GLV-NIT-L": 29.40, "CP-STRETCH-500": 25.00}
    while day <= dt.date(2026, 10, 30):
        orders = [(rng.choice(suppliers), None) for _ in range(rng.choice((0, 1, 2, 2, 3)))]
        orders += [(supplier, lines) for date, supplier, lines in AWAITING if date == day.isoformat()]
        for supplier, awaiting in orders:
            po = f"PO-26-{po_n:04d}"
            po_n += 1
            if awaiting:
                for sku, qty in awaiting:
                    desc, unit = next((d, u) for s, d, u, _ in CATALOG[supplier] if s == sku)
                    rows.append([len(rows) + 1, po, day.isoformat(), supplier, sku, desc, qty, unit, None,
                                 "Awaiting price", None])
                continue
            for sku, desc, unit, base in rng.sample(CATALOG[supplier], k=min(len(CATALOG[supplier]),
                                                                              rng.randint(1, 4))):
                if supplier == AWT and sku == "BRG-6204-2RS":
                    continue
                price = old.get(sku, round(base * (1 + rng.choice((-0.04, -0.02, 0, 0, 0.02, 0.03))), 2))
                age = (dt.date(2026, 10, 31) - day).days
                status = ("Received" if age > 40 else rng.choice(("Received", "Ordered", "Ordered"))) \
                    if rng.random() > 0.06 else rng.choice(("Cancelled", "Awaiting approval"))
                ref = f"Q-{7200 + day.toordinal() % 400}"
                if sku == "AW-3310" and day >= dt.date(2026, 9, 28):
                    ref = "Q-7714"
                qty = rng.randint(2, 60) * (5 if unit in ("each", "metre") else 1)
                rows.append([len(rows) + 1, po, day.isoformat(), supplier, sku, desc, qty, unit, price, status, ref])
        day += dt.timedelta(days=1)
    return rows


def tracker() -> tuple[list[list], dict[str, int], list[list]]:
    """(Lines sheet rows incl. header, sku -> sheet row of each awaiting line, Summary rows)."""
    rows = tracker_rows()
    at = {r[4]: n for n, r in enumerate(rows, 2) if r[9] == "Awaiting price"}
    assert sorted(at) == sorted(AWAITING_PRICES), at
    open_value = round(sum(r[6] * r[8] for r in rows if r[9] == "Ordered"), 2)
    summary = [["Measure", "Value"], ["Open commitments (AED)", open_value], ["Awaiting lines value (AED)", None],
               ["Prepared by", "Procurement"], ["Last refreshed", "2026-10-30"]]
    return [HEADER, *rows], at, summary


def awaiting_total() -> float:
    return round(sum(qty * AWAITING_PRICES[sku] for _, _, lines in AWAITING for sku, qty in lines), 2)


SUPPLIERS = [["Supplier", "Code", "Contact", "Payment terms"]] + [
    [name, f"S-{100 + i}", f"sales@{name.split()[0].lower()}{name.split()[1].lower()}.example", "45 days"]
    for i, name in enumerate(sorted(CATALOG))]

TRACKER_NOTES = [["Note"],
                 ["Every purchase-order line this year. Unit prices are in AED per the line's Unit, excluding VAT."],
                 ["Supplier quotations are in quotes/ (PDF), as received. A quotation applies to purchase orders "
                  "dated from its quote date until its valid-until date."],
                 ["A revision of a quotation supersedes the quotation it revises."]]


def build_procure(root: Path) -> None:
    rows, _, summary = tracker()
    write_xlsx(root / TRACKER, [("Lines", rows), ("Suppliers", SUPPLIERS), ("Summary", summary),
                                ("Notes", TRACKER_NOTES)], "PO tracker")
    for q in QUOTES:
        p = root / "quotes" / f"{q[0]}.pdf"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(quote_pdf(*q))


# ------------------------------------------------------------------ b4a-contracts

CONTRACT = "contracts/MSA-2026-014_northwind_freight.docx"
RETAIL = "contracts/MSA-2025-031_northwind_retail.docx"
REGISTER = "contracts/change_register.xlsx"


def msa(ref: str, customer: str, dated: str, *, applied: dict[str, str] | None = None,
        note: str = "Amended by CR-087.") -> list[tuple[str, str]]:
    """The agreement's paragraphs (style, text). applied: clause number -> replacement text of that clause."""
    clauses = [
        ("1. Definitions", [("1.1", ('"Services" means the warehousing, transport and handling services described in '
                                    'Schedule 1.')),
                            ("1.2", '"Rates" means the charges set out in Schedule 2.'),
                            ("1.3", ('"Working Day" means a day other than a Saturday, Sunday or public holiday in the '
                                    'UAE.'))]),
        ("2. Term", [("2.1", "This Agreement starts on 1 February 2026 and runs for three years."),
                     ("2.2", ("Either party may extend the Agreement by one year by written notice before the end of "
                             "the term."))]),
        ("3. Services", [("3.1", "The Provider shall perform the Services with reasonable skill and care."),
                         ("3.2", "The Provider shall meet the service levels in Schedule 3."),
                         ("3.3", "The Customer shall give the Provider a volume forecast every month.")]),
        ("4. Customer obligations", [("4.1", ("The Customer shall label every consignment as the Provider's handling "
                                             "guide requires.")),
                                     ("4.2", "The Customer shall declare dangerous goods before collection.")]),
        ("5. Subcontracting", [("5.1", "The Provider may subcontract transport to licensed carriers."),
                               ("5.2", "The Provider remains responsible for its subcontractors.")]),
        ("6. Warehousing", [("6.1", "The Provider shall store the Customer's goods in its Jebel Ali warehouse."),
                            ("6.2", "The Provider shall count stock every quarter and report differences."),
                            ("6.3", "Goods stay the property of the Customer at all times."),
                            ("6.4", "[Withdrawn by CR-087.]")]),
        ("7. Charges and payment", [("7.1", "The Customer shall pay the Rates for the Services."),
                                    ("7.2", "The Customer shall pay each invoice within 30 days of the invoice date."),
                                    ("7.3", "Late payments bear interest at 1% per month."),
                                    ("7.4", ("The Customer shall raise any dispute about an invoice within 30 days of "
                                            "the invoice date.")),
                                    ("7.5", "The Rates exclude VAT.")]),
        ("8. Insurance", [("8.1", ("The Provider shall keep goods-in-transit insurance of at least AED 1,000,000 per "
                                  "consignment.")),
                          ("8.2", "The Provider shall show its insurance certificates on request.")]),
        ("9. Termination", [("9.1", ("Either party may terminate this Agreement for material breach that is not "
                                    "remedied within 30 days.")),
                            ("9.2", "Either party may terminate this Agreement if the other becomes insolvent."),
                            ("9.3", "On termination the Customer shall collect its goods within 14 days."),
                            ("9.4", ("Either party may terminate this Agreement for convenience on 30 days' written "
                                    "notice."))]),
        ("10. Exclusivity", [("10.1", ("The Provider shall not handle the goods of the Customer's direct competitors "
                                      "in the same warehouse bay.")),
                             ("10.2", "The Customer may use other providers for sea and air freight."),
                             ("10.3", ("The Customer shall use the Provider exclusively for all road freight between "
                                      "Dubai and Abu Dhabi."))]),
        ("11. Confidentiality", [("11.1", "Each party shall keep the other's confidential information confidential."),
                                 ("11.2", "This clause survives termination for three years.")]),
        ("12. Liability", [("12.1", ("Each party's total liability under this Agreement is limited to AED 2,000,000 in "
                                    "any contract year.")),
                           ("12.2", "Neither party is liable for loss of profit.")]),
        ("13. Force majeure", [("13.1", ("Neither party is liable for delay caused by events beyond its reasonable "
                                        "control."))]),
        ("14. Notices", [("14.1", "Notices must be in writing and sent to the addresses on the first page.")]),
        ("15. Governing law", [("15.1", ("This Agreement is governed by the laws of the Emirate of Dubai and the "
                                        "federal laws of the UAE.")),
                               ("15.2", "The courts of Dubai have exclusive jurisdiction.")]),
        ("Schedule 2: Rates", [("S2.1", "Storage: AED 38.00 per pallet per week."),
                               ("S2.2", "Handling in or out: AED 12.50 per pallet."),
                               ("S2.3", "Standard pallet, Dubai to Abu Dhabi: AED 410.00 per pallet."),
                               ("S2.4", "Standard pallet, Dubai to Al Ain: AED 410.00 per pallet."),
                               ("S2.5", "Standard pallet, Dubai to Ras Al Khaimah: AED 520.00 per pallet."),
                               ("S2.6", "Fuel surcharge: 4% of transport charges.")]),
    ]
    out = [("Heading 1", f"Master Services Agreement {ref}"),
           ("Normal", f"Between Sahara Logistics LLC (the Provider) and {customer} (the Customer), dated {dated}.")]
    for heading, items in clauses:
        out.append(("Heading 2", heading))
        out += [("Normal", f"{num} {(applied or {}).get(num, text)}") for num, text in items]
    out.append(("Amendment Note", note))
    return out


# CR, contract, customer, clause, change, old text, new text, status, decided on
KEY_CRS = [
    ("CR-087", "MSA-2026-014", "Northwind Freight LLC", "6.4", "Withdraw clause",
     "The Provider may store hazardous goods of class 3 in the open yard.", "[Withdrawn by CR-087.]", "Approved",
     "2026-01-28"),
    ("CR-118", "MSA-2026-014", "Northwind Freight LLC", "7.2", "Replace text", "within 30 days of the invoice date",
     "within 45 days of the invoice date", "Approved", "2026-03-12"),
    ("CR-120", "MSA-2025-031", "Northwind Retail LLC", "15.1", "Replace text", "Emirate of Dubai",
     "Emirate of Abu Dhabi", "Approved", "2026-03-30"),
    ("CR-122", "MSA-2026-041", "Northwind Foods LLC", "8.1", "Replace text", "AED 1,000,000", "AED 3,000,000",
     "Approved", "2026-04-09"),
    ("CR-124", "MSA-2026-014", "Northwind Freight LLC", "12.1", "Replace text", "AED 2,000,000", "AED 5,000,000",
     "Withdrawn", "2026-04-21"),
    ("CR-127", "MSA-2026-014", "Northwind Freight LLC", "S2.3", "Replace text", "AED 410.00 per pallet",
     "AED 435.00 per pallet", "Approved", "2026-05-20"),
    ("CR-129", "MSA-2026-014", "Northwind Freight LLC", "10.3", "Withdraw clause",
     "The Customer shall use the Provider exclusively for all road freight between Dubai and Abu Dhabi.",
     "[Withdrawn by CR-129.]", "Approved", "2026-06-03"),
    ("CR-131", "MSA-2026-014", "Northwind Freight LLC", "7.2", "Replace text", "within 45 days of the invoice date",
     "within 60 days of the invoice date", "Approved", "2026-07-08"),
    ("CR-133", "MSA-2026-014", "Northwind Freight LLC", "9.4", "Replace text", "30 days' written notice",
     "90 days' written notice", "Pending", None),
    ("CR-135", "MSA-2026-014", "Northwind Freight LLC", "11.2", "Replace text", "three years", "five years",
     "Rejected", "2026-08-14"),
    ("CR-137", "MSA-2026-104", "Northwind Freight LLC", "S2.5", "Replace text", "AED 520.00", "AED 545.00",
     "Approved", "2026-09-01"),
]
APPLIED = ["CR-087", "CR-118", "CR-127", "CR-129", "CR-131"]


def register_rows() -> list[list]:
    rng = random.Random(4501)
    others = [("MSA-2024-007", "Al Noor Trading LLC"), ("MSA-2024-019", "Harbor Foods LLC"),
              ("MSA-2025-003", "Falcon Systems LLC"), ("MSA-2025-012", "Oasis Retail LLC"),
              ("MSA-2025-031", "Northwind Retail LLC"), ("MSA-2025-044", "Marina Pharmacy LLC"),
              ("MSA-2026-002", "Desert Rose Hospitality LLC"), ("MSA-2026-009", "Palm Electronics LLC"),
              ("MSA-2026-041", "Northwind Foods LLC"), ("MSA-2026-104", "Northwind Freight LLC"),
              ("MSA-2026-022", "Cedar Contracting LLC"), ("MSA-2026-030", "Zenith Auto Parts LLC")]
    templates = [("7.2", "within 30 days of the invoice date", "within 45 days of the invoice date"),
                 ("7.3", "1% per month", "1.5% per month"), ("8.1", "AED 1,000,000", "AED 2,000,000"),
                 ("9.4", "30 days' written notice", "60 days' written notice"),
                 ("12.1", "AED 2,000,000", "AED 3,000,000"), ("S2.1", "AED 38.00", "AED 40.00"),
                 ("S2.2", "AED 12.50", "AED 13.00"), ("S2.6", "4% of transport charges", "5% of transport charges"),
                 ("2.1", "three years", "five years"), ("11.2", "three years", "two years")]
    key = {int(r[0][3:]): r for r in KEY_CRS}
    rows = []
    for n in range(40, 182):
        if n in key:
            rows.append(list(key[n]))
            continue
        contract, customer = rng.choice(others)
        clause, old, new = rng.choice(templates)
        status = rng.choices(("Approved", "Withdrawn", "Pending", "Rejected"), weights=(55, 15, 15, 15))[0]
        decided = None if status == "Pending" else (dt.date(2025, 6, 1) + dt.timedelta(days=2 * n + rng.randint(0, 3))
                                                    ).isoformat()
        rows.append([f"CR-{n:03d}", contract, customer, clause, "Replace text", old, new, status, decided])
    return [["CR", "Contract", "Customer", "Clause", "Change", "Old text", "New text", "Status", "Decided on"], *rows]


def contract_after() -> dict[str, str]:
    """Clause number -> its text once the approved requests are applied (what b4-05 checks)."""
    return {"7.2": "The Customer shall pay each invoice within 60 days of the invoice date.",
            "S2.3": "Standard pallet, Dubai to Abu Dhabi: AED 435.00 per pallet.",
            "10.3": "[Withdrawn by CR-129.]"}


def build_contracts(root: Path) -> None:
    write_docx(root / CONTRACT, msa("MSA-2026-014", "Northwind Freight LLC", "15 January 2026"),
               "Master Services Agreement MSA-2026-014")
    write_docx(root / RETAIL, msa("MSA-2025-031", "Northwind Retail LLC", "3 March 2025", note="Amended by CR-061."),
               "Master Services Agreement MSA-2025-031")
    for ref, customer, dated in (("MSA-2026-041", "Northwind Foods LLC", "2 February 2026"),
                                 ("MSA-2026-104", "Northwind Freight LLC", "1 September 2026"),
                                 ("MSA-2026-009", "Palm Electronics LLC", "20 January 2026"),
                                 ("MSA-2025-044", "Marina Pharmacy LLC", "11 May 2025")):
        slug = customer.split()[0].lower() + "_" + customer.split()[1].lower()
        write_docx(root / f"contracts/{ref}_{slug}.docx", msa(ref, customer, dated, note="Amended by CR-040."),
                   f"Master Services Agreement {ref}")
    write_xlsx(root / REGISTER, [("Changes", register_rows()),
                                 ("Notes", [["Note"], [("Approved requests are applied to the contract they name, in CR "
                                                       "order.")], [("Withdrawn, pending and rejected requests are "
                                                                   "never applied.")],
                                            [("A withdrawn clause keeps its number and reads "
                                             "[Withdrawn by CR-nnn.]")],
                                            [("Each agreement ends with an amendment note listing every change "
                                              "request applied to it, in CR order.")]])], "Change register")


def build(catalog: Path) -> None:
    build_procure(catalog / "seeds/b4a-procure/files")
    build_contracts(catalog / "seeds/b4a-contracts/files")


if __name__ == "__main__":
    build(Path(sys.argv[1]))
