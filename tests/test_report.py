"""Campaign report (hfx report): paired deltas and a well-formed markdown report from real scripted runs."""

from typer.testing import CliRunner

from harness_factory.results.cost import CostBaseline
from harness_factory.results.report import build_report, paired_delta
from harness_factory.runner.runner import plan, run_campaign


def test_paired_delta_counts_and_interval():
    d = paired_delta([(0.5, 0.75), (0.5, 0.25), (1.0, 1.0), (0.0, 0.5)])
    assert d["n"] == 4 and (d["wins"], d["losses"], d["ties"]) == (2, 1, 1)
    assert d["mean"] == 0.125 and d["ci"][0] <= d["mean"] <= d["ci"][1]
    assert paired_delta([]) == {"n": 0}


def tables_are_well_formed(md: str) -> bool:
    """Every row of a markdown table has as many cells as its header (a stray | would break it)."""
    widths = []
    for line in md.splitlines():
        if line.startswith("|"):
            widths.append(line.count("|"))
        elif widths:
            if len(set(widths)) != 1:
                return False
            widths = []
    return len(set(widths)) <= 1


async def test_report_from_a_campaign(factory, p0_tasks):
    await run_campaign(factory, plan(factory, ["v2-base", "v2-recover"], "scripted", ["scripted/invent"],
                                     task_ids=p0_tasks), concurrency=4)
    rows = factory.store.rows()
    md = build_report(factory, rows, CostBaseline.from_rows(rows), title="T")
    assert md.startswith("# T") and tables_are_well_formed(md)
    for section in ("## 1. Scope", "## 2. Recipes", "## 3. Matrix", "## 4. Selector", "## 5. Scored", "## 6. Not"):
        assert section in md
    matrix = md.split("## 3.")[1].split("## 4.")[0]
    assert "`v2-recover` | `v2-base` | `scripted/invent` | 5 |" in matrix and "5/0/0 | better" in matrix
    assert "SINGLE_STEP / REVERSIBLE / R1" in md                     # class keys readable, no stray pipes
    failures = md.split("## 5.")[1].split("## 6.")[0]
    assert failures.count("`v2-base`") == 5 and "malformed_output" in failures and "`v2-recover`" not in failures


def test_report_cli_writes_markdown(factory, tmp_path, monkeypatch):
    from harness_factory.cli import main as cli
    monkeypatch.setattr(cli, "_factory", lambda store=True: factory)
    out = tmp_path / "r.md"
    res = CliRunner().invoke(cli.app, ["report", "--out", str(out), "--title", "Empty"])
    assert res.exit_code == 0, res.output
    text = out.read_text()
    assert text.startswith("# Empty") and "No matrix recipe has runs paired" in text
