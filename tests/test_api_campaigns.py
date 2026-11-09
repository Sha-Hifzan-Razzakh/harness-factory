"""Background campaigns over the API (#31): same plan and preflight as `hfx smoke`, progress by polling, resume,
cancel with cleanup, and a recipe authored through the API runnable right away."""

from __future__ import annotations

import time

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from harness_factory import settings
from harness_factory.api.app import create_app
from harness_factory.authoring.service import AuthoringService

PAIR = {"recipes": ["v2-base"], "models": ["scripted/replay"], "tasks": ["sql-01", "fs-01"], "concurrency": 2}


@pytest.fixture
def client(tmp_path):
    app = create_app(AuthoringService(settings.ROOT), results_url=f"sqlite:///{tmp_path / 'results.db'}",
                     var_dir=tmp_path, quota_state=tmp_path / "quota.json")
    with TestClient(app) as c:              # the app's event loop keeps running between requests
        yield c


def _wait(client, cid: str, timeout_s: float = 240) -> dict:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        c = client.get(f"/api/campaigns/{cid}").json()
        if c["state"] != "running":
            return c
        time.sleep(0.5)
    raise AssertionError(f"campaign {cid} still running after {timeout_s}s")


def test_plan_and_invalid_requests(client):
    p = client.post("/api/campaigns/plan", json=PAIR).json()
    assert (p["total"], p["new"], p["will_run"], p["problems"]) == (2, 2, 2, [])
    assert p["projection"][0]["bucket"] == "scripted"
    assert client.post("/api/campaigns/plan", json={**PAIR, "recipes": ["nope"]}).status_code == 400
    r = client.post("/api/campaigns/plan", json={**PAIR, "models": ["gpt-oss-120b"]})
    assert r.status_code == 400 and "does not serve" in r.json()["detail"]
    assert client.post("/api/campaigns/plan", json={**PAIR, "faults": ["melt"]}).status_code == 400
    assert client.get("/api/campaigns/nope").status_code == 404


def test_run_poll_and_resume(client):
    r = client.post("/api/campaigns", json=PAIR)
    assert r.status_code == 202 and r.json()["state"] == "running"
    c = _wait(client, r.json()["id"])
    assert c["state"] == "done", c
    assert c["progress"]["finished"] == 2 and c["progress"]["statuses"] == {"SUCCESS": 2}
    assert c["result"]["statuses"] == {"SUCCESS": 2} and c["result"]["errors"] == []
    assert [x["id"] for x in client.get("/api/campaigns").json()] == [c["id"]]

    r = client.post("/api/campaigns", json={**PAIR, "resume": True})      # everything is scored already
    assert r.status_code == 409 and "nothing to run" in r.json()["detail"]["problems"][0]
    p = client.post("/api/campaigns/plan", json=PAIR).json()
    assert p["done"] == 2 and "will run again" in p["warnings"][0]


def test_cancel_cleans_up(client, own_sandboxes_dir):
    r = client.post("/api/campaigns", json={"recipes": ["v3-staged"], "models": ["scripted/replay"],
                                            "concurrency": 1})
    cid = r.json()["id"]
    time.sleep(1.0)
    assert client.delete(f"/api/campaigns/{cid}").status_code == 202
    c = _wait(client, cid)
    assert c["state"] == "cancelled" and c["progress"]["finished"] < c["plan"]["will_run"]
    assert list(own_sandboxes_dir.iterdir()) == []           # no sandbox outlives a cancelled run
    assert client.delete(f"/api/campaigns/{cid}").status_code == 409


def test_authored_recipe_runs_without_restart(tmp_path, monkeypatch):
    """The API reloads its catalog when a file changes, keeping its quota ledger, so a recipe written through it
    can be run by the next campaign. Everything here works on a copy of the catalog."""
    import shutil
    repo = tmp_path / "repo"
    repo.mkdir()
    for name in ("catalog", "prompts"):
        shutil.copytree(settings.ROOT / name, repo / name)
    for attr, path in {"ROOT": repo, "CATALOG": repo / "catalog", "PROMPTS": repo / "prompts",
                       "TASKS_DIR": repo / "catalog/tasks", "RECIPES_DIR": repo / "catalog/recipes",
                       "TOOLS_FILE": repo / "catalog/tools.yaml", "FAULTS_DIR": repo / "catalog/faults",
                       "SKILLS_DIR": repo / "catalog/skills"}.items():
        monkeypatch.setattr(settings, attr, path)
    app = create_app(AuthoringService(repo), results_url=f"sqlite:///{tmp_path / 'results.db'}", var_dir=tmp_path,
                     quota_state=tmp_path / "quota.json")
    with TestClient(app) as client:
        request = {**PAIR, "recipes": ["v2-terse"], "tasks": ["sql-01"]}
        assert client.post("/api/campaigns/plan", json=request).status_code == 400
        ledger = app.state.hf.factory().ledger
        assert client.post("/api/catalog/prompt", json={"ref": "system/terse", "text": "Be brief."}).status_code == 201
        assert client.post("/api/catalog/recipe", json={"id": "v2-terse", "fields": {
            "prompts": {"system": "system/terse"}}}).status_code == 201
        c = _wait(client, client.post("/api/campaigns", json=request).json()["id"])
        assert c["state"] == "done" and c["progress"]["statuses"] == {"SUCCESS": 1}
        assert app.state.hf.factory().ledger is ledger            # one ledger for the app's life
