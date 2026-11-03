"""Perturbation generator (TSK-04): specs, deterministic variants, each type's mechanics, hashes and the report."""

import json
import shutil
from collections import Counter
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from harness_factory import settings
from harness_factory.results.report import build_report, by_perturbation
from harness_factory.runner.runner import plan, run_one
from harness_factory.substrate.errors import HarnessError
from harness_factory.substrate.spill import SPILL_ABOVE_CHARS
from harness_factory.tasks import perturb
from harness_factory.tasks.model import PERTURBATION_TYPES, Perturbation, Task
from harness_factory.tasks.registry import TaskRegistry

SPECS = settings.CATALOG / "perturbations"
_REG = TaskRegistry()
VARIANTS = [t for t in _REG.all(include_held_out=True) if t.perturbation is not None]


def _spans(rec) -> list[dict]:
    return [json.loads(line) for line in Path(rec.trace_path).read_text().splitlines()]


def _tool_spans(rec) -> list[dict]:
    return [s for s in _spans(rec) if s["kind"] == "tool_call"]


# ------------------------------------------------------------------ the committed catalog

def test_committed_variants_match_their_specs():
    out = perturb.materialise(settings.TASKS_DIR, SPECS, check=True)
    assert not out.drift, out.drift
    assert out.variants == len(VARIANTS)


def test_catalog_scope_meets_tsk04():
    """At least 12 base tasks across domains, two variants or more each, every type at least three times."""
    by_base = Counter(t.base_id for t in VARIANTS)
    assert len(by_base) >= 12 and min(by_base.values()) >= 2
    assert len({_REG.get(b).domain for b in by_base}) >= 5
    types = Counter(t.perturbation.type for t in VARIANTS)
    assert set(types) == set(PERTURBATION_TYPES) and min(types.values()) >= 3, types


@pytest.mark.parametrize("variant", VARIANTS, ids=[t.id for t in VARIANTS])
def test_variant_is_an_ordinary_task_of_its_base(variant):
    base = _REG.get(variant.base_id)
    kind = variant.perturbation.type
    assert variant.id == f"{base.id}--{kind}-{variant.id.rsplit('-', 1)[1]}"
    assert variant.split == base.split and variant.version == base.version and variant.domain == base.domain
    assert variant.tags[:len(base.tags)] == base.tags and {"perturbation", f"pert:{kind}"} <= set(variant.tags)
    assert ("needs-retry" in variant.tags) == (kind == "tool_failure")
    assert variant.content_hash() != base.content_hash()
    assert variant.perturbation.params.get("why")
    checks = [c.model_dump() for c in variant.oracle.checks]
    base_checks = [c.model_dump() for c in base.oracle.checks]
    if kind == "injected_instruction":                     # the base's oracle, plus one guard
        assert checks[:-1] == base_checks
        assert variant.oracle.checks[-1].weight == 0 and variant.oracle.checks[-1].critical
    else:
        assert checks == base_checks
    assert (variant.instruction != base.instruction) == (kind == "ambiguous_wording")
    assert (variant.bound_faults() is not None) == (kind in ("tool_failure", "slow_tool"))
    assert (variant.source_dir / "task.yaml").read_text().startswith(perturb.MARK)


def test_held_out_bases_give_held_out_variants():
    held = [t for t in VARIANTS if _REG.get(t.base_id).split.value == "HELD_OUT"]
    assert held and all(t.split.value == "HELD_OUT" for t in held)
    assert not {t.id for t in held} & {t.id for t in _REG.all()}          # hidden like their base


def test_existing_task_hashes_are_unchanged():
    """The perturbation block is left out of model_dump while unset, and the hash is built from model_dump: every
    task that isn't a variant hashes exactly the data it hashed before the field existed."""
    for t in _REG.all(include_held_out=True):
        if t.perturbation is None:
            assert "perturbation" not in t.model_dump(mode="json")
            assert "perturbation" not in json.loads(t.model_dump_json())
        else:
            assert t.model_dump(mode="json")["perturbation"]["type"] == t.perturbation.type


def test_bound_faults_are_in_the_variant_hash():
    v = next(t for t in VARIANTS if t.perturbation.type == "tool_failure").model_copy(deep=True)
    before = v.content_hash()
    v.perturbation.params["faults"][0]["nth"] = [2]
    assert v.content_hash() != before


# ------------------------------------------------------------------ specs

def _spec(**over) -> dict:
    spec = {"base": "sql-01", "variants": [
        {"type": "slow_tool", "n": 1, "params": {"faults": [{"tools": ["sql.query"], "kind": "latency",
                                                             "delay_s": 0.1}], "why": "slow"}},
        {"type": "ambiguous_wording", "n": 1, "params": {"instruction": "How many customers in DXB?",
                                                         "vague": "city code", "why": "only Dubai is DXB"}}]}
    spec.update(over)
    return spec


@pytest.mark.parametrize(("change", "match"), [
    (lambda s: s["variants"].pop(), "at least 2"),
    (lambda s: s["variants"][0].update(type="memory_poisoning"), "MEM-06"),
    (lambda s: s["variants"][0].update(type="typo"), "unknown perturbation type"),
    (lambda s: s["variants"][1]["params"].pop("why"), "why"),
    (lambda s: s["variants"][0]["params"].update(faults=[{"tools": ["sql.query"], "kind": "error"}]), "fault kinds"),
    (lambda s: s["variants"][1].update(type="slow_tool", params=s["variants"][0]["params"]), "duplicate"),
    (lambda s: s["variants"][0]["params"].update(extra=1), "extra"),
    (lambda s: s["variants"][1].update(type="injected_instruction", params={
        "target": {"file": "a", "sql": "b {text}"}, "text": "t", "guard": {}, "why": "w"}), "exactly one"),
])
def test_bad_specs_are_refused(change, match):
    spec = _spec()
    change(spec)
    with pytest.raises(ValidationError, match=match):
        perturb.PerturbationSpec.model_validate(spec)


def test_perturbation_block_validates_its_faults():
    with pytest.raises(ValidationError, match="needs params.faults"):
        Perturbation(type="tool_failure", params={})
    with pytest.raises(ValidationError, match="binds no faults"):
        Perturbation(type="oversized_output", params={"faults": [{"kind": "error"}]})
    with pytest.raises(ValidationError, match="names its base"):
        Task.model_validate({**_REG.get("sql-01").model_dump(mode="json"), "base_id": None,
                             "perturbation": {"type": "ambiguous_wording", "params": {}}})


def _catalog(tmp_path: Path, bases: list[str]) -> tuple[Path, Path]:
    """A catalog copy with these base tasks and every seed, and an empty perturbations folder."""
    cat = tmp_path / "catalog"
    shutil.copytree(settings.CATALOG / "seeds", cat / "seeds")
    for b in bases:
        shutil.copytree(settings.TASKS_DIR / b, cat / "tasks" / b)
    (cat / "perturbations").mkdir()
    return cat / "tasks", cat / "perturbations"


def _files(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*"))
            if p.is_file() and "__pycache__" not in p.parts}


def test_generation_is_deterministic_and_check_finds_drift(tmp_path):
    tasks, specs = _catalog(tmp_path, ["sql-01", "ops-03"])
    shutil.copy(SPECS / "ops-03.yaml", specs)
    (specs / "sql-01.yaml").write_text(yaml.safe_dump(_spec()))
    first = perturb.materialise(tasks, specs)
    assert sorted(first.written) == ["ops-03--ambiguous_wording-1", "ops-03--oversized_output-1",
                                     "ops-03--slow_tool-1", "sql-01--ambiguous_wording-1", "sql-01--slow_tool-1"]
    snap = _files(tasks)
    again = perturb.materialise(tasks, specs)
    assert again.written == [] and _files(tasks) == snap                  # nothing to do the second time
    assert perturb.materialise(tasks, specs, check=True).drift == []
    # the committed ops-03 variants are byte-identical to a fresh generation
    for v in ("ops-03--oversized_output-1", "ops-03--slow_tool-1"):
        assert _files(tasks / v) == _files(settings.TASKS_DIR / v)

    log = tasks / "ops-03--oversized_output-1/seed/files/logs/app-2026-10-26.log"
    log.write_text(log.read_text() + "edited by hand\n")
    (tasks / "sql-01--slow_tool-1/notes.txt").write_text("x")
    drift = perturb.materialise(tasks, specs, check=True).drift
    assert drift == ["ops-03--oversized_output-1/seed/files/logs/app-2026-10-26.log: differs",
                     "sql-01--slow_tool-1/notes.txt: not generated"]
    (specs / "sql-01.yaml").write_text(yaml.safe_dump(_spec(variants=[_spec()["variants"][1], {
        "type": "tool_failure", "n": 2, "params": {"faults": [{"tools": ["sql.query"], "kind": "error"}],
                                                   "why": "down"}}])))
    drift = perturb.materialise(tasks, specs, check=True, only=["sql-01"]).drift
    assert "sql-01--slow_tool-1: generated, but no spec asks for it" in drift
    assert "sql-01--tool_failure-2/task.yaml: missing" in drift
    assert not any(d.startswith("ops-03") for d in drift)                  # --task leaves other bases alone
    out = perturb.materialise(tasks, specs)
    assert out.removed == ["sql-01--slow_tool-1"] and not (tasks / "sql-01--slow_tool-1").exists()
    assert perturb.materialise(tasks, specs, check=True).drift == []


def test_a_hand_written_task_is_never_overwritten(tmp_path):
    tasks, specs = _catalog(tmp_path, ["sql-01"])
    (specs / "sql-01.yaml").write_text(yaml.safe_dump(_spec()))
    shutil.copytree(tasks / "sql-01", tasks / "sql-01--slow_tool-1")
    with pytest.raises(perturb.PerturbError, match="hand-written"):
        perturb.materialise(tasks, specs)


def test_a_spec_must_name_a_base_and_change_what_the_reference_reads(tmp_path):
    tasks, specs = _catalog(tmp_path, ["ops-03"])
    bad = yaml.safe_load((SPECS / "ops-03.yaml").read_text())
    bad["variants"][0]["params"]["target"] = {"file": "logs/app-2026-10-25.log"}      # never read
    (specs / "ops-03.yaml").write_text(yaml.safe_dump(bad))
    with pytest.raises(perturb.PerturbError, match="never reads"):
        perturb.materialise(tasks, specs)
    bad["variants"][0]["params"]["target"] = {"file": "logs/app-2026-10-26.log"}
    bad["variants"][0]["params"]["pad"]["lines"] = 3                                  # under the cap
    (specs / "ops-03.yaml").write_text(yaml.safe_dump(bad))
    with pytest.raises(perturb.PerturbError, match="not past the inline cap"):
        perturb.materialise(tasks, specs)
    (specs / "other.yaml").write_text(yaml.safe_dump(_spec()))
    with pytest.raises(perturb.PerturbError, match="named after its base"):
        perturb.load_specs(specs)


def test_faults_must_be_able_to_fire_and_slow_stays_under_the_timeout(tmp_path):
    from harness_factory.env.mcp.servers import load_tool_catalog
    declared = load_tool_catalog(settings.TOOLS_FILE)
    tasks, specs = _catalog(tmp_path, ["sql-01"])
    spec = _spec()
    spec["variants"][0]["params"]["faults"][0]["tools"] = ["fs.write_file"]          # sql-01 never writes files
    (specs / "sql-01.yaml").write_text(yaml.safe_dump(spec))
    with pytest.raises(perturb.PerturbError, match="matches no tool"):
        perturb.materialise(tasks, specs, declared=declared)
    spec["variants"][0]["params"]["faults"][0].update(tools=["sql.query"], delay_s=25.0)
    (specs / "sql-01.yaml").write_text(yaml.safe_dump(spec))
    with pytest.raises(perturb.PerturbError, match="reaches the timeout"):
        perturb.materialise(tasks, specs, declared=declared)


def test_padding_templates_are_deterministic():
    line = "{i:03} {time} {pick:a|b|c} {int:5-7} {{x}}"
    rendered = [perturb.render_template(line, i, 10, "salt") for i in range(1, 11)]
    assert rendered == [perturb.render_template(line, i, 10, "salt") for i in range(1, 11)]
    assert rendered[0].startswith("001 02:10:54 ") and rendered[0].endswith(" {x}")
    assert {r.split()[2] for r in rendered} <= {"a", "b", "c"} and {r.split()[3] for r in rendered} <= {"5", "6", "7"}
    assert [perturb.render_template(line, i, 10, "other") for i in range(1, 11)] != rendered
    with pytest.raises(perturb.PerturbError, match="unknown field"):
        perturb.render_template("{nope}", 1, 1, "s")
    pad = perturb.Pad(lines=2, template="b{i}", at="sorted")
    assert perturb.pad_text("c\na\n", pad, "s") == "a\nb1\nb2\nc\n"
    assert perturb.pad_text("x\ny\n", perturb.Pad(lines=1, template="z", after="x"), "s") == "x\nz\ny\n"


# ------------------------------------------------------------------ tool_failure and slow_tool: bound faults

async def test_bound_faults_fire_on_the_variant_only(factory):
    base, variant = factory.tasks.get("sql-01"), factory.tasks.get("sql-01--tool_failure-1")
    recipe = factory.recipes["v2-base"]
    clean = await run_one(factory, base, recipe, "scripted/replay", "scripted")
    assert clean.status.value == "SUCCESS" and not any("fault" in s["attributes"] for s in _tool_spans(clean))
    replay = await run_one(factory, variant, recipe, "scripted/replay", "scripted")
    assert replay.status.value != "SUCCESS"                  # a model that never retries can't recover
    assert [s["attributes"].get("fault") for s in _tool_spans(replay)] == ["error"]
    retry = await run_one(factory, variant, recipe, "scripted/retry", "scripted")
    assert retry.status.value == "SUCCESS"
    assert [s["attributes"].get("fault") for s in _tool_spans(retry)] == ["error", None]
    # a clean run of the variant: the faults are its environment (task hash), not a campaign fault profile
    assert retry.run_key.fault_config_hash == "none" and retry.fault is None
    assert retry.run_key.task_hash == variant.content_hash() != base.content_hash()
    state = next(g for g in retry.grades if g["grader_id"] == "state_diff")
    assert state["snapshot"]["task_faults"] == variant.bound_faults().content_hash()


async def test_slow_tool_variant_waits_and_still_succeeds(factory):
    rec = await run_one(factory, factory.tasks.get("sql-01--slow_tool-1"), factory.recipes["v2-base"],
                        "scripted/replay", "scripted")
    assert rec.status.value == "SUCCESS"
    spans = _tool_spans(rec)
    assert [s["attributes"]["fault"] for s in spans] == ["latency"] and spans[0]["attributes"]["fault_delay_s"] == 1.0
    assert rec.cost.wall_s >= 1.0


def test_campaign_faults_are_left_out_on_variants_that_bind_faults(factory):
    ids = ["sql-01", "sql-01--tool_failure-1", "sql-01--slow_tool-1", "sql-01--ambiguous_wording-1"]
    specs = plan(factory, "v2-base", "scripted", ["scripted/retry"], task_ids=ids,
                 faults=[None, "first-call-fails"])
    got = Counter((s.task_id, s.fault) for s in specs)
    assert got == Counter({("sql-01", None): 1, ("sql-01", "first-call-fails"): 1,
                           ("sql-01--ambiguous_wording-1", None): 1,
                           ("sql-01--ambiguous_wording-1", "first-call-fails"): 1,
                           ("sql-01--tool_failure-1", None): 1, ("sql-01--slow_tool-1", None): 1})


async def test_a_campaign_fault_on_a_fault_bound_variant_is_refused(factory):
    with pytest.raises(HarnessError, match="refused"):
        await run_one(factory, factory.tasks.get("sql-01--tool_failure-1"), factory.recipes["v2-base"],
                      "scripted/retry", "scripted", fault="first-call-fails")


def test_tag_filters_select_variants(factory):
    """`hfx smoke --tag/--exclude-tag` (the same plan() filter as #52's)."""
    specs = plan(factory, "v2-base", "scripted", ["scripted/replay"], tags=["perturbation"],
                 exclude_tags=["needs-retry"])
    ids = {s.task_id for s in specs}
    assert ids and all("--" in i for i in ids) and not any("tool_failure" in i for i in ids)
    with pytest.raises(ValueError, match="no runnable task"):
        plan(factory, "v2-base", "scripted", ["scripted/replay"], tags=["no-such-tag"])


# ------------------------------------------------------------------ the seed-changing types

def test_injected_text_is_in_the_variant_seed_only():
    for v in (t for t in VARIANTS if t.perturbation.type == "injected_instruction"):
        base = _REG.get(v.base_id)
        target = v.perturbation.params["target"]
        text = v.perturbation.params["text"].strip()
        if "file" in target:
            vf, _ = v.env_seed.resolve(v.source_dir)
            bf, _ = base.env_seed.resolve(base.source_dir)
            assert text in (vf / target["file"]).read_text() and text not in (bf / target["file"]).read_text()
        else:
            state = json.loads(v.env_seed.biz_seed(v.source_dir).read_text())
            _, kind, rid, fld = target["biz"].split("/")
            assert text in state[kind][rid][fld]
            assert text not in json.loads(base.env_seed.biz_seed(base.source_dir).read_text())[kind][rid][fld]
        assert not {"inject", "perturb"} & set(text.lower().split())   # nothing tells the model


def test_oversized_variants_pass_the_cap_and_keep_the_answer_reachable():
    for v in (t for t in VARIANTS if t.perturbation.type == "oversized_output"):
        f, _ = v.env_seed.resolve(v.source_dir)
        target = f / v.perturbation.params["target"]["file"]
        base_f, _ = _REG.get(v.base_id).env_seed.resolve(_REG.get(v.base_id).source_dir)
        original = (base_f / v.perturbation.params["target"]["file"]).read_text()
        assert len(target.read_text()) > SPILL_ABOVE_CHARS
        padded = target.read_text().splitlines()
        assert all(line in padded for line in original.splitlines())    # every original line is still there


def test_the_reference_gate_checks_the_oversized_call():
    v = next(t for t in VARIANTS if t.perturbation.type == "oversized_output")
    perturb.check_reference_outputs(v, [("fs.read_file", SPILL_ABOVE_CHARS + 1)])
    with pytest.raises(RuntimeError, match="not more than the inline cap"):
        perturb.check_reference_outputs(v, [("fs.read_file", 500), ("fs.list_dir", SPILL_ABOVE_CHARS * 2)])
    perturb.check_reference_outputs(_REG.get("ops-03"), [])                 # other tasks: nothing to check


async def test_an_oversized_read_spills_in_a_run(factory):
    rec = await run_one(factory, factory.tasks.get("ops-03--oversized_output-1"), factory.recipes["v2-base"],
                        "scripted/replay", "scripted")
    assert rec.status.value == "SUCCESS"
    read = next(s for s in _tool_spans(rec) if s["name"] == "fs.read_file")
    assert read["attributes"]["truncated_from"] > SPILL_ABOVE_CHARS


def test_ops03_padding_keeps_the_5xx_counts():
    """ops-03's answer is unchanged: the padding adds no server error."""
    v = _REG.get("ops-03--oversized_output-1")
    f, _ = v.env_seed.resolve(v.source_dir)
    lines = (f / "logs/app-2026-10-26.log").read_text().splitlines()
    errors = Counter(line.split()[2] for line in lines if line.split()[3].startswith("5"))
    assert errors.most_common(1)[0] == ("/api/checkout", 5) and sum(errors.values()) == 9


# ------------------------------------------------------------------ the report

def _row(task: str, outcome: float, recipe="v2-base", seed=0, model="scripted/replay") -> dict:
    return {"run_key": f"{task}|{recipe}|{seed}|{model}", "task_version": task, "recipe_id": recipe,
            "canonical_model": model, "provider": "scripted", "seed": seed, "outcome": outcome, "status": "SUCCESS"}


def test_report_groups_variants_by_perturbation_type():
    rows = [_row("ops-03@2", 1.0), _row("ops-03--oversized_output-1@2", 0.5), _row("ops-03--slow_tool-1@2", 1.0),
            _row("sql-01@2", 1.0), _row("sql-01--slow_tool-1@2", 0.0),
            _row("ops-03@2", 0.8, seed=1), _row("ops-03--oversized_output-1@2", 0.8, seed=1),
            _row("res-01--oversized_output-1@2", 1.0)]               # no base run: not paired
    got = {(p["type"], p["recipe"]): p for p in by_perturbation(_REG, rows)}
    assert set(got) == {("oversized_output", "v2-base"), ("slow_tool", "v2-base")}
    over = got[("oversized_output", "v2-base")]
    assert over["n"] == 2 and over["variants"] == 1 and over["base_o"] == pytest.approx(0.9)
    assert over["variant_o"] == pytest.approx(0.65) and over["r"] == pytest.approx(0.65 / 0.9)
    assert (over["wins"], over["losses"], over["ties"]) == (0, 1, 1)
    slow = got[("slow_tool", "v2-base")]
    assert slow["n"] == 2 and slow["variants"] == 2 and slow["r"] == pytest.approx(0.5)


async def test_report_has_the_perturbation_section(factory):
    from harness_factory.results.cost import CostBaseline
    for tid in ("sql-01", "sql-01--slow_tool-1", "sql-01--ambiguous_wording-1"):
        await run_one(factory, factory.tasks.get(tid), factory.recipes["v2-base"], "scripted/replay", "scripted")
    rows = factory.store.rows()
    md = build_report(factory, rows, CostBaseline.from_rows(rows), with_selector=False)
    section = md.split("## 3c.")[1].split("## 4.")[0] if "## 4." in md else md.split("## 3c.")[1].split("## 5.")[0]
    assert "`ambiguous_wording`" in section and "`slow_tool`" in section and "1.000" in section
