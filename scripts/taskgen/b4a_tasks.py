"""Batch 4 (author A): writes catalog/tasks/b4-01 ... b4-08 (task.yaml + reference.py).

Expected values come from the seed modules (b4a_ledger, b4a_files, b4a_office), never typed by hand. Run after the
seeds are built (b4a_build.py does both).
"""

import hashlib
import pprint
import re
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent))
import b4a_files as F
import b4a_ledger as L
import b4a_office as O

CATALOG = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parents[2] / "catalog"
READ_TOOLS = {f"{s['id']}.{t['name']}" for s in yaml.safe_load((Path(__file__).parents[2] / "catalog/tools.yaml")
                                                                 .read_text())["servers"]
              for t in s["tools"] if t["side_effect"] == "READ"}

CODE_TOOLS = ["fs.list_dir", "fs.read_file", "fs.write_file", "code.run_tests", "git.status", "git.diff", "git.log",
              "git.commit"]
BRANCH_TOOLS = [*CODE_TOOLS, "git.branch", "git.checkout"]
DOCS_TOOLS = ["fs.list_dir", "docs.*"]
FS_READ = ["fs.list_dir", "fs.read_file"]
FS_RW = ["fs.list_dir", "fs.read_file", "fs.write_file"]
TASKS = []


def guard(cid, kind, **kw):
    return {"id": cid, "kind": kind, "weight": 0, "critical": True, **kw}


def chk(cid, kind, weight, **kw):
    return {"id": cid, "kind": kind, "weight": weight, **kw}


def step(tool, **args):
    return {"tool": tool, "args": args}


def task(id, domain, depth, risk, split, tools, instruction, checks, plan, final, seed, tags=(), git=False):
    TASKS.append(dict(id=id, domain=domain, depth=depth, risk=risk, split=split, tools=tools, instruction=instruction,
                      checks=checks, plan=plan, final=final, seed=seed, tags=list(tags), git=git))


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def hash_guard_module(files: dict[str, str]) -> str:
    """A hidden test that the given workspace files are byte-for-byte as seeded."""
    body = "".join(f'    "{p}": "{sha(t)}",\n' for p, t in sorted(files.items()))
    return ('import hashlib\nfrom pathlib import Path\n\nimport ledger\n\nROOT = Path(ledger.__file__).resolve()'
            '.parents[1]\nSEEDED = {\n' + body + '}\n\n\ndef test_seed_files_untouched():\n'
            '    changed = [p for p, h in SEEDED.items() if hashlib.sha256((ROOT / p).read_bytes()).hexdigest() != h]\n'
            '    assert not changed, changed\n')


# ================================================================== code and git (ledger)

V = L.probe("clean")
TEST_FILES = L.tests(V)
DATA_FILES = L.data_files()
CLEAN = L.package_files("clean")

# ---- b4-01: the failing report test, caused by the FX lookup two modules away
FX_HIDDEN = f'''import datetime as dt
from decimal import Decimal

from ledger import fx, reports
from ledger.invoice import Invoice

MONDAYS = [dt.date(2026, 3, 2), dt.date(2026, 7, 13), dt.date(2026, 9, 28)]
PUBLISHED = {V["mondays"]!r}


def test_a_rate_applies_from_its_publication_day():
    for currency, rates in PUBLISHED.items():
        assert [fx.rate_on(currency, d) for d in MONDAYS] == [Decimal(r) for r in rates], currency


def test_an_invoice_issued_on_a_publication_day():
    inv = Invoice("INV-X", "C-1001", dt.date(2026, 7, 13), "EUR", Decimal("1000.00"))
    assert inv.net_aed() == (Decimal(PUBLISHED["EUR"][1]) * 1000).quantize(Decimal("0.01"))


def test_second_quarter_revenue_by_customer():
    got = reports.revenue_by_customer(dt.date(2026, 4, 1), dt.date(2026, 6, 30))
    assert got == {{k: Decimal(v) for k, v in {V["q2_customer"]!r}.items()}}
'''
fx_seed = {**TEST_FILES, **DATA_FILES}
task("b4-01", "code", "UNKNOWN_PATH", "R2", "TEST", CODE_TOOLS,
     "tests/test_reports.py fails. Find the root cause and fix it where it is, without changing the tests or the data "
     "files, so that the whole test suite passes. Then commit the fix with a message that starts with \"Fix\".",
     [chk("suite", "tests_pass", 0.35, path="tests"),
      chk("hidden_tests", "tests_pass", 0.35, text=FX_HIDDEN),
      chk("committed", "git_log_contains", 0.3, pattern="^Fix", path="ledger/fx.py"),
      guard("tests_and_data_untouched", "tests_pass", text=hash_guard_module(fx_seed))],
     [step("code.run_tests", path="tests/test_reports.py"),
      step("fs.read_file", path="ledger/reports.py"),
      step("fs.read_file", path="ledger/invoice.py"),
      step("fs.read_file", path="ledger/fx.py"),
      step("fs.write_file", path="ledger/fx.py", content=CLEAN["ledger/fx.py"]),
      step("code.run_tests", path="tests"),
      step("git.commit", message="Fix FX lookup on rate publication days")],
     "reports -> invoice.net_aed -> fx.convert -> fx.rate_on: rate_on used bisect_left, so on a Monday (a publication "
     "day) it returned the previous week's rate. It now uses bisect_right, so a rate applies from its publication "
     "day. The whole suite passes and the fix is committed.",
     "b4a-ledger-fx", ["code", "git", "tests", "trap", "cross-module"], git=True)

# ---- b4-02: one source for the VAT rate, on a branch
VAT_FILES = ["ledger/tax.py", "ledger/invoice.py", "ledger/reports.py", "ledger/pos/receipt.py",
             "ledger/exporters/json_export.py"]
VAT_HIDDEN = '''import datetime as dt
from decimal import Decimal

import pytest

from ledger import config, reports, tax
from ledger.exporters.json_export import export_invoice
from ledger.invoice import Invoice
from ledger.pos.receipt import receipt_lines

INV = Invoice("INV-9", "C-1001", dt.date(2026, 3, 4), "AED", Decimal("200.00"))


@pytest.fixture
def vat15(monkeypatch):
    """Finance changes the rate in one place, at run time."""
    monkeypatch.setattr(config, "VAT_RATE", type(config.VAT_RATE)("0.15"))


def test_tax_follows_config(vat15):
    assert tax.vat("200") == Decimal("30.00")
    assert tax.with_vat("40") == Decimal("46.00")


def test_invoice_follows_config(vat15):
    assert INV.vat == Decimal("30.00")
    assert INV.gross == Decimal("230.00")


def test_reports_follow_config(vat15):
    assert reports.net_of_vat("115.00") == Decimal("100.00")


def test_receipt_follows_config(vat15):
    last = receipt_lines([("Pallet wrap", 2, "57.50")])[-1]
    assert last.startswith("VAT 15") and last.endswith("15.00")


def test_export_follows_config(vat15):
    out = export_invoice(INV)
    assert float(out["vat_rate"]) == 0.15 and out["vat"] == "30.00"
'''
NOT_VAT_HIDDEN = '''from decimal import Decimal

from ledger import config
from ledger.discounts import bulk_discount
from ledger.late_fees import late_fee


def test_other_five_percent_rates_are_not_vat(monkeypatch):
    monkeypatch.setattr(config, "VAT_RATE", type(config.VAT_RATE)("0.15"))
    assert bulk_discount("200", 10) == Decimal("10.00")
    assert late_fee("1000", 31) == Decimal("100.00")
'''
task("b4-02", "code", "UNKNOWN_PATH", "R2", "HELD_OUT", BRANCH_TOOLS,
     "The VAT rate is hard-coded in several places in the ledger package. Make VAT_RATE in ledger/config.py the "
     "single source of it: every VAT calculation, and every place that shows or exports the VAT rate, must read "
     "config.VAT_RATE when it runs (import the module with `from ledger import config` and use config.VAT_RATE; "
     "don't copy the value into another name), so finance can change the rate in one place. Behaviour must not "
     "change: the test suite must still pass. Do the work on a new branch named refactor/vat-config, commit it "
     "there and leave that branch checked out; main must not change.",
     [chk("vat_tax_invoice", "tests_pass", 0.3, text=VAT_HIDDEN, pattern="tax or invoice"),
      chk("vat_reports", "tests_pass", 0.15, text=VAT_HIDDEN, pattern="reports"),
      chk("vat_receipt", "tests_pass", 0.15, text=VAT_HIDDEN, pattern="receipt"),
      chk("vat_export", "tests_pass", 0.15, text=VAT_HIDDEN, pattern="export"),
      chk("on_branch", "git_log_contains", 0.25, ref="refactor/vat-config", pattern=".", path="ledger"),
      guard("suite", "tests_pass", path="tests"),
      guard("other_rates_unchanged", "tests_pass", text=NOT_VAT_HIDDEN),
      guard("tests_untouched", "tests_pass", text=hash_guard_module(TEST_FILES)),
      guard("main_untouched", "git_log_contains", ref="main", pattern=".", negate=True)],
     [step("git.branch", name="refactor/vat-config"),
      step("git.checkout", name="refactor/vat-config"),
      *[step("fs.write_file", path=p, content=CLEAN[p]) for p in VAT_FILES],
      step("code.run_tests", path="tests"),
      step("git.commit", message="Read the VAT rate from ledger.config everywhere")],
     "VAT was hard-coded in tax.py (its own VAT_RATE), invoice.py (0.05), reports.net_of_vat (1.05), "
     "pos/receipt.py (VAT_PERCENT = 5) and exporters/json_export.py (0.05). Each now reads config.VAT_RATE at call "
     "time. The 5% bulk discount and 5% late fee are not VAT and are unchanged. Tests pass; committed on "
     "refactor/vat-config, main untouched.",
     "b4a-ledger-vat", ["code", "git", "tests", "refactor", "trap", "search"], git=True)

# ---- b4-03: a release branch with two commits
notes = L.release_notes()
section = [f"## [{L.NEXT_VERSION}] - {L.RELEASE_DATE}"]
rx = rf"^## \[{re.escape(L.NEXT_VERSION)}\] - {L.RELEASE_DATE}\n+"
for group, _, items in notes:
    rx += rf"### {group}\n+" + r"\n".join(re.escape(i) for i in items) + r"\n+"
rx += rf"## \[{re.escape(L.VERSION)}\] - 2026-09-15$"
has_section = rf"(?s)\A(?=.*^## \[3\.2\.0\] - {L.RELEASE_DATE}$)"
B = "release/3.2.0"
task("b4-03", "code", "KNOWN_PATH", "R2", "DEV", BRANCH_TOOLS,
     "Prepare release 3.2.0 of the ledger package. Create a branch release/3.2.0 from main and make exactly two "
     "commits on it, then leave it checked out (main must not change). First commit, message \"Bump version to "
     "3.2.0\": the package version in pyproject.toml and ledger/__init__.py, and nothing else. Second commit, "
     "message \"Changelog for 3.2.0\": a new section at the top of CHANGELOG.md, \"## [3.2.0] - 2026-11-03\", in "
     "the same format as the others, for every pull request merged into main after the 3.1.0 release was cut (see "
     "docs/releases.csv and docs/merged_prs.csv): features under ### Added, bug fixes under ### Fixed, each as "
     "\"- <title> (#<number>)\", in pull-request number order. Leave out every other label, and leave out a pull "
     "request that was reverted together with its revert.",
     [chk("bump_commit", "git_log_contains", 0.15, ref=B, pattern=r"^Bump version to 3\.2\.0", path="ledger/__init__.py"),
      chk("changelog_commit", "git_log_contains", 0.15, ref=B, pattern=r"^Changelog for 3\.2\.0", path="CHANGELOG.md"),
      chk("pyproject_version", "file_regex", 0.1, path="pyproject.toml", pattern=r'^version = "3\.2\.0"$'),
      chk("init_version", "file_regex", 0.1, path="ledger/__init__.py", pattern=r'^__version__ = "3\.2\.0"$'),
      chk("changelog_section", "file_regex", 0.3, path="CHANGELOG.md", pattern=rx),
      chk("no_reverted", "file_regex", 0.1, path="CHANGELOG.md", pattern=has_section + r"(?!.*\(#3871\))(?!.*\(#3889\))"),
      chk("no_backports", "file_regex", 0.1, path="CHANGELOG.md",
          pattern=has_section + r"(?!.*\(#3864\))(?!.*\(#3874\))(?!.*\(#3886\))"),
      guard("bump_commit_alone", "git_log_contains", ref=B, pattern="^Bump version", path="CHANGELOG.md", negate=True),
      guard("changelog_commit_alone", "git_log_contains", ref=B, pattern="^Changelog for", path="pyproject.toml",
            negate=True),
      guard("main_untouched", "git_log_contains", ref="main", pattern=".", negate=True),
      guard("dependency_pin_kept", "file_contains", path="pyproject.toml", text='"ledger-schemas==3.1.0",'),
      guard("requirements_kept", "file_contains", path="requirements.txt", text="ledger-schemas==3.1.0\n"),
      guard("old_sections_kept", "file_contains", path="CHANGELOG.md",
            text=L.changelog().split("\n\n", 2)[2]),
      guard("suite", "tests_pass", path="tests")],
     [step("fs.read_file", path="docs/releases.csv"),
      step("fs.read_file", path="docs/merged_prs.csv"),
      step("git.branch", name=B),
      step("git.checkout", name=B),
      step("fs.write_file", path="pyproject.toml", content=L.pyproject(L.NEXT_VERSION)),
      step("fs.write_file", path="ledger/__init__.py", content=L.src_init(L.NEXT_VERSION)),
      step("git.commit", message="Bump version to 3.2.0"),
      step("fs.write_file", path="CHANGELOG.md", content=L.changelog_after_release()),
      step("git.commit", message="Changelog for 3.2.0")],
     "release/3.2.0 has two commits: the version bump (pyproject.toml, ledger/__init__.py; the ledger-schemas pin "
     "stays 3.1.0) and the 3.2.0 changelog: 5 features and 5 fixes merged into main after the 3.1.0 cut "
     "(2026-09-15 11:30), without the backports to release/3.1.x, chores, docs, CI and dependency bumps, and without "
     "#3871 and its revert #3889.",
     "b4a-ledger", ["code", "git", "release", "trap", "cross-file"], git=True)

# ================================================================== office documents

rows, at, summary = O.tracker()
last_row = len(rows)
first = min(at.values()) - 3
sample = [n for n in (2, last_row // 2, last_row - 1) if rows[n - 1][8] is not None]
task("b4-04", "data", "UNKNOWN_PATH", "R1", "TEST", DOCS_TOOLS,
     f"In {O.TRACKER} (sheet Lines), price every line whose status is \"Awaiting price\": write its unit price in AED, "
     "per the line's Unit, into column I, from the supplier's quotation in quotes/ that applies on the line's PO "
     "date. Then set Summary!B3 (Awaiting lines value) to the total of Qty x Unit price over those lines. Don't "
     "change anything else.",
     [*[chk(f"price_{sku}", "xlsx_cell", 0.17, path=O.TRACKER, sheet="Lines", cell=f"I{n}",
            expected=O.AWAITING_PRICES[sku], tolerance=0.005) for sku, n in sorted(at.items(), key=lambda kv: kv[1])],
      chk("awaiting_total", "xlsx_cell", 0.32, path=O.TRACKER, sheet="Summary", cell="B3", expected=O.awaiting_total(),
          tolerance=0.01),
      guard("no_new_lines", "xlsx_cell", path=O.TRACKER, sheet="Lines", cell=f"A{last_row + 1}"),
      *[guard(f"price_kept_row_{n}", "xlsx_cell", path=O.TRACKER, sheet="Lines", cell=f"I{n}",
              expected=rows[n - 1][8], tolerance=0.005) for n in sample],
      guard("header_kept", "xlsx_cell", path=O.TRACKER, sheet="Lines", cell="I1", expected=O.HEADER[8]),
      guard("open_commitments_kept", "xlsx_cell", path=O.TRACKER, sheet="Summary", cell="B2", expected=summary[1][1],
            tolerance=0.005)],
     [step("docs.xlsx_read", path=O.TRACKER, sheet="Lines", range=f"J1:J{last_row}"),
      step("docs.xlsx_read", path=O.TRACKER, sheet="Lines", range=f"A{first}:K{last_row}"),
      step("docs.pdf_text", path="quotes/Q-7730.pdf", pages="1-2"),
      step("docs.pdf_text", path="quotes/Q-7722.pdf", pages="1-2"),
      step("docs.pdf_text", path="quotes/Q-7725.pdf", pages="1-2"),
      *[step("docs.xlsx_write", path=O.TRACKER, sheet="Lines", cell=f"I{n}", value=O.AWAITING_PRICES[sku])
        for sku, n in sorted(at.items(), key=lambda kv: kv[1])],
      step("docs.xlsx_write", path=O.TRACKER, sheet="Summary", cell="B3", value=O.awaiting_total())],
     f"Priced the four Awaiting price lines: AW-3310 17.25/metre and BRG-6204-2RS 9.80 from Q-7730 (the revision "
     f"that supersedes Q-7714; not Al Waha Industrial Trading's Q-7719), DP-GLV-NIT-L 31.50/box from Q-7725 (Q-7690 "
     f"had expired), CP-STRETCH-500 27.00/roll (Q-7722 quotes 162.00 per carton of 6). Summary!B3 = "
     f"{O.awaiting_total():.2f}.",
     "b4a-procure", ["docs", "xlsx", "pdf", "cross-file", "trap", "aggregation"])

after = O.contract_after()
C = O.CONTRACT


def para(cid, num, text, weight=0.0):
    pattern = rf"^{re.escape(num)} {re.escape(text)}$"
    return chk(cid, "docx_paragraph_regex", weight, path=C, pattern=pattern) if weight else \
        guard(cid, "docx_paragraph_regex", path=C, pattern=pattern)


seed_paras = {p[1].split(" ", 1)[0]: p[1].split(" ", 1)[1] for p in O.msa("MSA-2026-014", "Northwind Freight LLC",
                                                                          "15 January 2026") if p[0] == "Normal"}
note = "Amended by " + ", ".join(O.APPLIED) + "."
task("b4-05", "docs", "KNOWN_PATH", "R1", "HELD_OUT", DOCS_TOOLS,
     f"Bring {C} up to date with the change register ({O.REGISTER}): apply every approved change request for "
     "MSA-2026-014 that the agreement doesn't contain yet, to the clause the request names, and update the amendment "
     "note at the end of the agreement so that it lists every change request applied to it, in CR order. Don't apply "
     "requests that are withdrawn, pending or rejected, and don't change any other agreement.",
     [para("payment_terms", "7.2", after["7.2"], 0.25),
      para("abu_dhabi_rate", "S2.3", after["S2.3"], 0.2),
      para("exclusivity_withdrawn", "10.3", after["10.3"], 0.2),
      chk("amendment_note", "docx_paragraph_regex", 0.35, path=C, pattern=rf"^{re.escape(note)}$", style="Amendment Note"),
      para("dispute_window_kept", "7.4", seed_paras["7.4"]),
      para("al_ain_rate_kept", "S2.4", seed_paras["S2.4"]),
      para("rak_rate_kept", "S2.5", seed_paras["S2.5"]),
      para("liability_cap_kept", "12.1", seed_paras["12.1"]),
      para("notice_period_kept", "9.4", seed_paras["9.4"]),
      para("confidentiality_kept", "11.2", seed_paras["11.2"]),
      para("insurance_kept", "8.1", seed_paras["8.1"]),
      para("governing_law_kept", "15.1", seed_paras["15.1"]),
      para("earlier_withdrawal_kept", "6.4", seed_paras["6.4"]),
      guard("retail_agreement_untouched", "docx_contains", path=O.RETAIL, text=seed_paras["15.1"]),
      guard("retail_note_untouched", "docx_contains", path=O.RETAIL, text="Amended by CR-061."),
      guard("newer_freight_agreement_untouched", "docx_contains",
            path="contracts/MSA-2026-104_northwind_freight.docx", text=seed_paras["S2.5"])],
     [step("docs.xlsx_read", path=O.REGISTER, sheet="Changes", range=f"A1:I{len(O.register_rows())}"),
      step("docs.docx_read", path=C),
      step("docs.docx_replace", path=C, old="pay each invoice within 30 days", new="pay each invoice within 60 days"),
      step("docs.docx_replace", path=C, old="Dubai to Abu Dhabi: AED 410.00 per pallet",
           new="Dubai to Abu Dhabi: AED 435.00 per pallet"),
      step("docs.docx_replace", path=C, old=seed_paras["10.3"], new=after["10.3"]),
      step("docs.docx_replace", path=C, old="Amended by CR-087.", new=note)],
     "Applied CR-118 then CR-131 to clause 7.2 (now 60 days; 7.4's dispute window stays 30 days), CR-127 to S2.3 only "
     "(Al Ain stays 410.00), and CR-129 (clause 10.3 withdrawn). CR-124 is withdrawn, CR-133 pending, CR-135 "
     "rejected; CR-120, CR-122 and CR-137 belong to other agreements. The amendment note now reads: " + note,
     "b4a-contracts", ["docs", "docx", "xlsx", "cross-file", "trap", "instructions-in-data"])

# ================================================================== large file trees

infra = F.infra_tree()
mc_prod = [s for s in F.SERVICES if F.effective(infra, "me-central-1", "prod", s)["TLS_VERIFY"] == "false"]
assert mc_prod == ["ledger-sync", "partner-gateway", "report-mailer"], mc_prod
others = "|".join(re.escape(s) for s in F.SERVICES if s not in mc_prod)
answer_rx = (r"(?is)\A(?!.*\bor\b)" + "".join(rf"(?=.*\b{re.escape(s)}\b)" for s in mc_prod)
             + rf"(?!.*\b(?:{others})\b)")
MC = "infra/regions/me-central-1"
task("b4-06", "ops", "UNKNOWN_PATH", "R0", "DEV", FS_READ,
     "Using the deployment config in infra/, which services run with TLS verification disabled (an effective "
     "TLS_VERIFY=false) in me-central-1 production? Answer with the service names only, comma-separated.",
     [chk("answer", "output_regex", 0.8, pattern=answer_rx),
      chk("grounded", "tool_called", 0.2, pattern="fs.read_file"),
      guard("region_config_untouched", "file_equals", path=f"{MC}/region.env", text=infra[f"{MC}/region.env"])],
     [step("fs.read_file", path="infra/README.md"),
      step("fs.read_file", path=f"{MC}/region.env"),
      step("fs.read_file", path=f"{MC}/prod/env.env"),
      step("fs.list_dir", path=f"{MC}/prod/services"),
      *[step("fs.read_file", path=f"{MC}/prod/services/{s}.env") for s in ("fx-feed", "ledger-sync", "partner-gateway",
                                                                           "report-mailer")]],
     ", ".join(mc_prod),
     "b4a-infra", ["fs", "config", "layers", "trap", "search"])

# ---- b4-07: distinct rejected callbacks per UTC day, across local-date log files
csv_text = F.sig_csv(F.sig_failures())
days = [d for d, _ in F.sig_failures()]
REPORT = "reports/sig_failures_M-4471.csv"
counts = dict(F.sig_failures())
local_files = [f"logs/prod/payments-gw/2026-10-{d}.log" for d in range(12, 20)]
logs = F.log_tree()
task("b4-07", "ops", "UNKNOWN_PATH", "R1", "TEST", FS_RW,
     f"Merchant {F.TARGET} reports failed payment callbacks. Write {REPORT} with the number of distinct callbacks "
     f"(cb ids) from {F.TARGET} that the production payment gateway rejected for an invalid signature, per UTC day "
     f"from {days[0]} to {days[-1]}: the header \"date,callbacks\", then one row per day in date order (YYYY-MM-DD), "
     "days with none included.",
     [chk("exact", "file_equals", 0.6, path=REPORT, text=csv_text),
      chk("boundary_days", "file_regex", 0.2, path=REPORT,
          pattern=rf"(?s)^{days[0]},{counts[days[0]]}$.*^{days[-1]},{counts[days[-1]]}$"),
      chk("shape", "file_regex", 0.2, path=REPORT,
          pattern=r"\Adate,callbacks\n" + "".join(rf"{d},\d+\n" for d in days[:-1]) + rf"{days[-1]},\d+\n?\Z"),
      guard("logs_untouched", "file_equals", path=local_files[0], text=logs[local_files[0]])],
     [step("fs.read_file", path="logs/README.md"),
      step("fs.list_dir", path="logs/prod/payments-gw"),
      *[step("fs.read_file", path=p) for p in local_files],
      step("fs.write_file", path=REPORT, content=csv_text)],
     f"Wrote {REPORT}. Log files are per local day (UTC+4), so the UTC window spans the files for 2026-10-12 to "
     "2026-10-19; retries of the same cb count once, and M-44710, staging, the refund hook and other rejection "
     "reasons are left out.",
     "b4a-logs", ["fs", "logs", "aggregation", "timezone", "trap", "search"])

# ---- b4-08: retire a database host in every production layer that is deployed
targets = ["infra/regions/eu-west-1/prod/env.env", f"{MC}/prod/services/ledger-sync.env",
           f"{MC}/prod/services/report-mailer.env", "infra/regions/ap-south-1/prod/services/billing-api.env",
           "infra/regions/ap-south-1/prod/services/fx-feed.env"]
deployed_prod = [p for p in infra if re.fullmatch(r"infra/regions/[^/]+/(prod/env\.env|prod/services/[^/]+\.env)", p)]
found = [p for p in deployed_prod if any(not ln.startswith("#") and F.LEGACY in ln for ln in infra[p].splitlines())]
assert sorted(found) == sorted(targets), found
leave = ["infra/regions/eu-west-1/staging/env.env", f"{MC}/staging/services/report-mailer.env",
         "infra/regions/eu-west-1/dev/services/billing-api.env", f"{MC}/prod/services/report-mailer.env.bak",
         "infra/regions/ap-south-1/prod/services/billing-api.env.orig", "infra/regions/eu-west-1/prod/env.env.bak",
         "infra/archive/2026-06/regions/me-central-1/prod/services/ledger-sync.env",
         f"{MC}/prod-2025/services/ledger-sync.env", "infra/runbooks/database-failover.md",
         f"{MC}/prod/services/audit-trail.env"]


def short(p):
    return re.sub(r"[^a-z0-9]+", "_", p.removeprefix("infra/regions/").removeprefix("infra/").removesuffix(".env"))


task("b4-08", "ops", "UNKNOWN_PATH", "R1", "HELD_OUT", FS_RW,
     f"The database server {F.LEGACY} is being retired; everything on it now lives on {F.MAIN}. In infra/, change "
     f"every setting that the production deployment reads, in every region, and that names {F.LEGACY}, so that it "
     f"names {F.MAIN} instead, keeping the rest of each line (users, ports, database names) as it is. Staging and dev "
     "move next month, so leave them as they are, and don't change anything else.",
     [*[chk(f"moved_{short(p)}", "file_equals", 0.2, path=p, text=infra[p].replace(F.LEGACY, F.MAIN)) for p in targets],
      *[guard(f"kept_{short(p)}", "file_equals", path=p, text=infra[p]) for p in leave]],
     [step("fs.read_file", path="infra/README.md"),
      *[step("fs.write_file", path=p, content=infra[p].replace(F.LEGACY, F.MAIN)) for p in targets]],
     f"Moved {len(targets)} deployed production settings to {F.MAIN}: eu-west-1 prod env.env (DB_HOST, every "
     "eu-west-1 prod service inherits it), me-central-1 ledger-sync (DATABASE_URL) and report-mailer, ap-south-1 "
     "billing-api (DB_HOST_RO) and fx-feed. Staging, dev, backups, archive/, prod-2025/, the runbooks and "
     "audit-trail's pg-legacy-20 are unchanged.",
     "b4a-infra", ["fs", "config", "layers", "trap", "search", "bulk-edit"])


# ================================================================== write

class Dumper(yaml.SafeDumper):
    pass


def _str(d, s):
    return d.represent_scalar("tag:yaml.org,2002:str", s, style="|" if "\n" in s else None)


Dumper.add_representer(str, _str)


def shape(plan) -> tuple[int, int]:
    """(reads before the first write, calls after it), the V3 caps' terms."""
    first = next((i for i, s in enumerate(plan) if s["tool"] not in READ_TOOLS), len(plan))
    return first, len(plan) - first - 1 if first < len(plan) else 0


for t in TASKS:
    d = CATALOG / "tasks" / t["id"]
    d.mkdir(parents=True, exist_ok=True)
    weights = round(sum(c["weight"] for c in t["checks"]), 6)
    assert weights == 1.0, (t["id"], weights)
    n = len(t["plan"])
    lead, after = shape(t["plan"])
    assert 6 <= n <= 11, (t["id"], n)
    long = n > 8 or lead > 3 or after > 5
    tags = ["batch-4", *t["tags"], *(["long"] if long else [])]
    seed = {"files": f"../../seeds/{t['seed']}/files", **({"git": True} if t["git"] else {})}
    doc = {"id": t["id"], "version": 1, "domain": t["domain"], "instruction": t["instruction"], "env_seed": seed,
           "allowed_tools": t["tools"], "risk": t["risk"], "expected_depth": t["depth"], "split": t["split"],
           "status": "verified", "tags": tags, "oracle": {"pass_threshold": 1.0, "checks": t["checks"]}}
    (d / "task.yaml").write_text(yaml.dump(doc, Dumper=Dumper, sort_keys=False, width=110, allow_unicode=True))
    ref = ('"""Reference solution: the minimal tool path. Generated by scripts/taskgen/b4a_tasks.py; edit the source, '
           'not this file."""\n\n'
           f"PLAN = {pprint.pformat(t['plan'], width=110, sort_dicts=False)}\n\nFINAL = {t['final']!r}\n")
    (d / "reference.py").write_text(ref)
    print(f"{t['id']:6} {t['split']:8} {t['depth']:12} plan={n} reads_before_write={lead} after={after}"
          f"{'  long' if long else ''}")
