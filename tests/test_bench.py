"""hfx bench <file>: a benchmark described in one file, and the report pairing any two recipes."""

import hashlib
import json
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from harness_factory.runner import bench as b

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "docs" / "design" / "benchmark.example.yaml"
FIXTURE = ROOT / "tests" / "fixtures" / "benchmark.scripted.yaml"


def variant(tmp_path: Path, **changes) -> Path:
    """The scripted fixture with top-level sections replaced (None deletes one)."""
    data = yaml.safe_load(FIXTURE.read_text())
    for k, v in changes.items():
        if v is None:
            data.pop(k, None)
        else:
            data[k] = v
    path = tmp_path / "bench.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    return path


def problems_of(path: Path) -> str:
    with pytest.raises(b.BenchFileError) as e:
        b.load(path)
    return " ".join(str(e.value).split())


def invoke(factory, monkeypatch, tmp_path, *args):
    from harness_factory import settings
    from harness_factory.cli import main as cli
    monkeypatch.setattr(cli, "_factory", lambda store=True: factory)
    monkeypatch.setattr(cli.console, "width", 200)
    monkeypatch.setattr(settings, "VAR", tmp_path / "var")
    return CliRunner().invoke(cli.app, list(args))


# ------------------------------------------------------------- the file

def test_the_design_example_is_a_valid_benchmark(factory):
    spec, _ = b.load(EXAMPLE)
    assert b.check(factory, spec) == []
    assert spec.runners == ["native"] and spec.fault_ids() == [None, "first-call-fails"]
    assert spec.providers() == ["groq"] and spec.models_for("groq") == ["gpt-oss-120b", "gpt-oss-20b"]
    assert spec.judge.compare.pairs() == [("v2-base", "v3-staged"), ("v2-base", "v4-planner")]
    specs = b.build_plan(factory, spec)
    tasks = b.select_tasks(factory, spec.tasks)
    assert len(specs) == len(tasks) * 3 * 2 * 2 * 2
    assert all(factory.tasks.get(t).split.value in ("DEV", "TEST") for t in tasks)


@pytest.mark.parametrize(("changes", "message"), [
    ({"recipe": ["v2-base"]}, "recipe: unknown field"),
    ({"runners": ["native", "langgraph-prebuilt"]}, "runners: runner(s) langgraph-prebuilt are planned, not built"),
    ({"tasks": {"splits": ["DEV", "HELD_OUT"]}}, "HELD_OUT tasks are never used for choosing"),
    ({"tasks": {"ids": ["fs-01"], "tag": "batch-3"}}, "give `ids` or `tag`, not both"),
    ({"calibration": {"enabled": True}}, "hfx bench does not run calibration"),
    ({"judge": {"compare": {"base": "v2-base", "against": ["v4-planner"]}}},
     "judge.compare names recipe(s) not in `recipes`: v4-planner"),
    ({"judge": {"score": "E0"}}, "judge.score: Input should be 'E'"),
    ({"seeds": 0}, "seeds: Input should be greater than or equal to 1"),
    ({"recipes": None}, "recipes: Field required"),
    ({"recipes": ["v2-base", "v2-base"]}, "listed more than once: v2-base"),
    ({"models": [{"model": "scripted/replay"}]}, "models.0.provider: Field required"),
    ({"limits": {"concurrency": 0}}, "limits.concurrency: Input should be greater than or equal to 1"),
    ({"name": "has spaces"}, "name: String should match pattern"),
])
def test_invalid_files_are_refused_with_the_field_and_the_reason(tmp_path, changes, message):
    assert message in problems_of(variant(tmp_path, **changes))


def test_files_that_are_not_a_benchmark(tmp_path):
    assert "no such file" in problems_of(tmp_path / "missing.yaml")
    bad = tmp_path / "bad.yaml"
    bad.write_text("name: [unclosed\n")
    assert "not valid YAML" in problems_of(bad)
    bad.write_text("- just a list\n")
    assert "expected a mapping" in problems_of(bad)


def test_the_catalog_must_provide_what_the_file_names(factory, tmp_path):
    spec, _ = b.load(variant(tmp_path, recipes=["v2-base", "v3-staged", "v9-nope"],
                             models=[{"model": "gpt-oss-120b", "provider": "scripted"}],
                             faults=["none", "solar-flare"], tasks={"ids": ["fs-01", "nope-01"]}))
    problems = " ".join(b.check(factory, spec))
    for text in ("unknown recipe 'v9-nope'", "does not serve 'gpt-oss-120b'", "unknown profile 'solar-flare'",
                 "unknown task 'nope-01'"):
        assert text in problems
    spec, _ = b.load(variant(tmp_path, tasks={"splits": ["TEST"], "ids": ["fs-01"]}))
    assert "fs-01 is a DEV task, not in splits ['TEST']" in " ".join(b.check(factory, spec))
    spec, _ = b.load(variant(tmp_path, tasks={"tag": "no-such-tag"}))
    assert "carries tag 'no-such-tag'" in " ".join(b.check(factory, spec))


def test_parse_compare():
    assert b.parse_compare("v2-base:v3-staged, v2-base:v4-planner") == [("v2-base", "v3-staged"),
                                                                        ("v2-base", "v4-planner")]
    for bad in ("v2-base", "v2-base:v2-base", "a:b:c", "a:"):
        with pytest.raises(ValueError):
            b.parse_compare(bad)


# ------------------------------------------------------------- the command

def test_cli_refuses_invalid_files_before_anything_runs(factory, monkeypatch, tmp_path):
    res = invoke(factory, monkeypatch, tmp_path, "bench", str(variant(tmp_path, runners=["llamaindex-agent"])))
    assert res.exit_code == 2 and "planned, not built" in " ".join(res.output.split())
    res = invoke(factory, monkeypatch, tmp_path, "bench", str(variant(tmp_path, recipes=["v9-nope"],
                                                                      judge={"report": True})))
    assert res.exit_code == 2 and "unknown recipe 'v9-nope'" in res.output
    assert not (tmp_path / "var" / "bench").exists()


def test_dry_run_and_preflight_never_start_a_campaign(factory, monkeypatch, tmp_path):
    async def no_campaign(*a, **k):
        raise AssertionError("must not start")

    monkeypatch.setattr("harness_factory.runner.runner.run_campaign", no_campaign)
    dry = invoke(factory, monkeypatch, tmp_path, "bench", str(FIXTURE), "--dry-run")
    assert dry.exit_code == 0, dry.output
    assert "= 20 runs" in dry.output and "projection" in dry.output and "new 20" in dry.output
    assert not (tmp_path / "var" / "bench").exists()                 # a dry run stores nothing
    groq = variant(tmp_path, models=[{"model": "gpt-oss-120b", "provider": "groq"}],
                   limits={"budget_usd": 0.0001, "deadline_days": 30})
    res = invoke(factory, monkeypatch, tmp_path, "bench", str(groq))
    assert res.exit_code == 2 and "limits.budget_usd 0.0001" in " ".join(res.output.split())


def test_the_scripted_example_runs_end_to_end_and_is_stored_with_its_campaign(factory, monkeypatch, tmp_path):
    res = invoke(factory, monkeypatch, tmp_path, "bench", str(FIXTURE))
    assert res.exit_code == 0, res.output
    assert "10/20 SUCCESS" in res.output                            # faulted runs feed R only

    raw = FIXTURE.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    d = tmp_path / "var" / "bench" / f"scripted-first-look-{sha[:12]}"
    assert (d / "benchmark.yaml").read_bytes() == raw                 # the exact file, next to its report
    manifest = json.loads((d / "manifest.json").read_text())
    assert manifest["file_sha256"] == sha and manifest["runs"] == 20 and len(manifest["invocations"]) == 1
    stored = {r["run_key"]: r for r in factory.store.rows()}
    assert set(manifest["run_keys"]) == set(stored)                   # every result traces back to the file
    clean = [stored[k] for k in manifest["run_keys"] if stored[k]["fault"] is None]
    assert len(clean) == 10 and all(r["status"] == "SUCCESS" for r in clean)

    frozen = factory.store.list_baselines()
    assert len(frozen) == 1 and frozen[0].note == f"bench scripted-first-look {sha[:12]}"
    assert manifest["invocations"][0]["baseline"] == frozen[0].id and manifest["invocations"][0]["baseline_frozen"]

    report = (d / "report.md").read_text()
    assert report.startswith("# Benchmark scripted-first-look") and frozen[0].id in report
    pairs = report.split("## 3b.")[1].split("## 4.")[0]
    assert "| `v3-staged` | `v2-base` | `scripted/replay` | scripted | 5 |" in pairs
    assert "## 3. Matrix recipes vs their base" in report             # the matrix section is still there

    again = invoke(factory, monkeypatch, tmp_path, "bench", str(FIXTURE), "--resume")
    assert again.exit_code == 0, again.output
    assert "skipped 20 done, reran 0" in again.output
    assert len(json.loads((d / "manifest.json").read_text())["invocations"]) == 2
    assert len(factory.store.list_baselines()) == 1                   # same medians: the same baseline id

    # hfx report --compare pairs any recipes on the same store; a pair with no runs says so.
    out = tmp_path / "r.md"
    rep = invoke(factory, monkeypatch, tmp_path, "report", "--out", str(out),
                 "--compare", "v2-base:v3-staged,v2-base:v4-planner")
    assert rep.exit_code == 0, rep.output
    text = out.read_text()
    assert "| `v3-staged` | `v2-base` | `scripted/replay` | scripted | 5 |" in text
    assert "No paired scored runs yet for: `v2-base` vs `v4-planner`." in text
    bad = invoke(factory, monkeypatch, tmp_path, "report", "--compare", "v2-base")
    assert bad.exit_code == 2 and "base:variant" in bad.output
