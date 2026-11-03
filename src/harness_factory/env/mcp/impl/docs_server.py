"""docs MCP server: read and edit Word (.docx) and Excel (.xlsx) files and read PDFs, confined to the run's
workspace (HF_WORKSPACE).

Beyond mcp and the standard library it imports python-docx, openpyxl and pypdf, the allow-list in
env/mcp/deps.py (ADR 0013): the `docs` extra installs them locally, and the sandbox image vendors them.
Every output is bounded, so a large document can't flood the model's context.
"""

from __future__ import annotations

import builtins
import datetime as dt
import os
import re
import sys
from pathlib import Path

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError  # message reaches the model; ValueError would be masked

try:
    import docx
    import openpyxl
    import pypdf
    from openpyxl.utils.cell import coordinate_from_string, get_column_letter, range_boundaries
    from openpyxl.utils.exceptions import CellCoordinatesException
except ImportError as e:                       # say what's missing instead of a bare traceback in the client log
    sys.exit(f"docs MCP server: cannot import {e.name!r}. Install the optional extra: uv sync --extra docs "
             f"(the sandbox image vendors it; see docs/adr/0013-server-dependencies.md)")

app = MCPServer("docs", instructions="Read and edit Word (.docx) and Excel (.xlsx) files, and read PDF files, in "
                                     "the task workspace. Paths are relative to the workspace root.")

MAX_FILE_BYTES = 20_000_000
MAX_PARAGRAPHS = 300
MAX_PARAGRAPH_CHARS = 1000
MAX_TABLE_ROWS = 10                 # rows previewed per table
MAX_CELL_CHARS = 200
MAX_CELLS = 2000                    # per xlsx_read
MAX_PDF_PAGES = 10                  # per pdf_text
MAX_PDF_CHARS = 20_000
MAX_TEXT_CHARS = 10_000             # text a write may add


def _root() -> Path:
    return Path(os.environ["HF_WORKSPACE"]).resolve()


def _resolve(path: str, suffixes: tuple[str, ...], kind: str) -> Path:
    root = _root()
    p = (root / path).resolve()
    if not p.is_relative_to(root):
        raise ToolError(f"path escapes the workspace: {path}")
    if not p.is_file():
        raise ToolError(f"no such file: {path}")
    if p.suffix.lower() not in suffixes:
        raise ToolError(f"not a {kind} file ({' or '.join(suffixes)}): {path}")
    if p.stat().st_size > MAX_FILE_BYTES:
        raise ToolError(f"file too large to open ({p.stat().st_size} bytes, limit {MAX_FILE_BYTES}): {path}")
    return p


def _clip(s: str, n: int) -> str:
    return s if len(s) <= n else f"{s[:n]}... [{len(s) - n} more characters]"


# ------------------------------------------------------------------ docx

def _open_docx(path: str):
    p = _resolve(path, (".docx",), "Word")
    try:
        return p, docx.Document(str(p))
    except Exception as e:  # any parse failure is the file's, and the model must see why
        raise ToolError(f"cannot open {path} as a Word document: {type(e).__name__}: {e}") from e


def _paragraphs(document):
    """Body paragraphs, then the paragraphs inside table cells, in document order per table."""
    yield from document.paragraphs
    for table in document.tables:
        for row in table.rows:
            seen = set()
            for cell in row.cells:              # a merged cell repeats across the row: visit it once
                if id(cell._tc) in seen:
                    continue
                seen.add(id(cell._tc))
                yield from cell.paragraphs


def _replace_in_runs(paragraph, old: str, new: str) -> int:
    """Replace every `old` in the paragraph's runs. A match inside one run keeps that run's formatting; a match
    across runs takes the formatting of the run it starts in, and the other runs keep the text around it."""
    count, start = 0, 0
    while True:
        runs = paragraph.runs
        texts = [r.text for r in runs]
        joined = "".join(texts)
        i = joined.find(old, start)
        if i < 0:
            return count
        end = i + len(old)
        pos, first = 0, None
        for k, t in enumerate(texts):
            lo, hi = pos, pos + len(t)
            if first is None and lo <= i < hi:
                first = k
                head = t[: i - lo]
                if end <= hi:                    # all inside this run
                    runs[k].text = head + new + t[end - lo:]
                    break
                runs[k].text = head + new
            elif first is not None:
                if end <= hi:                    # the run the match ends in keeps its tail
                    runs[k].text = t[end - lo:]
                    break
                runs[k].text = ""                # wholly inside the match
            pos = hi
        count += 1
        start = i + len(new)


@app.tool()
def docx_read(path: str) -> str:
    """Read a Word document: its paragraphs with their index and style, then a summary of each table (size and
    the first rows). Paragraph indices count body paragraphs only."""
    _, d = _open_docx(path)
    paras = d.paragraphs
    lines = [f"{path}: {len(paras)} paragraphs, {len(d.tables)} tables"]
    for i, para in enumerate(paras[:MAX_PARAGRAPHS]):
        lines.append(f"[{i}] ({para.style.name if para.style is not None else 'none'}) "
                     f"{_clip(para.text, MAX_PARAGRAPH_CHARS)}")
    if len(paras) > MAX_PARAGRAPHS:
        lines.append(f"... {len(paras) - MAX_PARAGRAPHS} more paragraphs not shown")
    for t, table in enumerate(d.tables):
        rows = table.rows
        lines.append(f"table {t}: {len(rows)} rows x {len(table.columns)} columns")
        for r, row in enumerate(rows[:MAX_TABLE_ROWS]):
            lines.append(f"  row {r}: " + " | ".join(_clip(c.text, 60) for c in row.cells))
        if len(rows) > MAX_TABLE_ROWS:
            lines.append(f"  ... {len(rows) - MAX_TABLE_ROWS} more rows")
    return "\n".join(lines)


@app.tool()
def docx_replace(path: str, old: str, new: str) -> str:
    """Replace every occurrence of exact text in a Word document's paragraphs and table cells, keeping the
    formatting of the text it replaces. A match must lie within one paragraph."""
    if not old:
        raise ToolError("old must not be empty")
    if len(new) > MAX_TEXT_CHARS:
        raise ToolError(f"new text too long ({len(new)} characters, limit {MAX_TEXT_CHARS})")
    p, d = _open_docx(path)
    count = sum(_replace_in_runs(para, old, new) for para in _paragraphs(d))
    if not count:
        raise ToolError(f"text not found in {path}: {old!r} (matching is exact, within one paragraph)")
    d.save(str(p))
    return f"replaced {count} occurrence{'s' if count != 1 else ''} in {path}"


@app.tool()
def docx_append_paragraph(path: str, text: str, style: str | None = None) -> str:
    """Add a paragraph at the end of a Word document's body, optionally in one of the document's paragraph
    styles (by name, e.g. "Heading 2")."""
    if len(text) > MAX_TEXT_CHARS:
        raise ToolError(f"text too long ({len(text)} characters, limit {MAX_TEXT_CHARS})")
    p, d = _open_docx(path)
    if style is not None:
        names = [s.name for s in d.styles if s.type == docx.enum.style.WD_STYLE_TYPE.PARAGRAPH]
        if style not in names:
            raise ToolError(f"no paragraph style {style!r} in {path}; available: {', '.join(names[:40])}")
    d.add_paragraph(text, style=style)
    d.save(str(p))
    return f"appended paragraph [{len(d.paragraphs) - 1}] ({style or 'default style'}) to {path}"


# ------------------------------------------------------------------ xlsx

def _open_xlsx(path: str, values: bool):
    p = _resolve(path, (".xlsx",), "Excel")
    try:
        return p, openpyxl.load_workbook(str(p), data_only=values)
    except Exception as e:  # any parse failure is the file's, and the model must see why
        raise ToolError(f"cannot open {path} as an Excel workbook: {type(e).__name__}: {e}") from e


def _sheet(wb, path: str, sheet: str):
    if sheet not in wb.sheetnames:
        raise ToolError(f"no sheet {sheet!r} in {path}; sheets: {', '.join(wb.sheetnames)}")
    return wb[sheet]


def _json_value(v):
    if isinstance(v, (dt.datetime, dt.date, dt.time)):
        return v.isoformat()
    if isinstance(v, str):
        return _clip(v, MAX_CELL_CHARS)
    if v is None or isinstance(v, (bool, int, float)):
        return v
    return str(v)


@app.tool()
def xlsx_sheets(path: str) -> list[str]:
    """List an Excel workbook's sheets with the range each one uses, e.g. "Prices: A1:D7 (7 rows x 4 columns)"."""
    _, wb = _open_xlsx(path, values=True)
    return [f"{ws.title}: {ws.dimensions} ({ws.max_row} rows x {ws.max_column} columns)" for ws in wb.worksheets]


@app.tool()
def xlsx_read(path: str, sheet: str, range: str = "A1:Z50") -> dict:
    """Read cell values from a range of one sheet, as rows (row 1 of the result is the range's first row).
    Cells with a formula show the result last saved in the file (null if never calculated) and are listed under
    "formulas" with their formula. The range is cut to the sheet's used area and to 2000 cells."""
    try:
        min_col, min_row, max_col, max_row = range_boundaries(range.upper())
    except (CellCoordinatesException, ValueError, TypeError) as e:
        raise ToolError(f"invalid range {range!r}: use A1 notation such as 'A1:D20'") from e
    if None in (min_col, min_row, max_col, max_row):
        raise ToolError(f"invalid range {range!r}: give both corners, such as 'A1:D20'")
    _, wb_values = _open_xlsx(path, values=True)
    _, wb_formulas = _open_xlsx(path, values=False)
    ws, wf = _sheet(wb_values, path, sheet), _sheet(wb_formulas, path, sheet)
    max_row, max_col = min(max_row, ws.max_row), min(max_col, ws.max_column)
    width = max(max_col - min_col + 1, 0)
    note = None
    if width and (max_row - min_row + 1) * width > MAX_CELLS:
        cut = min_row + MAX_CELLS // width - 1
        note = (f"cut at row {cut} (limit {MAX_CELLS} cells); read on with range="
                f"{get_column_letter(min_col)}{cut + 1}:{get_column_letter(max_col)}{max_row}")
        max_row = cut
    rows, formulas = [], {}
    for r in builtins.range(min_row, max_row + 1):
        row = []
        for c in builtins.range(min_col, max_col + 1):
            row.append(_json_value(ws.cell(row=r, column=c).value))
            f = wf.cell(row=r, column=c).value
            if isinstance(f, str) and f.startswith("="):
                formulas[f"{get_column_letter(c)}{r}"] = _clip(f, MAX_CELL_CHARS)
        rows.append(row)
    shown = (f"{get_column_letter(min_col)}{min_row}:{get_column_letter(max_col)}{max_row}" if rows and width
             else "empty")
    out = {"sheet": sheet, "range": shown, "rows": rows, "formulas": formulas}
    if note:
        out["note"] = note
    return out


@app.tool()
def xlsx_write(path: str, sheet: str, cell: str,
               value: str | int | float | bool | None) -> str:  # noqa: PYI041  (an int stays an int)
    """Set one cell of a sheet to a value (text, number, true/false, or null to clear it). Formulas can't be
    written: write the computed value. Saving keeps the workbook's other cells, formulas and formatting."""
    try:
        col, row = coordinate_from_string(cell.upper())
    except (CellCoordinatesException, ValueError, TypeError) as e:
        raise ToolError(f"invalid cell {cell!r}: use A1 notation such as 'C4'") from e
    if isinstance(value, str):
        if value.startswith("="):
            raise ToolError("formulas can't be written; write the computed value")
        if len(value) > MAX_TEXT_CHARS:
            raise ToolError(f"value too long ({len(value)} characters, limit {MAX_TEXT_CHARS})")
    p, wb = _open_xlsx(path, values=False)
    ws = _sheet(wb, path, sheet)
    ref = f"{col}{row}"
    target = ws[ref]
    if type(target).__name__ == "MergedCell":
        raise ToolError(f"{sheet}!{ref} is inside a merged range; write to the range's top-left cell")
    before = target.value
    target.value = value
    wb.save(str(p))
    return f"wrote {sheet}!{ref} = {value!r} (was {before!r})"


# ------------------------------------------------------------------ pdf

def _open_pdf(path: str):
    p = _resolve(path, (".pdf",), "PDF")
    try:
        reader = pypdf.PdfReader(str(p))
        if reader.is_encrypted and not reader.decrypt(""):
            raise ToolError(f"{path} is encrypted with a password")
        return reader
    except ToolError:
        raise
    except Exception as e:  # any parse failure is the file's, and the model must see why
        raise ToolError(f"cannot open {path} as a PDF: {type(e).__name__}: {e}") from e


_PAGES = re.compile(r"^\s*\d+\s*(-\s*\d+\s*)?(,\s*\d+\s*(-\s*\d+\s*)?)*$")


def _pages(spec: str, n: int) -> list[int]:
    if not _PAGES.match(spec):
        raise ToolError(f"invalid pages {spec!r}: use page numbers from 1, such as '1-3' or '2,4'")
    out: list[int] = []
    for part in spec.split(","):
        lo, _, hi = part.partition("-")
        a, b = int(lo), int(hi or lo)
        if a < 1 or b < a:
            raise ToolError(f"invalid pages {spec!r}: page numbers start at 1 and ranges go upwards")
        out.extend(x for x in range(a, b + 1) if x not in out)
    if out and out[0] > n:
        raise ToolError(f"the document has {n} page{'s' if n != 1 else ''}; asked for {spec!r}")
    return [x for x in out if x <= n]


@app.tool()
def pdf_info(path: str) -> str:
    """A PDF's page count, page size and document information (title, author, creation date)."""
    reader = _open_pdf(path)
    lines = [f"{path}: {len(reader.pages)} pages"]
    if reader.pages:
        box = reader.pages[0].mediabox
        lines.append(f"page 1 size: {float(box.width):.0f} x {float(box.height):.0f} pt")
    meta = reader.metadata or {}
    for key in ("/Title", "/Author", "/Subject", "/Creator", "/Producer", "/CreationDate"):
        if meta.get(key):
            lines.append(f"{key[1:].lower()}: {_clip(str(meta[key]), MAX_CELL_CHARS)}")
    return "\n".join(lines)


@app.tool()
def pdf_text(path: str, pages: str = "1-3") -> str:
    """Extract the text of some pages of a PDF (numbered from 1, e.g. "1-3" or "2,4"), at most 10 pages and
    20000 characters per call. Pages past the end are skipped."""
    reader = _open_pdf(path)
    wanted = _pages(pages, len(reader.pages))
    out, used = [], 0
    for k, n in enumerate(wanted):
        if k == MAX_PDF_PAGES:
            out.append(f"... stopped after {MAX_PDF_PAGES} pages; ask for pages {n}-{wanted[-1]} next")
            break
        try:
            text = reader.pages[n - 1].extract_text() or ""
        except Exception as e:  # noqa: BLE001  (a broken page is the file's; report it and go on)
            text = f"[could not extract text: {type(e).__name__}]"
        block = f"--- page {n} of {len(reader.pages)} ---\n{text.strip()}"
        if used + len(block) > MAX_PDF_CHARS:
            out.append(_clip(block, max(MAX_PDF_CHARS - used, 0)))
            out.append(f"... stopped at {MAX_PDF_CHARS} characters; ask for pages from {n} on")
            break
        out.append(block)
        used += len(block)
    return "\n".join(out)


if __name__ == "__main__":
    app.run()
