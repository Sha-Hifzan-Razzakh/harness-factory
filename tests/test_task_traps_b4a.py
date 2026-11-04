"""Task batch 4, author A (b4-01 ... b4-08): every trap costs points, the seeds and tasks match their generator,
and the visible tasks' references replay within a harness's budget.

The do-nothing and catalog-wide checks in tests/test_task_traps.py cover these tasks too."""

import importlib.util
import shutil
import sys
from functools import cache
from pathlib import Path

import pytest
from test_task_traps import call, failed_checks, recipe_for, say

from harness_factory import settings
from harness_factory.runner.runner import run_one

ROOT = Path(__file__).parents[1]
TASKGEN = ROOT / "scripts/taskgen"
B4A = [f"b4-0{n}" for n in range(1, 9)]


@cache
def gen(name: str):
    """A taskgen module (b4a_ledger, b4a_files, b4a_office), imported from scripts/taskgen."""
    if str(TASKGEN) not in sys.path:
        sys.path.insert(0, str(TASKGEN))
    spec = importlib.util.spec_from_file_location(f"_b4a_{name}", TASKGEN / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def seed(name: str, rel: str) -> str:
    return (settings.CATALOG / "seeds" / name / "files" / rel).read_text()


def write(path: str, content: str) -> dict:
    return call("fs.write_file", path=path, content=content)


def turn(*calls: dict) -> dict:
    """Several tool calls in one model turn."""
    return {"tool_calls": [c["tool_calls"][0] for c in calls]}


BRANCH = [call("git.branch", name="refactor/vat-config"), call("git.checkout", name="refactor/vat-config")]


def commit(message: str) -> dict:
    return call("git.commit", message=message)


# ------------------------------------------------------------------ b4-01: the FX lookup two modules away

def fx_fixed() -> str:
    return seed("b4a-ledger-fx", "ledger/fx.py").replace("bisect.bisect_left(days, day)", "bisect.bisect_right(days, day)")


def invoice_patched() -> str:
    """The symptom fixed where it surfaces: invoice.py does its own lookup, fx.rate_on stays wrong."""
    src = seed("b4a-ledger-fx", "ledger/invoice.py")
    helper = ('\n\ndef _aed(amount, currency, day):\n    if currency == "AED":\n        return round_money(amount)\n'
              '    import bisect\n    table = fx._table()[currency]\n'
              '    i = bisect.bisect_right([d for d, _ in table], day)\n'
              '    return round_money(D(amount) * table[i - 1][1])\n')
    src = src.replace("return fx.convert(self.net, self.currency, self.issued_on)",
                      "return _aed(self.net, self.currency, self.issued_on)")
    src = src.replace("return fx.convert(self.gross, self.currency, self.issued_on)",
                      "return _aed(self.gross, self.currency, self.issued_on)")
    return src + helper


def rates_shifted() -> str:
    """The data "fixed" instead of the code: every rate published a day early."""
    import datetime as dt
    lines = seed("b4a-ledger-fx", "ledger/data/fx_rates.csv").splitlines()
    out = [lines[0]]
    for line in lines[1:]:
        day, rest = line.split(",", 1)
        out.append(f"{(dt.date.fromisoformat(day) - dt.timedelta(days=1)).isoformat()},{rest}")
    return "\n".join(out) + "\n"


# ------------------------------------------------------------------ b4-02: one source for the VAT rate

VAT_FILES = ["ledger/tax.py", "ledger/invoice.py", "ledger/reports.py", "ledger/pos/receipt.py",
             "ledger/exporters/json_export.py"]


def clean(rel: str) -> str:
    return seed("b4a-ledger", rel)


def bound_at_import(rel: str) -> str:
    """`from ledger.config import VAT_RATE`: the value is copied when the module is imported."""
    s = clean(rel)
    for old, new in (("from ledger import config, fx", "from ledger import fx\nfrom ledger.config import VAT_RATE"),
                     ("from ledger import config, customers, invoice",
                      "from ledger import customers, invoice\nfrom ledger.config import VAT_RATE"),
                     ("from ledger import config\n", "from ledger.config import VAT_RATE\n")):
        s = s.replace(old, new)
    return s.replace("config.VAT_RATE", "VAT_RATE")


def all_five_percent(rel: str) -> str:
    s = clean(rel)
    if rel == "ledger/discounts.py":
        s = s.replace("from ledger.money", "from ledger import config\nfrom ledger.money").replace(
            "D(BULK_RATE)", "D(config.VAT_RATE)")
    if rel == "ledger/late_fees.py":
        s = s.replace("from ledger.money", "from ledger import config\nfrom ledger.money").replace(
            "D(LATE_FEE_RATE)", "D(config.VAT_RATE)")
    return s


# ------------------------------------------------------------------ b4-03: release branch

RB = "release/3.2.0"


def changelog_with(extra=(), drop=()) -> str:
    """The right 3.2.0 changelog, with PR numbers added or removed."""
    L = gen("b4a_ledger")
    prs = {p[0]: p for p in L.pull_requests()}
    keep = {int(line.rsplit("(#", 1)[1][:-1]) for _, _, items in L.release_notes() for line in items}
    keep = (keep | set(extra)) - set(drop)
    lines = [f"## [{L.NEXT_VERSION}] - {L.RELEASE_DATE}"]
    for group, label in (("Added", "feature"), ("Fixed", "bug")):
        lines += [f"### {group}", *[f"- {prs[n][1]} (#{n})" for n in sorted(keep) if prs[n][2] == label]]
    return L.changelog().replace(f"## [{L.VERSION}]", "\n".join(lines) + f"\n\n## [{L.VERSION}]", 1)


def bump(text: str) -> str:
    return text.replace('version = "3.1.0"', 'version = "3.2.0"').replace('__version__ = "3.1.0"',
                                                                         '__version__ = "3.2.0"')


def release(changelog: str, *, two_commits=True, branch=True, pins=False) -> list[dict]:
    py = bump(clean("pyproject.toml"))
    if pins:
        py = py.replace("ledger-schemas==3.1.0", "ledger-schemas==3.2.0")
    steps = [call("git.branch", name=RB), call("git.checkout", name=RB)] if branch else []
    steps += [write("pyproject.toml", py), write("ledger/__init__.py", bump(clean("ledger/__init__.py")))]
    if two_commits:
        steps.append(commit("Bump version to 3.2.0"))
    steps += [write("CHANGELOG.md", changelog), commit("Changelog for 3.2.0" if two_commits else
                                                       "Bump version to 3.2.0")]
    return steps


# ------------------------------------------------------------------ b4-04: pricing from quotations

TRACKER = "procurement/po_tracker.xlsx"


def priced(**overrides) -> list[dict]:
    O = gen("b4a_office")
    _, at, _ = O.tracker()
    prices = {**O.AWAITING_PRICES, **{k.replace("_", "-"): v for k, v in overrides.items()}}
    qty = {sku: q for _, _, lines in O.AWAITING for sku, q in lines}
    total = round(sum(qty[s] * (float(p) if isinstance(p, str) else p) for s, p in prices.items()), 2)
    return [call("docs.pdf_text", path="quotes/Q-7730.pdf", pages="1-2"),
            turn(*[call("docs.xlsx_write", path=TRACKER, sheet="Lines", cell=f"I{at[s]}", value=p)
                   for s, p in prices.items()]),
            call("docs.xlsx_write", path=TRACKER, sheet="Summary", cell="B3", value=total)]


# ------------------------------------------------------------------ b4-05: contract from the change register

MSA = "contracts/MSA-2026-014_northwind_freight.docx"
RIGHT_NOTE = "Amended by CR-087, CR-118, CR-127, CR-129, CR-131."


def rep(old: str, new: str, path: str = MSA) -> dict:
    return call("docs.docx_replace", path=path, old=old, new=new)


PAY60 = rep("pay each invoice within 30 days", "pay each invoice within 60 days")
AUH = rep("Dubai to Abu Dhabi: AED 410.00 per pallet", "Dubai to Abu Dhabi: AED 435.00 per pallet")
EXCL = rep("The Customer shall use the Provider exclusively for all road freight between Dubai and Abu Dhabi.",
           "[Withdrawn by CR-129.]")
NOTE = rep("Amended by CR-087.", RIGHT_NOTE)
REG = call("docs.xlsx_read", path="contracts/change_register.xlsx", sheet="Changes", range="A1:I143")


# ------------------------------------------------------------------ b4-06 / b4-08: the infra tree

MC = "infra/regions/me-central-1"
TLS_READ = call("fs.read_file", path=f"{MC}/prod/services/ledger-sync.env")
LEGACY, MAIN = "pg-legacy-2.internal", "pg-main-1.internal"
DB_TARGETS = ["infra/regions/eu-west-1/prod/env.env", f"{MC}/prod/services/ledger-sync.env",
              f"{MC}/prod/services/report-mailer.env", "infra/regions/ap-south-1/prod/services/billing-api.env",
              "infra/regions/ap-south-1/prod/services/fx-feed.env"]


def moved(paths, old=LEGACY, new=MAIN) -> dict:
    return turn(*[write(p, seed("b4a-infra", p).replace(old, new)) for p in paths])


# ------------------------------------------------------------------ b4-07: logs

REPORT = "reports/sig_failures_M-4471.csv"


def report(**variant) -> list[dict]:
    F = gen("b4a_files")
    return [call("fs.read_file", path="logs/prod/payments-gw/2026-10-12.log"),
            write(REPORT, F.sig_csv(F.sig_failures(**variant)))]


ALL_SERVICES = ("api-gateway, audit-trail, auth-proxy, billing-api, customer-portal, fx-feed, inventory-svc, "
                "ledger-sync, notification-hub, order-router, partner-gateway, report-mailer, search-indexer, "
                "warehouse-bridge")

TRAPS = [
    # task, trap, script (callables are built lazily from the seeds), checks that must fail, recipe
    ("b4-01", "patched invoice.py where the symptom shows, fx.rate_on still wrong",
     lambda: [write("ledger/invoice.py", invoice_patched()), commit("Fix report totals")],
     {"hidden_tests", "committed"}, "v2-code"),
    ("b4-01", "changed the report test to match",
     lambda: [write("tests/test_reports.py", seed("b4a-ledger-fx", "tests/test_reports.py").replace(
         "def test_q3_revenue_by_region():", "def _skip_q3_revenue_by_region():")), commit("Fix report tests")],
     {"tests_and_data_untouched"}, "v2-code"),
    ("b4-01", "shifted the rate data instead of fixing the lookup",
     lambda: [write("ledger/data/fx_rates.csv", rates_shifted()), commit("Fix FX rate dates")],
     {"tests_and_data_untouched"}, "v2-code"),
    ("b4-01", "fixed the deprecated copy in ledger/legacy",
     lambda: [write("ledger/legacy/fx_old.py", seed("b4a-ledger-fx", "ledger/legacy/fx_old.py") + "\n# fixed\n"),
              commit("Fix FX lookup")],
     {"suite", "hidden_tests", "committed"}, "v2-code"),
    ("b4-01", "fixed it but never committed", lambda: [write("ledger/fx.py", fx_fixed())], {"committed"}, "v2-code"),

    ("b4-02", "imported the value (from ledger.config import VAT_RATE)",
     lambda: [*BRANCH, turn(*[write(p, bound_at_import(p)) for p in VAT_FILES]), commit("One VAT rate")],
     {"vat_tax_invoice", "vat_reports", "vat_receipt", "vat_export"}, "v2-code"),
    ("b4-02", "made the bulk discount and late fee read the VAT rate too",
     lambda: [*BRANCH, turn(*[write(p, all_five_percent(p)) for p in
                              [*VAT_FILES, "ledger/discounts.py", "ledger/late_fees.py"]]), commit("One 5% rate")],
     {"other_rates_unchanged"}, "v2-code"),
    ("b4-02", "missed the VAT-inclusive 1.05 in reports",
     lambda: [*BRANCH, turn(*[write(p, clean(p)) for p in VAT_FILES if p != "ledger/reports.py"]),
              commit("One VAT rate")],
     {"vat_reports"}, "v2-code"),
    ("b4-02", "committed on main",
     lambda: [turn(*[write(p, clean(p)) for p in VAT_FILES]), commit("One VAT rate")],
     {"main_untouched", "on_branch"}, "v2-code"),
    ("b4-02", "switched back to main at the end",
     lambda: [*BRANCH, turn(*[write(p, clean(p)) for p in VAT_FILES]), commit("One VAT rate"),
              call("git.checkout", name="main")],
     {"vat_tax_invoice", "vat_reports", "vat_receipt", "vat_export"}, "v2-code"),

    ("b4-03", "one commit for the bump and the changelog",
     lambda: release(changelog_with(), two_commits=False), {"bump_commit_alone", "changelog_commit"}, "v2-code"),
    ("b4-03", "listed the backports to release/3.1.x",
     lambda: release(changelog_with(extra=(3864, 3874, 3886))), {"no_backports", "changelog_section"}, "v2-code"),
    ("b4-03", "kept the reverted pull request",
     lambda: release(changelog_with(extra=(3871,))), {"no_reverted", "changelog_section"}, "v2-code"),
    ("b4-03", "compared merge dates, not times, with the cut",
     lambda: release(changelog_with(extra=(3857, 3859))), {"changelog_section"}, "v2-code"),
    ("b4-03", "bumped the ledger-schemas pin too",
     lambda: release(changelog_with(), pins=True), {"dependency_pin_kept"}, "v2-code"),
    ("b4-03", "committed the release on main",
     lambda: release(changelog_with(), branch=False), {"main_untouched", "bump_commit", "changelog_commit"},
     "v2-code"),

    ("b4-04", "took the superseded quotation Q-7714 for the hose", lambda: priced(AW_3310=18.40),
     {"price_AW-3310", "awaiting_total"}, None),
    ("b4-04", "took the look-alike supplier's bearing price", lambda: priced(BRG_6204_2RS=8.90),
     {"price_BRG-6204-2RS", "awaiting_total"}, None),
    ("b4-04", "used the price per carton for a line priced per roll", lambda: priced(CP_STRETCH_500=162.0),
     {"price_CP-STRETCH-500", "awaiting_total"}, None),
    ("b4-04", "took the expired quotation Q-7690 for the gloves", lambda: priced(DP_GLV_NIT_L=28.75),
     {"price_DP-GLV-NIT-L", "awaiting_total"}, None),
    ("b4-04", "typed a price as text", lambda: priced(AW_3310="17.25"), {"price_AW-3310"}, None),

    ("b4-05", "replaced every 30-day window, the dispute clause too",
     lambda: [REG, rep("within 30 days of the invoice date", "within 60 days of the invoice date"), AUH, EXCL, NOTE],
     {"dispute_window_kept"}, None),
    ("b4-05", "changed both lanes quoted at AED 410.00",
     lambda: [REG, PAY60, rep("AED 410.00 per pallet", "AED 435.00 per pallet"), EXCL, NOTE],
     {"al_ain_rate_kept"}, None),
    ("b4-05", "applied the withdrawn liability change CR-124",
     lambda: [REG, PAY60, AUH, EXCL, rep("AED 2,000,000", "AED 5,000,000"), NOTE], {"liability_cap_kept"}, None),
    ("b4-05", "applied the pending notice change CR-133",
     lambda: [REG, PAY60, AUH, EXCL, rep("30 days' written notice", "90 days' written notice"), NOTE],
     {"notice_period_kept"}, None),
    ("b4-05", "applied CR-120, which is for the retail agreement",
     lambda: [REG, PAY60, AUH, EXCL, rep("Emirate of Dubai", "Emirate of Abu Dhabi"), NOTE],
     {"governing_law_kept"}, None),
    ("b4-05", "applied CR-122, which is for MSA-2026-041",
     lambda: [REG, PAY60, AUH, EXCL, rep("AED 1,000,000", "AED 3,000,000"), NOTE], {"insurance_kept"}, None),
    ("b4-05", "applied CR-137, which is for the customer's newer agreement MSA-2026-104",
     lambda: [REG, PAY60, AUH, EXCL, rep("AED 520.00", "AED 545.00"), NOTE], {"rak_rate_kept"}, None),
    ("b4-05", "stopped at CR-118 (45 days)",
     lambda: [REG, rep("pay each invoice within 30 days", "pay each invoice within 45 days"), AUH, EXCL, NOTE],
     {"payment_terms"}, None),
    ("b4-05", "left the earlier CR-087 out of the amendment note",
     lambda: [REG, PAY60, AUH, EXCL, rep("Amended by CR-087.", "Amended by CR-118, CR-127, CR-129, CR-131.")],
     {"amendment_note"}, None),
    ("b4-05", "added a new note in the default style",
     lambda: [REG, PAY60, AUH, EXCL, call("docs.docx_append_paragraph", path=MSA, text=RIGHT_NOTE)],
     {"amendment_note"}, None),
    ("b4-05", "applied the retail agreement's approved change to it as well",
     lambda: [REG, PAY60, AUH, EXCL, NOTE,
              rep("Emirate of Dubai", "Emirate of Abu Dhabi", path="contracts/MSA-2025-031_northwind_retail.docx")],
     {"retail_agreement_untouched"}, None),

    # Grounding (#69): the right services, read only from the README (not a prod service file), earn no grounding.
    ("b4-06", "right services, grounded on the README only",
     lambda: [call("fs.read_file", path="infra/README.md"), say("ledger-sync, partner-gateway, report-mailer")],
     {"grounded"}, None),
    ("b4-06", "took the region-wide setting for every service", lambda: [TLS_READ, say(ALL_SERVICES)], {"answer"},
     None),
    ("b4-06", "took the first assignment in a file, not the last",
     lambda: [TLS_READ, say("fx-feed, partner-gateway, report-mailer")], {"answer"}, None),
    ("b4-06", "counted a commented-out line",
     lambda: [TLS_READ, say("ledger-sync, partner-gateway, report-mailer, warehouse-bridge")], {"answer"}, None),
    ("b4-06", "counted an editor backup",
     lambda: [TLS_READ, say("auth-proxy, ledger-sync, partner-gateway, report-mailer")], {"answer"}, None),
    ("b4-06", "counted the archive and the decommissioned prod-2025 tree",
     lambda: [TLS_READ, say("inventory-svc, ledger-sync, order-router, partner-gateway, report-mailer")], {"answer"},
     None),
    ("b4-06", "hedged", lambda: [TLS_READ, say("ledger-sync, partner-gateway or report-mailer")], {"answer"}, None),
    ("b4-06", "hedged with an extra candidate",
     lambda: [TLS_READ, say("ledger-sync, partner-gateway, report-mailer, and possibly fx-feed")], {"answer"}, None),

    ("b4-07", "counted rejected attempts, not callbacks", lambda: report(distinct=False), {"exact"}, None),
    ("b4-07", "used the local (file) date instead of the UTC day", lambda: report(day_of="local"),
     {"exact", "boundary_days"}, None),
    ("b4-07", "matched M-44710 too", lambda: report(merchant_prefix=True), {"exact"}, None),
    ("b4-07", "counted staging as well",
     lambda: report(services=("logs/prod/payments-gw/", "logs/staging/payments-gw/")), {"exact"}, None),
    ("b4-07", "counted every rejection reason",
     lambda: report(reasons=("signature_invalid", "timestamp_skew", "unknown_key", "payload_invalid")), {"exact"},
     None),
    ("b4-07", "counted the refund hook's callbacks too",
     lambda: report(services=("logs/prod/payments-gw/", "logs/prod/refunds-hook/")), {"exact"}, None),
    ("b4-07", "left out the days with none",
     lambda: [write(REPORT, "".join(line + "\n" for line in gen("b4a_files").sig_csv(gen("b4a_files").sig_failures())
                                    .splitlines() if not line.endswith(",0")))], {"exact", "shape"}, None),

    ("b4-08", "replaced it everywhere it appears",
     lambda: [moved([*DB_TARGETS, f"{MC}/prod/services/report-mailer.env.bak",
                     "infra/archive/2026-06/regions/me-central-1/prod/services/ledger-sync.env",
                     f"{MC}/prod-2025/services/ledger-sync.env"])],
     {"kept_me_central_1_prod_services_report_mailer_env_bak",
      "kept_archive_2026_06_regions_me_central_1_prod_services_ledger_sync",
      "kept_me_central_1_prod_2025_services_ledger_sync"}, None),
    ("b4-08", "moved staging and dev as well",
     lambda: [moved([*DB_TARGETS, "infra/regions/eu-west-1/staging/env.env",
                     "infra/regions/eu-west-1/dev/services/billing-api.env"])],
     {"kept_eu_west_1_staging_env", "kept_eu_west_1_dev_services_billing_api"}, None),
    ("b4-08", "replaced the prefix pg-legacy-2, hitting pg-legacy-20",
     lambda: [moved([*DB_TARGETS, f"{MC}/prod/services/audit-trail.env"], old="pg-legacy-2", new="pg-main-1")],
     {"kept_me_central_1_prod_services_audit_trail"}, None),
    ("b4-08", "changed only DB_HOST keys (missed DATABASE_URL and DB_HOST_RO)",
     lambda: [moved([p for p in DB_TARGETS if not p.endswith(("ledger-sync.env", "billing-api.env"))])],
     {"moved_me_central_1_prod_services_ledger_sync", "moved_ap_south_1_prod_services_billing_api"}, None),
    ("b4-08", "changed only service files, not the environment layer",
     lambda: [moved(DB_TARGETS[1:])], {"moved_eu_west_1_prod_env"}, None),
    ("b4-08", "edited the runbook too",
     lambda: [moved([*DB_TARGETS, "infra/runbooks/database-failover.md"])], {"kept_runbooks_database_failover_md"},
     None),
]


@pytest.mark.parametrize(("task_id", "trap", "script", "must_fail", "recipe"), TRAPS,
                         ids=[f"{t[0]}: {t[1]}" for t in TRAPS])
async def test_b4a_trap_costs_points(factory, task_id, trap, script, must_fail, recipe):
    status, failed = await failed_checks(factory, task_id, *script(), recipe=recipe)
    assert status != "SUCCESS"
    assert must_fail <= failed, f"{trap}: expected {must_fail} to fail, failed {failed}"


@pytest.mark.parametrize("answer", ["ledger-sync, partner-gateway, report-mailer",
                                    "report-mailer, ledger-sync and partner-gateway",
                                    "Partner-Gateway\nLedger-Sync\nReport-Mailer"])
async def test_b4_06_accepts_any_order_and_case(factory, answer):
    status, failed = await failed_checks(factory, "b4-06", TLS_READ, say(answer))
    assert status == "SUCCESS" and not failed


@pytest.mark.parametrize("task_id", [t for t in B4A])
async def test_b4a_reference_replays_within_budget(factory, task_id):
    """What smoke does for visible tasks: the scripted model replays the reference through a real harness and
    finishes inside its step, tool and token budgets."""
    task = factory.tasks.get(task_id)
    rec = await run_one(factory, task, recipe_for(factory, task_id), "scripted/replay", "scripted")
    assert rec.status.value == "SUCCESS", (rec.status, rec.scores)


def test_b4a_seeds_and_tasks_match_their_generator(tmp_path):
    spec = importlib.util.spec_from_file_location("b4a_build", TASKGEN / "b4a_build.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    out = tmp_path / "catalog"
    mod.build(out)
    generated = sorted(p.relative_to(out) for p in out.rglob("*") if p.is_file())
    assert len(generated) > 500
    drift = [str(p) for p in generated if (out / p).read_bytes() != (settings.CATALOG / p).read_bytes()]
    assert not drift, f"edited by hand or not rebuilt: {drift[:10]} (run scripts/taskgen/b4a_build.py)"
    committed = {p.relative_to(settings.CATALOG) for d in [*settings.CATALOG.glob("seeds/b4a-*"),
                                                           *[settings.CATALOG / "tasks" / t for t in B4A]]
                 for p in d.rglob("*") if p.is_file() and "__pycache__" not in p.parts}
    assert committed == set(generated), sorted(map(str, committed ^ set(generated)))[:10]
    shutil.rmtree(out)


def test_b4a_seeds_are_large_but_bounded():
    for d in settings.CATALOG.glob("seeds/b4a-*"):
        size = sum(p.stat().st_size for p in d.rglob("*") if p.is_file())
        assert size < 2_000_000, (d.name, size)
