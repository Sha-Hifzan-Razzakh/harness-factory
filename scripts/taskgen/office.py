"""Writes catalog/seeds/office/files: a supplier quotation (PDF), a price list (xlsx) and a letter (docx).

The data below is also what scripts/taskgen/tasks.py computes the office tasks' expected values from. All three are
written with the standard library, and every byte is reproducible: fixed document metadata, fixed zip timestamps,
stored (uncompressed) zip entries. So the seed hash doesn't depend on when or where it was built, and building it
needs no extra.
"""

import datetime as dt
import sys
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

QUOTE_PATH = "quotes/Q-2291.pdf"
PRICES_PATH = "pricing/price_list.xlsx"
LETTER_PATH = "letters/acceptance.docx"

QUOTE_REF = "Q-2291"
QUOTE_DATE = ("20 October 2026", "2026-10-20")
VALID_UNTIL = "30 November 2026"
QUOTE_LINES = [("OS-104", "Wireless Keyboard", "89.00"), ("OS-221", "Ergonomic Mouse", "54.50"),
               ("OS-310", "USB-C Hub", "120.00"), ("OS-777", "Laptop Stand", "75.00")]
OLD_QUOTE = "Q-2290"

PRICE_HEADER = ["SKU", "Item", "Unit price (AED)", "Supplier"]
PRICES = [("OS-104", "Wireless Keyboard", 95.0, "Gulf Office Supply"),
          ("OS-118", "Monitor Arm", 210.0, "Emirates Tech Trading"),
          ("OS-221", "Ergonomic Mouse", 49.0, "Gulf Office Supply"),
          ("OS-310", "USB-C Hub", 135.0, "Gulf Office Supply"),
          ("OS-402", "Desk Lamp", 88.0, "Gulf Office Supply"),
          ("OS-509", "Cable Tray", 42.5, "Emirates Tech Trading")]
NOTES = [("Price list owner", "Procurement"), ("Last updated", "2026-09-01"), ("Currency", "AED, excluding VAT")]

QUOTE_PAGES = [
    ["Gulf Office Supply LLC", "Office 1204, Bay Square, Business Bay, Dubai", "",
     f"Quotation {QUOTE_REF}", f"Quote date: {QUOTE_DATE[0]}", f"Valid until: {VALID_UNTIL}",
     "Customer: Procurement, Dubai office", "",
     "SKU      Item                  Unit price (AED)",
     *[f"{sku}   {item:<21} {price}" for sku, item, price in QUOTE_LINES], "",
     "All prices are in AED and exclude VAT (5%)."],
    ["Terms", "",
     "Delivery within 5 working days of the purchase order.",
     "Payment within 30 days of the invoice date.",
     f"This quotation replaces quotation {OLD_QUOTE} of 12 September 2026, which quoted",
     "OS-104 at 92.00 and OS-310 at 128.00. That quotation is withdrawn.", "",
     "Contact: Farah Haddad, Sales department, sales@gulfofficesupply.example"],
]

# The letter: (style, [(run text, bold)]). The first placeholder in the body is split across two runs, as Word
# often leaves it after an edit; docs.docx_replace still finds it.
SIGNATURE = "Procurement, Dubai office"
LETTER = [
    ("Heading 1", [("Acceptance of quotation [QUOTE REF]", False)]),
    ("Normal", [("To: Gulf Office Supply LLC, Sales department", False)]),
    ("Normal", [("Dear Ms. Haddad,", False)]),
    ("Normal", [("We accept quotation ", False), ("[QUOTE ", True), ("REF]", True),
                (" for the items and prices it lists. Please deliver to our Dubai office before the quotation "
                 + "expires on [VALID UNTIL].", False)]),
    ("Normal", [(f"This acceptance does not cover quotation {OLD_QUOTE}, which you have withdrawn.", False)]),
    ("Normal", [("Kind regards,", False)]),
]

STAMP = dt.datetime(2026, 10, 21, 9, 0, 0)


# ------------------------------------------------------------------ pdf (standard library only)

def _pdf_str(s: str) -> str:
    return "(" + s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") + ")"


def pdf_bytes(pages: list[list[str]], title: str, author: str) -> bytes:
    """A plain PDF: Courier text, one line per entry, uncompressed content streams."""
    objs: list[bytes] = []
    n_pages = len(pages)
    page_ids = [5 + 2 * i for i in range(n_pages)]
    objs.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objs.append(f"<< /Type /Pages /Kids [{' '.join(f'{p} 0 R' for p in page_ids)}] /Count {n_pages} >>".encode())
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier /Encoding /WinAnsiEncoding >>")
    objs.append(f"<< /Title {_pdf_str(title)} /Author {_pdf_str(author)} /Producer (harness-factory taskgen) "
                f"/CreationDate (D:20261020090000+04'00') >>".encode())
    for i, lines in enumerate(pages):
        body = "\n".join(f"BT /F1 11 Tf 1 0 0 1 56 {770 - 16 * k} Tm {_pdf_str(line)} Tj ET"
                         for k, line in enumerate(lines) if line)
        stream = body.encode("latin-1")
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 3 0 R >> >> "
                    f"/Contents {page_ids[i] + 1} 0 R >>".encode())
        objs.append(f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream")
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for n, obj in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{n} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += "".join(f"{o:010d} 00000 n \n" for o in offsets).encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R /Info 4 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


# ------------------------------------------------------------------ docx and xlsx (standard library only)
# Minimal Office Open XML packages, written by hand rather than through python-docx's default template (800 KB of
# styles) or openpyxl (which stamps the save time into the file). python-docx and openpyxl open and edit them.

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
S = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG = "http://schemas.openxmlformats.org/package/2006/relationships"
CT = "application/vnd.openxmlformats-officedocument"
XML = "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>\n"


def _rels(*rels: tuple[str, str, str]) -> str:
    body = "".join(f'<Relationship Id="{i}" Type="{t}" Target="{target}"/>' for i, t, target in rels)
    return f'{XML}<Relationships xmlns="{PKG}">{body}</Relationships>'


def _types(overrides: dict[str, str]) -> str:
    body = "".join(f'<Override PartName="{p}" ContentType="{t}"/>' for p, t in overrides.items())
    return (f'{XML}<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            f'<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            f'<Default Extension="xml" ContentType="application/xml"/>{body}</Types>')


def _core(title: str) -> str:
    when = STAMP.strftime("%Y-%m-%dT%H:%M:%SZ")
    return (f'{XML}<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/'
            f'core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" '
            f'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"><dc:title>{escape(title)}</dc:title>'
            f'<dc:creator>Procurement</dc:creator><cp:lastModifiedBy>Procurement</cp:lastModifiedBy>'
            f'<cp:revision>1</cp:revision><dcterms:created xsi:type="dcterms:W3CDTF">{when}</dcterms:created>'
            f'<dcterms:modified xsi:type="dcterms:W3CDTF">{when}</dcterms:modified></cp:coreProperties>')


def _zip(path: Path, parts: dict[str, str]) -> None:
    """Fixed timestamps and attributes, entries stored uncompressed: the same bytes on every machine."""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as z:
        for name, text in parts.items():
            info = zipfile.ZipInfo(name, date_time=STAMP.timetuple()[:6])
            info.create_system = 3
            info.external_attr = 0o644 << 16
            z.writestr(info, text.encode())


def _run(text: str, bold: bool) -> str:
    return f'<w:r>{"<w:rPr><w:b/></w:rPr>" if bold else ""}<w:t xml:space="preserve">{escape(text)}</w:t></w:r>'


def write_docx(path: Path) -> None:
    ids = {"Normal": "Normal", "Heading 1": "Heading1", "Signature": "Signature"}
    paras = "".join(f'<w:p><w:pPr><w:pStyle w:val="{ids[style]}"/></w:pPr>{"".join(_run(t, b) for t, b in runs)}'
                    f'</w:p>' for style, runs in LETTER)
    document = (f'{XML}<w:document xmlns:w="{W}" xmlns:r="{R}"><w:body>{paras}<w:sectPr><w:pgSz w:w="11906" '
                f'w:h="16838"/><w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440" w:header="708" '
                f'w:footer="708" w:gutter="0"/></w:sectPr></w:body></w:document>')
    styles = (f'{XML}<w:styles xmlns:w="{W}"><w:docDefaults><w:rPrDefault><w:rPr><w:sz w:val="22"/></w:rPr>'
              f'</w:rPrDefault></w:docDefaults>'
              f'<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:qFormat/>'
              f'</w:style>'
              f'<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/>'
              f'<w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/><w:rPr><w:b/><w:sz w:val="32"/>'
              f'</w:rPr></w:style>'
              f'<w:style w:type="paragraph" w:customStyle="1" w:styleId="Signature"><w:name w:val="Signature"/>'
              f'<w:basedOn w:val="Normal"/><w:qFormat/><w:rPr><w:i/></w:rPr></w:style></w:styles>')
    _zip(path, {
        "[Content_Types].xml": _types({
            "/word/document.xml": f"{CT}.wordprocessingml.document.main+xml",
            "/word/styles.xml": f"{CT}.wordprocessingml.styles+xml",
            "/docProps/core.xml": "application/vnd.openxmlformats-package.core-properties+xml"}),
        "_rels/.rels": _rels(("rId1", f"{R}/officeDocument", "word/document.xml"),
                             ("rId2", f"{PKG}/metadata/core-properties", "docProps/core.xml")),
        "word/document.xml": document,
        "word/_rels/document.xml.rels": _rels(("rId1", f"{R}/styles", "styles.xml")),
        "word/styles.xml": styles,
        "docProps/core.xml": _core("Acceptance of quotation"),
    })


def _cell(ref: str, value) -> str:
    if isinstance(value, str):
        return f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{escape(value)}</t></is></c>'
    return f'<c r="{ref}" s="1"><v>{value:g}</v></c>'          # style 1: number format 0.00


def _sheet(rows: list[list]) -> str:
    cols = max(len(r) for r in rows)
    body = "".join(f'<row r="{n}">' + "".join(_cell(f"{chr(65 + k)}{n}", v) for k, v in enumerate(row))
                   + "</row>" for n, row in enumerate(rows, 1))
    return (f'{XML}<worksheet xmlns="{S}"><dimension ref="A1:{chr(64 + cols)}{len(rows)}"/><sheetData>{body}'
            f'</sheetData></worksheet>')


def write_xlsx(path: Path) -> None:
    sheets = [("Prices", [PRICE_HEADER, *[list(r) for r in PRICES]]), ("Notes", [list(r) for r in NOTES])]
    workbook = (f'{XML}<workbook xmlns="{S}" xmlns:r="{R}"><sheets>'
                + "".join(f'<sheet name="{name}" sheetId="{i}" r:id="rId{i}"/>' for i, (name, _) in
                          enumerate(sheets, 1)) + "</sheets></workbook>")
    styles = (f'{XML}<styleSheet xmlns="{S}"><fonts count="1"><font><sz val="11"/><name val="Calibri"/></font>'
              f'</fonts><fills count="2"><fill><patternFill patternType="none"/></fill><fill>'
              f'<patternFill patternType="gray125"/></fill></fills><borders count="1"><border><left/><right/>'
              f'<top/><bottom/><diagonal/></border></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" '
              f'fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" '
              f'borderId="0" xfId="0"/><xf numFmtId="2" fontId="0" fillId="0" borderId="0" xfId="0" '
              f'applyNumberFormat="1"/></cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" '
              f'builtinId="0"/></cellStyles></styleSheet>')
    parts = {
        "[Content_Types].xml": _types({
            "/xl/workbook.xml": f"{CT}.spreadsheetml.sheet.main+xml",
            **{f"/xl/worksheets/sheet{i}.xml": f"{CT}.spreadsheetml.worksheet+xml" for i in (1, 2)},
            "/xl/styles.xml": f"{CT}.spreadsheetml.styles+xml",
            "/docProps/core.xml": "application/vnd.openxmlformats-package.core-properties+xml"}),
        "_rels/.rels": _rels(("rId1", f"{R}/officeDocument", "xl/workbook.xml"),
                             ("rId2", f"{PKG}/metadata/core-properties", "docProps/core.xml")),
        "xl/workbook.xml": workbook,
        "xl/_rels/workbook.xml.rels": _rels(*[(f"rId{i}", f"{R}/worksheet", f"worksheets/sheet{i}.xml")
                                              for i in (1, 2)], ("rId3", f"{R}/styles", "styles.xml")),
        **{f"xl/worksheets/sheet{i}.xml": _sheet(rows) for i, (_, rows) in enumerate(sheets, 1)},
        "xl/styles.xml": styles,
        "docProps/core.xml": _core("Price list"),
    }
    _zip(path, parts)


def build(files: Path) -> None:
    for rel in (QUOTE_PATH, PRICES_PATH, LETTER_PATH):
        (files / rel).parent.mkdir(parents=True, exist_ok=True)
    (files / QUOTE_PATH).write_bytes(pdf_bytes(QUOTE_PAGES, f"Quotation {QUOTE_REF}", "Gulf Office Supply LLC"))
    write_xlsx(files / PRICES_PATH)
    write_docx(files / LETTER_PATH)


if __name__ == "__main__":
    build(Path(sys.argv[1]) / "seeds/office/files")
