"""The docs server (docx, xlsx, pdf), its check kinds, and the libraries it needs beyond mcp (ADR 0013): what each
tool returns and refuses, how the graders read office files, what happens without the `docs` extra, and that
adding all this moved no existing identity."""

import ast
import json
import subprocess
import sys
import tomllib
from pathlib import Path

import docx
import openpyxl
import pytest

from harness_factory.env import sandbox_image
from harness_factory.env.mcp import deps
from harness_factory.env.sandbox import LocalSandbox
from harness_factory.grading.base import GradeContext
from harness_factory.grading.state import StateDiffGrader
from harness_factory.mcp.pool import MCPSessionPool
from harness_factory.provision.provisioner import allowed_tools, missing_task_tools, task_class
from harness_factory.runner.runner import plan, run_one
from harness_factory.runner.spec import RunKey
from harness_factory.shapes.result import RunResult
from harness_factory.substrate.errors import ErrorKind, HarnessError
from harness_factory.substrate.gateway import ScriptedBackend
from harness_factory.tasks.model import Task, WeightedCheck

ROOT = Path(__file__).parents[1]


@pytest.fixture
def office(factory):
    with LocalSandbox("docs-test") as sb:
        sb.reset(factory.tasks.get("office-01"))                # the office seed
        yield sb


async def call(pool, tool, **args):
    return await pool.call(f"docs.{tool}", args)


# ------------------------------------------------------------------ docx

def _letter(path: Path) -> None:
    d = docx.Document()
    d.add_paragraph("Order summary", style="Heading 1")
    p = d.add_paragraph()
    p.add_run("Total due: ")
    p.add_run("AED 1,").bold = True
    p.add_run("200").bold = True
    p.add_run(" by Friday.")
    t = d.add_table(rows=3, cols=2)
    for r, (a, b) in enumerate([("SKU", "Qty"), ("OS-104", "3"), ("OS-221", "5")]):
        t.cell(r, 0).text, t.cell(r, 1).text = a, b
    t.cell(2, 1).text = "5 boxes of AED 1,200"
    d.save(str(path))


async def test_docx_read_lists_paragraphs_styles_and_tables(factory, office):
    _letter(office.workspace / "order.docx")
    async with MCPSessionPool(factory.declared, ["docs"], office.env()) as pool:
        seed = (await call(pool, "docx_read", path="letters/acceptance.docx")).text
        assert "6 paragraphs, 0 tables" in seed
        assert "[0] (Heading 1) Acceptance of quotation [QUOTE REF]" in seed
        assert "[3] (Normal) We accept quotation [QUOTE REF] for the items" in seed    # runs joined
        made = (await call(pool, "docx_read", path="order.docx")).text
        assert "table 0: 3 rows x 2 columns" in made and "row 1: OS-104 | 3" in made


async def test_docx_replace_keeps_formatting_and_crosses_runs(factory, office):
    _letter(office.workspace / "order.docx")
    async with MCPSessionPool(factory.declared, ["docs"], office.env()) as pool:
        res = await call(pool, "docx_replace", path="order.docx", old="AED 1,200", new="AED 1,350")
        assert not res.is_error and "replaced 2 occurrences" in res.text               # body + table cell
    d = docx.Document(str(office.workspace / "order.docx"))
    p = d.paragraphs[1]
    assert p.text == "Total due: AED 1,350 by Friday."
    assert [(r.text, bool(r.bold)) for r in p.runs] == [("Total due: ", False), ("AED 1,350", True), ("", True),
                                                       (" by Friday.", False)]   # the match's first run's style
    assert d.tables[0].cell(2, 1).text == "5 boxes of AED 1,350"


async def test_docx_replace_and_append_refuse_what_they_cant_do(factory, office):
    L = "letters/acceptance.docx"
    async with MCPSessionPool(factory.declared, ["docs"], office.env()) as pool:
        missing = await call(pool, "docx_replace", path=L, old="[PO NUMBER]", new="x")
        assert missing.is_error and "text not found" in missing.text
        empty = await call(pool, "docx_replace", path=L, old="", new="x")
        assert empty.is_error and "must not be empty" in empty.text
        style = await call(pool, "docx_append_paragraph", path=L, text="x", style="Fancy")
        assert style.is_error and "no paragraph style 'Fancy'" in style.text and "Signature" in style.text
        ok = await call(pool, "docx_append_paragraph", path=L, text="Procurement, Dubai office", style="Signature")
        assert not ok.is_error and "[6] (Signature)" in ok.text
        plain = await call(pool, "docx_append_paragraph", path=L, text="P.S.")
        assert not plain.is_error
    paras = docx.Document(str(office.workspace / L)).paragraphs
    assert [(p.style.name, p.text) for p in paras[-2:]] == [("Signature", "Procurement, Dubai office"),
                                                            ("Normal", "P.S.")]


# ------------------------------------------------------------------ xlsx

def _formulas(path: Path) -> None:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet"
    ws.append(["qty", "price", "total"])
    for r in range(2, 502):
        ws.append([r, 2.5, f"=A{r}*B{r}"])
    ws.merge_cells("E1:F1")
    wb.save(str(path))


async def test_xlsx_sheets_and_read_values(factory, office):
    async with MCPSessionPool(factory.declared, ["docs"], office.env()) as pool:
        sheets = (await call(pool, "xlsx_sheets", path="pricing/price_list.xlsx")).text
        assert "Prices: A1:D7 (7 rows x 4 columns)" in sheets and "Notes: A1:B3" in sheets
        res = await call(pool, "xlsx_read", path="pricing/price_list.xlsx", sheet="Prices")
        data = json.loads(res.text)
        assert data["range"] == "A1:D7"                       # cut to the used area, not A1:Z50
        assert data["rows"][0] == ["SKU", "Item", "Unit price (AED)", "Supplier"]
        assert data["rows"][6] == ["OS-509", "Cable Tray", 42.5, "Emirates Tech Trading"]   # numbers stay numbers
        assert data["formulas"] == {}
        part = json.loads((await call(pool, "xlsx_read", path="pricing/price_list.xlsx", sheet="Prices",
                                      range="b3:c4")).text)
        assert part == {"sheet": "Prices", "range": "B3:C4", "rows": [["Monitor Arm", 210], ["Ergonomic Mouse", 49]],
                        "formulas": {}}


async def test_xlsx_read_flags_formulas_and_bounds_output(factory, office):
    _formulas(office.workspace / "calc.xlsx")
    async with MCPSessionPool(factory.declared, ["docs"], office.env()) as pool:
        data = json.loads((await call(pool, "xlsx_read", path="calc.xlsx", sheet="Sheet", range="A1:C3")).text)
        assert data["rows"][1] == [2, 2.5, None]               # never calculated: no saved result
        assert data["formulas"] == {"C2": "=A2*B2", "C3": "=A3*B3"}
        big = json.loads((await call(pool, "xlsx_read", path="calc.xlsx", sheet="Sheet", range="A1:Z1000")).text)
        assert sum(len(r) for r in big["rows"]) <= 2000 and "cut at row" in big["note"]
        bad = await call(pool, "xlsx_read", path="calc.xlsx", sheet="Sheet", range="nonsense")
        assert bad.is_error and "invalid range" in bad.text
        nosheet = await call(pool, "xlsx_read", path="calc.xlsx", sheet="Missing")
        assert nosheet.is_error and "sheets: Sheet" in nosheet.text


async def test_xlsx_write_sets_values_and_refuses_formulas(factory, office):
    P = "pricing/price_list.xlsx"
    _formulas(office.workspace / "calc.xlsx")
    async with MCPSessionPool(factory.declared, ["docs"], office.env()) as pool:
        ok = await call(pool, "xlsx_write", path=P, sheet="Prices", cell="c2", value=89.0)
        assert not ok.is_error and "wrote Prices!C2 = 89.0 (was 95)" in ok.text
        assert not (await call(pool, "xlsx_write", path=P, sheet="Notes", cell="B2", value="2026-10-20")).is_error
        assert not (await call(pool, "xlsx_write", path=P, sheet="Notes", cell="B3", value=None)).is_error
        formula = await call(pool, "xlsx_write", path=P, sheet="Prices", cell="C3", value="=C2*2")
        assert formula.is_error and "formulas can't be written" in formula.text
        cell = await call(pool, "xlsx_write", path=P, sheet="Prices", cell="C", value=1)
        assert cell.is_error and "invalid cell" in cell.text
        merged = await call(pool, "xlsx_write", path="calc.xlsx", sheet="Sheet", cell="F1", value=1)
        assert merged.is_error and "merged range" in merged.text
        kept = await call(pool, "xlsx_write", path="calc.xlsx", sheet="Sheet", cell="A2", value=4)
        assert not kept.is_error
    wb = openpyxl.load_workbook(str(office.workspace / P))
    assert wb["Prices"]["C2"].value == 89 and wb["Prices"]["C3"].value == 210
    assert wb["Notes"]["B2"].value == "2026-10-20" and wb["Notes"]["B3"].value is None
    assert openpyxl.load_workbook(str(office.workspace / "calc.xlsx"))["Sheet"]["C2"].value == "=A2*B2"  # kept


# ------------------------------------------------------------------ pdf

async def test_pdf_text_and_info(factory, office):
    Q = "quotes/Q-2291.pdf"
    async with MCPSessionPool(factory.declared, ["docs"], office.env()) as pool:
        info = (await call(pool, "pdf_info", path=Q)).text
        assert "2 pages" in info and "title: Quotation Q-2291" in info and "595 x 842 pt" in info
        first = (await call(pool, "pdf_text", path=Q, pages="1")).text
        assert "--- page 1 of 2 ---" in first and "OS-104   Wireless Keyboard     89.00" in first
        assert "page 2" not in first
        both = (await call(pool, "pdf_text", path=Q)).text            # default 1-3: past the end is skipped
        assert "--- page 2 of 2 ---" in both and "Q-2290" in both
        far = await call(pool, "pdf_text", path=Q, pages="5")
        assert far.is_error and "has 2 pages" in far.text
        bad = await call(pool, "pdf_text", path=Q, pages="first")
        assert bad.is_error and "invalid pages" in bad.text


async def test_paths_stay_in_the_workspace_and_files_must_fit_the_tool(factory, office):
    (office.workspace / "broken.docx").write_bytes(b"not a zip")
    async with MCPSessionPool(factory.declared, ["docs"], office.env()) as pool:
        escape = await call(pool, "docx_read", path="../state/task.sqlite")
        assert escape.is_error and "escapes" in escape.text
        wrong = await call(pool, "xlsx_sheets", path="quotes/Q-2291.pdf")
        assert wrong.is_error and "not a Excel file" in wrong.text
        broken = await call(pool, "docx_read", path="broken.docx")
        assert broken.is_error and "cannot open broken.docx as a Word document" in broken.text
        gone = await call(pool, "pdf_info", path="quotes/none.pdf")
        assert gone.is_error and "no such file" in gone.text


# ------------------------------------------------------------------ check kinds

def _grade(factory, task: Task, workspace: Path):
    dummy = RunResult(run_key=RunKey(task_version=task.key(), task_hash="x", recipe_hash="x",
                                     canonical_model="m", provider="p"), output="", finished=True)
    from harness_factory.env.state import EnvState, StateDiff
    ctx = GradeContext(task=task, result=dummy, before=EnvState(), after=EnvState(), diff=StateDiff(),
                       workspace=workspace, db_path=workspace / "none.sqlite", spans=[], max_steps=10)
    return {c.id: (c.passed, c.detail) for c in StateDiffGrader().grade(ctx).checks}


def _task(checks: list[dict]) -> Task:
    total = sum(c.get("weight", 0) for c in checks)
    checks = [*checks, {"id": "pad", "kind": "file_absent", "path": "nothing", "weight": round(1 - total, 6)}] \
        if total < 1 else checks
    return Task(id="t", domain="docs", instruction="x", allowed_tools=["docs.*"], oracle={"checks": checks})


def test_xlsx_cell_numbers_text_and_empty(factory, office):
    P = {"path": "pricing/price_list.xlsx", "sheet": "Prices"}
    wb = openpyxl.load_workbook(str(office.workspace / P["path"]))
    wb["Prices"]["C2"] = 89.004
    wb["Prices"]["C4"] = "54.50"                                # a number typed as text
    wb.save(str(office.workspace / P["path"]))
    got = _grade(factory, _task([
        {"id": "close", "kind": "xlsx_cell", "weight": 0.2, **P, "cell": "C2", "expected": 89, "tolerance": 0.005},
        {"id": "exact", "kind": "xlsx_cell", "weight": 0.2, **P, "cell": "C2", "expected": 89},
        {"id": "text_number", "kind": "xlsx_cell", "weight": 0.2, **P, "cell": "C4", "expected": 54.5,
         "tolerance": 0.01},
        {"id": "text", "kind": "xlsx_cell", "weight": 0.1, **P, "cell": "B3", "expected": "Monitor Arm"},
        {"id": "empty", "kind": "xlsx_cell", "weight": 0.1, **P, "cell": "A8"},
        {"id": "not_empty", "kind": "xlsx_cell", "weight": 0.1, **P, "cell": "A7"},
        {"id": "negated", "kind": "xlsx_cell", "weight": 0.05, **P, "cell": "C3", "expected": 999, "negate": True},
        {"id": "no_sheet", "kind": "xlsx_cell", "weight": 0.05, "path": P["path"], "sheet": "X", "cell": "A1",
         "expected": 1},
    ]), office.workspace)
    assert got["close"][0] and not got["exact"][0] and "89.004" in got["exact"][1]
    assert not got["text_number"][0] and got["text"][0] and got["empty"][0] and not got["not_empty"][0]
    assert got["negated"][0] and not got["no_sheet"][0] and "no sheet 'X'" in got["no_sheet"][1]


def test_docx_checks_read_paragraphs_tables_and_styles(factory, office):
    L = {"path": "letters/acceptance.docx"}
    d = docx.Document(str(office.workspace / L["path"]))
    d.add_paragraph("Procurement, Dubai office", style="Signature")
    d.add_paragraph("Procurement, Dubai office (unsigned)")
    d.add_table(rows=1, cols=1).cell(0, 0).text = "PO 7781"
    d.save(str(office.workspace / L["path"]))
    got = _grade(factory, _task([
        {"id": "contains", "kind": "docx_contains", "weight": 0.2, **L, "text": "Dear Ms. Haddad,"},
        {"id": "in_table", "kind": "docx_contains", "weight": 0.1, **L, "text": "PO 7781"},
        {"id": "absent", "kind": "docx_contains", "weight": 0.1, **L, "text": "Q-2291"},
        {"id": "styled", "kind": "docx_paragraph_regex", "weight": 0.2, **L, "pattern": "^Procurement, Dubai office$",
         "style": "Signature"},
        {"id": "wrong_style", "kind": "docx_paragraph_regex", "weight": 0.1, **L, "pattern": "^Kind regards",
         "style": "Signature"},
        {"id": "placeholders_left", "kind": "docx_paragraph_regex", "weight": 0.2, **L,
         "pattern": r"\[QUOTE REF\]", "negate": True},
        {"id": "no_file", "kind": "docx_contains", "weight": 0.1, "path": "letters/none.docx", "text": "x"},
    ]), office.workspace)
    assert got["contains"][0] and got["in_table"][0] and not got["absent"][0]
    assert got["styled"][0] and not got["wrong_style"][0]
    assert not got["placeholders_left"][0] and not got["no_file"][0] and "missing file" in got["no_file"][1]


def test_new_check_fields_validate_and_stay_out_of_old_hashes(factory):
    with pytest.raises(ValueError, match="needs sheet, cell"):
        WeightedCheck(id="x", kind="xlsx_cell", weight=1, path="a.xlsx")
    with pytest.raises(ValueError, match="one A1-style cell"):
        WeightedCheck(id="x", kind="xlsx_cell", weight=1, path="a.xlsx", sheet="S", cell="A1:B2")
    with pytest.raises(ValueError, match="tolerance applies only to a number"):
        WeightedCheck(id="x", kind="xlsx_cell", weight=1, path="a.xlsx", sheet="S", cell="A1", expected="x",
                      tolerance=0.1)
    with pytest.raises(ValueError, match="style is for docx_paragraph_regex"):
        WeightedCheck(id="x", kind="docx_contains", weight=1, path="a.docx", text="t", style="Normal")
    with pytest.raises(ValueError, match="sheet is for xlsx_cell"):
        WeightedCheck(id="x", kind="file_contains", weight=1, path="a", text="t", sheet="S")
    with pytest.raises(ValueError, match="bad pattern"):
        WeightedCheck(id="x", kind="docx_paragraph_regex", weight=1, path="a.docx", pattern="(unclosed")
    # Unset, the new fields are left out of the task hash: every existing task kept its hash (#47).
    assert factory.tasks.get("fs-01").content_hash() == "f88f6a779f5f0dfc"
    assert factory.tasks.get("mix-04").content_hash() == "880b8a65ecdf8c87"


# ------------------------------------------------------------------ the allow-list and the missing extra

def test_allow_list_matches_the_extra_and_what_is_installed():
    extra = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["optional-dependencies"][deps.EXTRA]
    assert sorted(extra) == sorted(deps.image_requirements())
    assert deps.problems(tuple(deps.ALLOWED)) == []
    assert set(deps.CHECK_LIBRARIES) == {"xlsx_cell", "docx_contains", "docx_paragraph_regex"}


def test_server_modules_import_only_mcp_the_stdlib_and_their_own_libraries():
    impl = ROOT / "src/harness_factory/env/mcp/impl"
    stdlib = set(sys.stdlib_module_names) | {"__future__", "mcp"}
    for f in sorted(impl.glob("*_server.py")):
        module = f"harness_factory.env.mcp.impl.{f.stem}"
        own = {deps.ALLOWED[d].imports for d in deps.SERVER_LIBRARIES.get(module, ())}
        for node in ast.walk(ast.parse(f.read_text())):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else \
                [node.module] if isinstance(node, ast.ImportFrom) and node.module else []
            for n in names:      # + the shared helpers next to the servers (impl/proc.py, ADR 0012)
                assert n.split(".")[0] in stdlib | own or n.startswith("harness_factory.env.mcp.impl"), \
                    f"{f.name} imports {n}: not on its allow-list"


async def test_missing_extra_fails_provisioning_clearly(factory, monkeypatch, own_sandboxes_dir):
    real = deps.importlib.util.find_spec
    monkeypatch.setattr(deps.importlib.util, "find_spec", lambda name, *a: None if name == "openpyxl" else real(name))
    with LocalSandbox("docs-missing") as sb:
        with pytest.raises(HarnessError) as e:
            sb.launch(factory.declared.server("docs"))
        assert e.value.kind == ErrorKind.PROVISION
        assert "MCP server 'docs' needs the optional extra 'docs'" in e.value.message
        assert "openpyxl is not installed" in e.value.message and "uv sync --extra docs" in e.value.message
        sb.launch(factory.declared.server("fs"))                # stdlib servers don't care
    with pytest.raises(HarnessError, match="office-01@1's checks needs the optional extra"):
        await run_one(factory, factory.tasks.get("office-01"), factory.recipes["v2-base"], "scripted/replay",
                      "scripted")
    assert not list(own_sandboxes_dir.iterdir())                # refused before a sandbox was made


def test_a_wrong_version_is_refused_too(monkeypatch):
    monkeypatch.setattr(deps.md, "version", lambda dist: "0.1" if dist == "pypdf" else deps.ALLOWED[dist].version)
    assert deps.problems(("pypdf", "openpyxl")) == ["pypdf 0.1 is installed, but the allow-list pins 6.20.1"]


def test_the_server_explains_a_missing_library_instead_of_a_traceback(tmp_path):
    code = ("import sys, runpy; sys.modules['pypdf'] = None; "
            "runpy.run_module('harness_factory.env.mcp.impl.docs_server', run_name='__main__')")
    proc = subprocess.run([sys.executable, "-c", code], check=False, capture_output=True, text=True, timeout=60,
                          env={"HF_WORKSPACE": str(tmp_path), "PATH": ""})
    assert proc.returncode == 1 and "cannot import 'pypdf'" in proc.stderr and "--extra docs" in proc.stderr


# ------------------------------------------------------------------ identity, plans, end to end

def test_adding_docs_moved_no_existing_identity(factory):
    d = factory.declared
    assert d.env_hash(factory.recipes["v2-base"].tools.mcp_servers, "openai/gpt-oss-120b") == "a55c2fb739f1"
    assert factory.recipes["v2-base"].content_hash() == "79abd03818943f65"
    docs_env, base_env = d.env_hash(["fs", "sql", "docs"], "m"), d.env_hash(["fs", "sql"], "m")
    pinned = dict(deps.ALLOWED)
    try:                                                         # a library upgrade is a new experiment
        deps.ALLOWED["pypdf"] = deps.Library("pypdf", "9.9.9", "pypdf")
        assert d.env_hash(["fs", "sql", "docs"], "m") != docs_env
        assert d.env_hash(["fs", "sql"], "m") == base_env       # only for runs that open the docs server
        tag = sandbox_image.image_tag("python:3.12-slim", "3.12", "x86_64")
    finally:
        deps.ALLOWED.clear()
        deps.ALLOWED.update(pinned)
    assert sandbox_image.image_tag("python:3.12-slim", "3.12", "x86_64") != tag    # the pins are in the tag
    assert "env/mcp/impl/docs_server.py" in sandbox_image.SERVER_FILES


def test_office_tasks_run_only_on_recipes_that_reach_the_docs_server(factory):
    d, office1 = factory.declared, factory.tasks.get("office-01")
    assert missing_task_tools(factory.recipes["v2-base"], office1, d)[0].startswith("docs.")
    assert missing_task_tools(factory.recipes["v2-docs"], office1, d) == []
    assert {r for r in allowed_tools(factory.recipes["v2-docs"], factory.tasks.get("code-01"), d)
            if r.startswith("docs.")} == set()                  # nobody else gets the docs tools
    assert task_class(office1, d) == "KNOWN_PATH|REVERSIBLE|R1"
    specs = plan(factory, ["v2-base", "v2-docs"], "scripted", ["scripted/replay"])
    pairs = {(s.task_id, s.recipe_id) for s in specs}
    assert ("office-01", "v2-docs") in pairs and ("office-01", "v2-base") not in pairs
    # tasks on servers neither recipe opens (code and git from #45, biz from #46) are left out of both
    visible = [t.id for t in factory.tasks.all()
               if all(p.split(".")[0] in {"fs", "sql", "docs"} for p in t.allowed_tools)]
    assert all((t, "v2-docs") in pairs for t in visible)        # v2-docs is v2-base plus one server
    assert len(pairs) == 2 * len(visible) - 2


async def test_scripted_run_edits_the_price_list_from_the_pdf(factory):
    rec = await run_one(factory, factory.tasks.get("office-01"), factory.recipes["v2-docs"], "scripted/replay",
                        "scripted")
    assert rec.status.value == "SUCCESS" and rec.composite == pytest.approx(1.0)
    called = [s["name"] for s in map(json.loads, Path(rec.trace_path).read_text().splitlines())
              if s["kind"] == "tool_call"]
    assert called[:2] == ["docs.pdf_text", "docs.xlsx_read"] and called.count("docs.xlsx_write") == 4
    letter = await run_one(factory, factory.tasks.get("office-02"), factory.recipes["v2-docs"], "scripted/replay",
                           "scripted")
    assert letter.status.value == "SUCCESS"
    base = await run_one(factory, factory.tasks.get("office-01"), factory.recipes["v2-base"], "scripted/replay",
                         "scripted", backend=ScriptedBackend([
                             {"tool_calls": [{"name": "docs__pdf_text", "arguments": {"path": "quotes/Q-2291.pdf"}}]},
                             {"content": "done"}]))
    assert base.status.value != "SUCCESS" and any(v.kind == "tool_not_allowed" for v in base.violations)
