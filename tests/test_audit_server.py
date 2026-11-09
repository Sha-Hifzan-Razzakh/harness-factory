"""The degree audit server (ADR 0021): the pinned core, the read-only reference mount, the server answering exactly
what the core answers, refusals with the core's codes, and identity (task hash, env hash) moving only where it
should."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import stat
import subprocess
from pathlib import Path

import pytest
import yaml
from degree_audit_core import tools as core
from degree_audit_core.adapters.sqlite_sis import SqliteSis
from degree_audit_core.reference import Reference

from harness_factory import settings
from harness_factory.env import sandbox_image
from harness_factory.env.mcp import deps
from harness_factory.env.sandbox import REF_FILE, DockerSandbox, LocalSandbox
from harness_factory.mcp.pool import MCPSessionPool
from harness_factory.substrate.errors import HarnessError
from harness_factory.tasks.model import load_task

CONFORMANCE = settings.CATALOG / "conformance"
SIS_SQL = CONFORMANCE / "seed" / "audit" / "sis.sql"
REFERENCE = settings.CATALOG / "seeds" / "degree-audit-2026.11.0" / "reference.json"
CORE = deps.ALLOWED["degree-audit"]


def make_task(root: Path, reference: bool = True) -> Path:
    t = root / "audit-task"
    (t / "seed").mkdir(parents=True)
    shutil.copyfile(SIS_SQL, t / "seed" / "sis.sql")
    seed = {"sqlite": "seed/sis.sql"}
    if reference:
        shutil.copyfile(REFERENCE, t / "seed" / "reference.json")
        seed["reference"] = "seed/reference.json"
    (t / "task.yaml").write_text(yaml.safe_dump({
        "id": "audit-task", "domain": "data", "instruction": "Audit S20231047.", "env_seed": seed,
        "allowed_tools": ["audit.validate_record", "audit.evaluate", "audit.explain"],
        "oracle": {"checks": [{"id": "a", "kind": "output_regex", "pattern": "NOT_ELIGIBLE", "weight": 1}]}}))
    return t


@pytest.fixture
def task(tmp_path):
    return load_task(make_task(tmp_path))


@pytest.fixture
def sb(task):
    with LocalSandbox("audit-test") as s:
        s.reset(task)
        yield s


async def call(pool, tool, **args):
    r = await pool.call(f"audit.{tool}", args)
    return r if r.is_error else json.loads(r.text)


def direct(sb, fn, *args, **kwargs):
    """The same call on the core itself, over the same SIS and reference."""
    sis = SqliteSis(sb.db_path)
    try:
        return fn(sis, *args, ref=Reference.load(REFERENCE), **kwargs)
    finally:
        sis.close()


# ------------------------------------------------------------------ the pin

def test_the_core_is_pinned_to_one_commit_not_a_tag():
    assert CORE.version == "0.1.0" and CORE.imports == "degree_audit_core"
    assert CORE.commit is not None and len(CORE.commit) == 40 and int(CORE.commit, 16) >= 0
    assert CORE.requirement == f"degree-audit @ {CORE.source}"
    assert deps.requirements("harness_factory.env.mcp.impl.audit_server") == [CORE.requirement]
    assert deps.source_requirements(tuple(deps.ALLOWED)) == [CORE.requirement]


def test_the_installed_core_is_the_pinned_commit():
    assert deps.problems(("degree-audit",)) == []


def test_a_core_built_from_another_commit_is_refused(monkeypatch):
    monkeypatch.setattr(deps, "_installed_commit", lambda dist: "0" * 40)
    [problem] = deps.problems(("degree-audit",))
    assert "not from the pinned commit" in problem and CORE.commit[:12] in problem


def test_the_image_builds_the_git_pinned_core_without_wheels(monkeypatch, tmp_path):
    ran = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: ran.append(cmd) or subprocess.CompletedProcess(cmd, 0))
    sandbox_image._vendor(tmp_path / "target", "3.13", "x86_64", tmp_path)
    binary, sourced = ran
    assert "--only-binary" in binary and CORE.requirement not in binary
    assert sourced[-1] == CORE.requirement and "--no-deps" in sourced and "--only-binary" not in sourced


# ------------------------------------------------------------------ the reference mount

def test_the_reference_is_mounted_read_only_outside_the_workspace(task, sb):
    mounted = sb.ref_dir / REF_FILE
    assert sb.env()["HF_REFERENCE"] == str(mounted) and not sb.ref_dir.is_relative_to(sb.workspace)
    assert mounted.read_bytes() == REFERENCE.read_bytes()
    assert not list(sb.workspace.rglob("*"))
    if os.name == "posix":
        assert stat.S_IMODE(mounted.stat().st_mode) == 0o444
        assert stat.S_IMODE(sb.ref_dir.stat().st_mode) == 0o555
        if os.geteuid() != 0:                                                # root ignores file modes
            with pytest.raises(PermissionError):
                mounted.write_text("{}")
    before = sb.ref_dir.stat().st_ino
    sb.reset(task)                                    # the same folder (a Docker bind mount stays valid), refilled
    assert sb.ref_dir.stat().st_ino == before and mounted.read_bytes() == REFERENCE.read_bytes()


def test_a_task_without_a_reference_mounts_nothing(tmp_path):
    with LocalSandbox("audit-none") as s:
        s.reset(load_task(make_task(tmp_path, reference=False)))
        assert not list(s.ref_dir.iterdir())
    assert not s.root.exists()


def test_a_missing_reference_file_is_a_setup_error(tmp_path):
    t = make_task(tmp_path)
    (t / "seed" / "reference.json").unlink()
    with LocalSandbox("audit-missing") as s, pytest.raises(HarnessError, match="is not a file"):
        s.reset(load_task(t))


def test_docker_mounts_the_reference_read_only(tmp_path):
    sb = DockerSandbox.__new__(DockerSandbox)
    sb.label, sb.image, sb.container = "x", "img", "hf-x"
    sb.workspace, sb.db_path = tmp_path / "workspace", tmp_path / "state" / "task.sqlite"
    assert f"{tmp_path / 'state' / 'reference'}:/state/reference:ro" in sb.run_args()
    assert sb.env()["HF_REFERENCE"] == f"/state/reference/{REF_FILE}"


# ------------------------------------------------------------------ the server is the core

async def test_evaluate_answers_exactly_what_the_core_answers(factory, sb):
    async with MCPSessionPool(factory.declared, ["audit"], sb.env()) as pool:
        got = await call(pool, "evaluate", student_id="S20231047")
        what_if = await call(pool, "evaluate", student_id="S20221356", mode="what_if", study_plan_version="BSCS-2025")
    assert got == direct(sb, core.evaluate, "S20231047")
    assert got["recommendation"] == "NOT_ELIGIBLE"
    assert what_if == direct(sb, core.evaluate, "S20221356", None, "what_if", None, "BSCS-2025")


async def test_validate_and_explain_answer_what_the_core_answers(factory, sb):
    async with MCPSessionPool(factory.declared, ["audit"], sb.env()) as pool:
        v = await call(pool, "validate_record", student_id="S29990002")
        e = await call(pool, "explain", student_id="S20231047", requirement_id="MAJOR-ELEC")
    assert v == direct(sb, core.validate_record, "S29990002") and v["blocking"] is True
    assert e == direct(sb, core.explain, "S20231047", "MAJOR-ELEC")


async def test_a_refusal_reaches_the_model_with_the_core_code(factory, sb):
    async with MCPSessionPool(factory.declared, ["audit"], sb.env()) as pool:
        unknown = await call(pool, "evaluate", student_id="S20299999")
        no_req = await call(pool, "explain", student_id="S20231047", requirement_id="NOT-A-REQ")
    assert unknown.is_error and "UNKNOWN_STUDENT: S20299999 is not in the SIS" in unknown.text
    assert no_req.is_error and "UNKNOWN_REQUIREMENT" in no_req.text


async def test_the_server_never_writes_the_sis(factory, sb):
    before = sb.db_path.read_bytes()
    async with MCPSessionPool(factory.declared, ["audit"], sb.env()) as pool:
        for sid in ("S20231047", "S20222001", "S29990002"):
            await call(pool, "evaluate", student_id=sid)
    assert sb.db_path.read_bytes() == before


async def test_without_a_reference_the_server_says_so(factory, tmp_path):
    with LocalSandbox("audit-noref") as s:
        s.reset(load_task(make_task(tmp_path, reference=False)))
        async with MCPSessionPool(factory.declared, ["audit"], s.env()) as pool:
            r = await call(pool, "evaluate", student_id="S20231047")
    assert r.is_error and "no reference data is mounted" in r.text


def test_the_tool_descriptions_are_the_cores(factory):
    from degree_audit_core.descriptions import DESCRIPTIONS
    spec = next(s for s in factory.declared.servers if s.id == "audit")
    assert {t.name for t in spec.tools} == {k.removeprefix("audit.") for k in DESCRIPTIONS}
    assert all(t.side_effect.value == "READ" and t.risk == "R0" for t in spec.tools)


# ------------------------------------------------------------------ identity

def test_the_reference_is_part_of_the_task_hash(tmp_path):
    t = make_task(tmp_path)
    before = load_task(t).content_hash()
    ref = json.loads((t / "seed" / "reference.json").read_text())
    ref["release"] = {**ref.get("release", {}), "dataset": "2099.1.0"}
    (t / "seed" / "reference.json").write_text(json.dumps(ref))
    assert load_task(t).content_hash() != before


def test_a_task_without_a_reference_keeps_its_hash(tmp_path):
    t = make_task(tmp_path, reference=False)
    data = yaml.safe_load((t / "task.yaml").read_text())
    task = load_task(t)
    dumped = task.model_dump(mode="json")["env_seed"]
    assert dumped["reference"] is None and data["env_seed"] == {"sqlite": "seed/sis.sql"}
    import harness_factory.tasks.model as m
    assert "reference" in m._SEED_OMIT_WHEN_NONE


def test_the_core_pin_is_in_the_env_hash_of_audit_runs_only(factory, monkeypatch):
    with_audit = factory.declared.env_hash(["sql", "audit"], "m")
    without = factory.declared.env_hash(["sql"], "m")
    moved = deps.Library(CORE.dist, CORE.version, CORE.imports, source=CORE.source.rsplit("@", 1)[0] + "@" + "1" * 40)
    monkeypatch.setitem(deps.ALLOWED, "degree-audit", moved)
    assert factory.declared.env_hash(["sql", "audit"], "m") != with_audit
    assert factory.declared.env_hash(["sql"], "m") == without


def test_the_conformance_sis_is_the_one_in_the_catalog(tmp_path):
    db = tmp_path / "sis.sqlite"
    with sqlite3.connect(db) as c:
        c.executescript(SIS_SQL.read_text())
    sis = SqliteSis(db)
    try:
        assert sis.student_ids() == ["S20221356", "S20222001", "S20231047", "S29990001", "S29990002"]
    finally:
        sis.close()
