"""Catalog history (authoring/history.py, versions.py): every authoring change is a version with its files, author
and reason; delete archives; restore and sync; models are editable without losing providers.yaml's comments;
tasks are versioned read-only; results group by the version they ran with."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import pytest

from harness_factory import settings
from harness_factory.authoring.service import AuthoringError, AuthoringService

SKILL = {"id": "csv-checks", "name": "Checking CSV files", "description": "Check CSVs.", "body": "Read the header."}


@pytest.fixture
def repo(tmp_path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    for name in ("catalog", "prompts"):
        shutil.copytree(settings.ROOT / name, root / name)
    return root


@pytest.fixture
def svc(repo, tmp_path) -> AuthoringService:
    return AuthoringService(repo, results_url=f"sqlite:///{tmp_path / 'results.db'}", author="tester")


def test_versions_diff_archive_restore(svc, repo):
    r = svc.create("skill", SKILL, note="first try")
    assert r.status == "applied" and r.version == 1
    r = svc.update("skill", {**SKILL, "body": "Read the header.\nCount the columns."}, note="count columns")
    assert r.version == 2 and "+Count the columns." in r.diff
    h = svc.history("skill", "csv-checks")
    assert [(v["version"], v["action"], v["note"], v["author"]) for v in h["versions"]] == [
        (2, "update", "count columns", "tester"), (1, "create", "first try", "tester")]
    assert "+Count the columns." in svc.diff_versions("skill", "csv-checks", 1, 2)

    r = svc.delete("skill", "csv-checks", note="not useful")
    assert r.status == "applied" and r.action == "archive" and r.version == 3
    assert not (repo / "catalog/skills/csv-checks").exists()
    assert [a["id"] for a in svc.archived_items()] == ["csv-checks"]
    assert svc.restore("skill", "csv-checks", 3).status == "rejected"          # the archive has no files

    r = svc.restore("skill", "csv-checks", 1, note="back to the first try")
    assert r.status == "applied" and r.version == 4
    assert (repo / "catalog/skills/csv-checks/SKILL.md").read_text().strip().endswith("Read the header.")
    assert svc.history("skill", "csv-checks")["versions"][0]["note"] == "restored v1: back to the first try"
    assert svc.archived_items() == []


def test_a_shared_item_versions_the_recipes_it_rehashes(svc):
    body = {"id": "sql-analysis", "name": "x", "description": "y", "body": "z"}
    assert svc.update("skill", body, note="rewrite").status == "needs_confirm"
    assert svc.history("skill", "sql-analysis")["versions"] == []            # nothing recorded without a write
    r = svc.update("skill", body, note="rewrite", confirm=True)
    assert r.status == "applied" and r.version == 2                          # v1 = the state before the change
    deps = {e["id"]: e for e in svc.activity() if e["action"] == "dependency"}
    assert set(deps) == set(r.impact.recipes_changed)
    assert all(e["cause"] == "skill sql-analysis v2" and e["version"] == 2 for e in deps.values())


def test_sync_records_changes_made_outside_authoring(svc, repo):
    first = svc.sync()
    assert first and {e["action"] for e in first} == {"baseline"}
    assert {"task", "model", "recipe", "skill", "prompt", "server", "fault"} <= {e["kind"] for e in first}
    p = repo / "prompts/system/default.j2"
    p.write_text(p.read_text() + "\nBe brief.\n")
    (repo / "catalog/faults/transient.yaml").unlink()
    assert svc.history("prompt", "system/default")["changed_outside"]
    made = {(e["kind"], e["id"]): e for e in svc.sync()}
    assert made[("prompt", "system/default")]["action"] == "external"
    assert made[("fault", "transient")]["note"] == "removed outside authoring"
    assert svc.sync() == []
    assert "+Be brief." in svc.diff_versions("prompt", "system/default", 1, 2)


def test_models_are_edited_in_place_keeping_comments(svc, repo):
    before = (repo / "catalog/providers.yaml").read_text()
    comments = [line for line in before.splitlines() if line.strip().startswith("#")]
    model = {"provider": "groq", "canonical": "gpt-oss-120b", "provider_model_id": "openai/gpt-oss-120b",
             "price_in": 0.2, "price_out": 0.6, "free": True, "caps": {"context_len": 131072}}
    r = svc.update("model", model, note="new price")
    assert r.status == "applied" and not r.impact.models_changed                 # prices are not hashed
    after = (repo / "catalog/providers.yaml").read_text()
    assert [line for line in after.splitlines() if line.strip().startswith("#")] == comments
    assert "price_in: 0.2," in after and "# follows the reference plan" in after
    r = svc.update("model", {**model, "provider_model_id": "openai/gpt-oss-120b-v2"})
    assert r.status == "needs_confirm" and r.impact.models_changed == ["groq/gpt-oss-120b"]
    assert svc.create("model", {"provider": "nope", "canonical": "m", "provider_model_id": "x"}).status == "rejected"
    r = svc.create("model", {"provider": "openrouter", "canonical": "mine", "provider_model_id": "x/mine"})
    assert r.status == "applied" and svc.get("model", "openrouter/mine").detail["provider_model_id"] == "x/mine"
    judge = svc.delete("model", "scripted-judge/scripted/judge")
    assert judge.status == "rejected" and "judge.yaml" in judge.issues[0].detail


def test_tasks_are_read_only_but_versioned(svc, repo):
    assert svc.get("task", "fs-02").text.startswith("# ---- ")
    with pytest.raises(AuthoringError, match="written by hand"):
        svc.update("task", {"id": "fs-02"})
    svc.sync()
    (repo / "catalog/tasks/fs-02/task.yaml").write_text(
        (repo / "catalog/tasks/fs-02/task.yaml").read_text().replace("Leave every", "Keep every"))
    made = [e for e in svc.sync() if e["kind"] == "task"]
    assert [(e["id"], e["action"]) for e in made] == [("fs-02", "external")]


def test_purge_only_archived_and_unused(svc):
    svc.create("skill", SKILL)
    assert svc.purge("skill", "csv-checks").status == "rejected"               # live
    svc.delete("skill", "csv-checks")
    assert svc.purge("skill", "csv-checks").status == "applied"
    assert svc.archived_items() == []


def test_results_group_by_the_recipe_version_they_ran_with(svc, repo, tmp_path, monkeypatch):
    from harness_factory.runner.runner import Factory, run_one
    monkeypatch.setattr(settings, "ROOT", repo)                 # the run reads the copy's catalog
    for attr, sub in (("CATALOG", "catalog"), ("RECIPES_DIR", "catalog/recipes"), ("PROMPTS", "prompts"),
                      ("TOOLS_FILE", "catalog/tools.yaml"), ("PROVIDERS_FILE", "catalog/providers.yaml"),
                      ("SKILLS_DIR", "catalog/skills"), ("FAULTS_DIR", "catalog/faults")):
        monkeypatch.setattr(settings, attr, repo / sub)
    svc.sync()
    f = Factory.load(results_url=svc.results_url, quota_state=tmp_path / "q.json")
    asyncio.run(run_one(f, f.tasks.get("fs-02"), f.recipes["v2-recover"], "scripted/replay", "scripted", 0))
    r = svc.update("recipe", {"id": "v2-recover", "base": "v2-base", "fields": {"config": {"recover_malformed": 1}}},
                   note="one retry", confirm=True)
    assert r.status == "applied" and r.version == 2
    f = Factory.load(results_url=svc.results_url, quota_state=tmp_path / "q.json")
    asyncio.run(run_one(f, f.tasks.get("fs-02"), f.recipes["v2-recover"], "scripted/replay", "scripted", 0))
    by = {v["version"]: v for v in svc.results_by_version("recipe", "v2-recover")["versions"]}
    assert by[1]["n"] == 1 and by[2]["n"] == 1 and by[2]["mean_e0"] == 1.0


def test_history_over_the_api(repo, tmp_path):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from harness_factory.api.app import create_app
    c = TestClient(create_app(AuthoringService(repo, author="api-user"), results_url=f"sqlite:///{tmp_path / 'r.db'}",
                              var_dir=tmp_path / "var"))
    assert c.post("/api/catalog/skill", json=SKILL, params={"note": "first"}).status_code == 201
    assert c.put("/api/catalog/skill/csv-checks", json={**SKILL, "body": "v2"}, params={"note": "second"}).json()[
        "version"] == 2
    h = c.get("/api/history/skill/csv-checks").json()
    assert [v["note"] for v in h["versions"]] == ["second", "first"] and h["versions"][0]["author"] == "api-user"
    assert "+v2" in c.get("/api/history/skill/csv-checks/diff", params={"a": 1, "b": 2}).json()["diff"]
    assert c.post("/api/history/skill/csv-checks/tags", json={"version": 1, "tags": ["baseline"]}).status_code == 200
    assert c.get("/api/history/skill/csv-checks").json()["versions"][1]["tags"] == ["baseline"]
    assert c.delete("/api/catalog/skill/csv-checks", params={"note": "gone"}).json()["action"] == "archive"
    assert [a["id"] for a in c.get("/api/archived").json()] == ["csv-checks"]
    assert c.post("/api/history/skill/csv-checks/restore", json={"version": 2}).json()["version"] == 4
    assert c.get("/api/activity").json()[0]["action"] == "restore"
    assert c.get("/api/history/skill/csv-checks/version/1").json()["parts"]
    assert c.get("/api/history/skill/nope/version/9").status_code == 404
    assert c.get("/api/whoami").json() == {"author": "api-user"}


def test_editing_files_as_text(svc, repo):
    parts = svc.version_parts("skill", "editing-files", "current")
    (path, text), = parts.items()
    r = svc.update_parts("skill", "editing-files", {path: text}, dry_run=True)
    assert r.status == "dry_run" and r.notes == ["no change"]
    edited = text.rstrip("\n") + "\nRe-read the file after writing it.\n"
    r = svc.update_parts("skill", "editing-files", {path: edited}, note="re-read", confirm=True)
    added = [ln for ln in r.diff.splitlines() if ln.startswith("+") and not ln.startswith("+++")]
    assert r.status == "applied" and added == ["+Re-read the file after writing it."]   # exactly the line added
    assert (repo / path).read_text() == edited
    assert svc.update_parts("skill", "editing-files", {"catalog/elsewhere.md": "x"}).status == "rejected"
    with pytest.raises(AuthoringError, match="through its form"):
        svc.update_parts("server", "fs", {"x": "y"})
    bad = svc.update_parts("recipe", "v2-recover", {"catalog/recipes/matrix/v2_recover.yaml": "id: v2-recover\n"
                                                    "base: v2-base\nconfig: {max_steps: -1}\n"})
    assert bad.status == "rejected"                                            # the factory's validators refuse it


def test_payloads_round_trip_without_identity_change(svc):
    for kind, item in (("skill", "sql-analysis"), ("recipe", "v2-recover"), ("fault", "transient"),
                       ("model", "groq/gpt-oss-120b"), ("prompt", "system/default")):
        r = svc.update(kind, svc.payload(kind, item), dry_run=True)
        assert r.status == "dry_run" and not r.impact.touches_existing, (kind, r.impact)
