"""The API (#30): the authoring service over HTTP with the same rules, a token guard, and read access to tasks,
models, results, reports and transcripts. Needs the `api` extra."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from harness_factory import settings
from harness_factory.api.app import create_app
from harness_factory.authoring.service import AuthoringService


@pytest.fixture
def repo(tmp_path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    for name in ("catalog", "prompts"):
        shutil.copytree(settings.ROOT / name, root / name)
    shutil.copy(settings.ROOT / "pyproject.toml", root / "pyproject.toml")
    return root


@pytest.fixture
def client(repo) -> TestClient:
    app = create_app(AuthoringService(repo), results_url=f"sqlite:///{repo / 'var/results.db'}",
                     var_dir=repo / "var")
    return TestClient(app)


def test_token_guard(repo):
    c = TestClient(create_app(AuthoringService(repo), token="s3cret"))
    assert c.get("/api/health").status_code == 200                       # open: for liveness checks
    assert c.get("/api/catalog/recipe").status_code == 401
    assert c.get("/api/catalog/recipe", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert c.get("/api/catalog/recipe", headers={"Authorization": "Bearer s3cret"}).status_code == 200


def test_catalog_reads(client):
    recipes = {it["id"]: it for it in client.get("/api/catalog/recipe").json()}
    assert recipes["v2-base"]["hash"] and "recipe v2-search (base)" in recipes["v2-base"]["used_by"]
    item = client.get("/api/catalog/prompt/system/default").json()           # ids with a slash
    assert item["path"] == "prompts/system/default.j2" and "automation agent" in item["text"]
    assert client.get("/api/catalog/prompt/system/nope").status_code == 404
    assert client.get("/api/catalog/widget").status_code == 400
    assert client.get("/api/check").json() == []
    meta = client.get("/api/meta").json()
    assert "V2_TOOL_LOOP" in meta["variants"] and meta["prompt_layers"]["task"] == ["task"]


def test_catalog_writes_follow_the_service_rules(client, repo):
    r = client.post("/api/catalog/recipe", json={"id": "v2-terse", "fields": {"prompts": {"system": "system/terse"}}})
    assert r.status_code == 422 and r.json()["status"] == "rejected"         # the prompt doesn't exist yet
    r = client.post("/api/catalog/prompt", json={"ref": "system/terse", "text": "Be brief. {{ tools }}"})
    assert r.status_code == 201 and (repo / "prompts/system/terse.j2").is_file()
    r = client.post("/api/catalog/recipe?dry_run=true",
                    json={"id": "v2-terse", "fields": {"prompts": {"system": "system/terse"}}})
    assert r.status_code == 200 and r.json()["status"] == "dry_run"
    assert not (repo / "catalog/recipes/matrix/v2_terse.yaml").exists()

    skill = {"name": "Answering from SQL", "description": "Read the schema first.", "body": "1. Describe tables."}
    r = client.put("/api/catalog/skill/sql-analysis", json=skill)
    assert r.status_code == 409 and r.json()["impact"]["recipes_changed"] == ["v2-skills-on-demand",
                                                                              "v2-skills-upfront"]
    assert client.put("/api/catalog/skill/sql-analysis?confirm=true", json=skill).status_code == 200
    assert client.put("/api/catalog/skill/sql-analysis", json={**skill, "id": "other"}).status_code == 400

    r = client.delete("/api/catalog/skill/sql-analysis")
    assert r.status_code == 422 and "still used by" in r.json()["issues"][0]["detail"]
    assert client.delete("/api/catalog/fault/transient").status_code == 200
    assert client.post("/api/catalog/recipe", json={"id": "Not A Slug"}).status_code == 400


def test_server_stub(client):
    r = client.post("/api/servers/stub", json={"id": "calc", "tools": [
        {"name": "add", "side_effect": "READ", "risk": "R0", "params": [{"name": "a", "type": "float"}]}]})
    assert r.status_code == 200 and "def add(a: float) -> str:" in r.json()["code"]
    assert r.json()["path"].endswith("impl/calc_server.py")
    assert client.post("/api/servers/stub", json={"tools": [{"name": "x"}]}).status_code == 422


def test_tasks_models_and_results(client, repo):
    tasks = {t["id"]: t for t in client.get("/api/tasks").json()}
    assert tasks["sql-01"]["task_class"].startswith("SINGLE_STEP")
    providers = {p["provider"]: p for p in client.get("/api/models").json()}
    assert "scripted/replay" in [m["canonical"] for m in providers["scripted"]["models"]]
    assert client.get("/api/results").json() == []

    env = {**os.environ, "HF_ROOT": str(repo), "HF_VAR": str(repo / "var"),
           "HF_RESULTS_URL": f"sqlite:///{repo / 'var/results.db'}", "HF_QUOTA_STATE": str(repo / "var/quota.json")}
    out = subprocess.run([sys.executable, "-c", "from harness_factory.cli.main import app; app()", "run", "--task",
                          "sql-01", "--model", "scripted/replay", "--provider", "scripted"],
                         env=env, capture_output=True, text=True, timeout=300, check=False)
    assert out.returncode == 0, out.stdout + out.stderr

    rows = client.get("/api/results").json()
    assert [(r["recipe_id"], r["status"]) for r in rows] == [("v2-base", "SUCCESS")]
    cells = client.get("/api/results/cells").json()["cells"]
    assert cells[0]["recipe_id"] == "v2-base" and cells[0]["mean_e0"] == 1.0
    t = client.get(f"/api/runs/{rows[0]['id']}/transcript").json()
    assert t["messages"] or t
    assert "# Campaign report" in client.get("/api/report").json()["markdown"]
    assert client.get("/api/runs/999/transcript").status_code == 404
