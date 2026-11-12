"""UTF-8 everywhere (#129, RW-10, RW-16, RW-17): the grader reads agent files as UTF-8, the bench report and its
manifest are written as UTF-8, and `hfx` prints UTF-8 on any console. A Windows machine without PYTHONUTF8 uses
cp1252 as the default encoding; these tests make that the default on any platform."""

from __future__ import annotations

import io
import json
import sqlite3
import sys
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from harness_factory.env.state import EnvState, StateDiff
from harness_factory.grading.base import GradeContext
from harness_factory.grading.state import StateDiffGrader
from harness_factory.runner.spec import RunKey
from harness_factory.shapes.result import RunResult
from harness_factory.tasks.model import WeightedCheck

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "benchmark.scripted.yaml"

NOTE = "Policy § 4.2 — the student’s plan"


@pytest.fixture
def cp1252_default(monkeypatch):
    """Path.read_text / write_text without an encoding use cp1252, as on Windows without PYTHONUTF8."""
    read, write = Path.read_text, Path.write_text

    def read_text(self, encoding=None, errors=None, **kw):
        return read(self, encoding=encoding or "cp1252", errors=errors, **kw)

    def write_text(self, data, encoding=None, errors=None, **kw):
        return write(self, data, encoding=encoding or "cp1252", errors=errors, **kw)

    monkeypatch.setattr(Path, "read_text", read_text)
    monkeypatch.setattr(Path, "write_text", write_text)


def check(**kw) -> WeightedCheck:
    return WeightedCheck(id="c", weight=1, **kw)


def _utf8_files(tmp_path: Path) -> GradeContext:
    """A workspace whose files hold "—", "§" and "’", written as UTF-8 bytes (what the agent's tools write)."""
    ws, db = tmp_path / "ws", tmp_path / "db.sqlite"
    ws.mkdir(parents=True)
    sqlite3.connect(db).close()
    key = RunKey(task_version="t@1", task_hash="h", recipe_hash="r", canonical_model="m", provider="p")
    ctx = GradeContext(task=None, result=RunResult(run_key=key, output=""), before=EnvState(), after=EnvState(),
                       diff=StateDiff(), workspace=ws, db_path=db, spans=[], max_steps=10)
    report = {"audit_lines": [{"check_id": "DA-27", "status": "FAIL", "attribute": "MAJOR-ELEC",
                               "processed": "§ 4.2 — 9 / 12, the student’s deficit 3"}]}
    for name, body in {"note.txt": NOTE + "\n",
                       "doc.json": json.dumps({"note": NOTE}, ensure_ascii=False),
                       "r.json": json.dumps(report, ensure_ascii=False)}.items():
        (ctx.workspace / name).write_bytes(body.encode("utf-8"))
    return ctx


CHECKS = {
    "file_contains": dict(kind="file_contains", path="note.txt", text="§ 4.2 — the student’s"),
    "file_regex": dict(kind="file_regex", path="note.txt", pattern=r"^Policy § \d\.\d — the student’s plan$"),
    "json_schema": dict(kind="json_schema", path="doc.json",
                        json_schema={"type": "object", "required": ["note"], "properties": {"note": {"const": NOTE}}}),
    "json_lines": dict(kind="json_lines", path="r.json",
                       lines={"expected": [{"check_id": "DA-27", "status": "FAIL",
                                            "processed": r"^§ 4\.2 — 9 / 12, the student’s deficit 3$"}]}),
}


def _results(tmp_path: Path) -> dict[str, tuple[bool, str]]:
    ctx = _utf8_files(tmp_path)
    return {name: StateDiffGrader()._check(check(**kw), ctx) for name, kw in CHECKS.items()}


def test_the_grader_reads_agent_files_as_utf8_whatever_the_default(tmp_path, monkeypatch, cp1252_default):
    windows = _results(tmp_path / "cp1252")
    monkeypatch.undo()                                              # the platform's own default (UTF-8 here)
    native = _results(tmp_path / "native")
    assert windows == native
    assert all(ok for ok, _ in native.values()), native


def test_the_state_diff_grader_version_names_the_utf8_reads():
    assert StateDiffGrader.version == "2"                           # "1" read with the locale encoding


def invoke(factory, monkeypatch, tmp_path, *args):
    from harness_factory import settings
    from harness_factory.cli import main as cli
    monkeypatch.setattr(cli, "_factory", lambda store=True: factory)
    monkeypatch.setattr(cli.console, "width", 200)
    monkeypatch.setattr(settings, "VAR", tmp_path / "var")
    return CliRunner().invoke(cli.app, list(args))


def test_the_bench_report_is_utf8_under_a_cp1252_default(factory, monkeypatch, tmp_path, cp1252_default):
    data = yaml.safe_load(FIXTURE.read_bytes())                    # two runs: one task, two recipes, no faults
    data.update(tasks={"splits": ["DEV", "TEST"], "ids": ["fs-01"]}, faults=["none"])
    small = tmp_path / "bench.yaml"
    small.write_bytes(yaml.safe_dump(data, sort_keys=False).encode("utf-8"))
    res = invoke(factory, monkeypatch, tmp_path, "bench", str(small))
    assert res.exit_code == 0, res.output
    (d,) = (tmp_path / "var" / "bench").iterdir()
    report = (d / "report.md").read_bytes().decode("utf-8")        # strict: a cp1252 report would not decode
    assert "mean ΔE" in report and report.startswith("# Benchmark scripted-first-look")
    assert json.loads((d / "manifest.json").read_bytes().decode("utf-8"))["runs"] == 2
    out = tmp_path / "r.md"
    rep = invoke(factory, monkeypatch, tmp_path, "report", "--out", str(out), "--compare", "v2-base:v3-staged")
    assert rep.exit_code == 0, rep.output
    assert "mean ΔE" in out.read_bytes().decode("utf-8")


def _cp1252_stream() -> io.TextIOWrapper:
    return io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict")


def test_a_cp1252_console_prints_anything_after_the_setup(monkeypatch):
    from harness_factory.cli import main as cli
    with pytest.raises(UnicodeEncodeError):                         # what a Windows console did before
        print("🏃 ΔE", file=_cp1252_stream())
    out, err = _cp1252_stream(), _cp1252_stream()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)
    cli._utf8_console()
    print("🏃 ΔE")
    print("§ —", file=sys.stderr)
    out.flush()
    err.flush()
    assert out.buffer.getvalue().decode("utf-8") == "🏃 ΔE\n"
    assert err.buffer.getvalue().decode("utf-8") == "§ —\n"


def test_streams_without_reconfigure_are_left_alone(monkeypatch):
    from harness_factory.cli import main as cli

    class Plain:
        def write(self, s):
            return len(s)

        def flush(self):
            pass

    plain = Plain()
    monkeypatch.setattr(sys, "stdout", plain)
    monkeypatch.setattr(sys, "stderr", plain)
    cli._utf8_console()
    assert sys.stdout is plain
