"""Cards and options over the API (api/cards.py), and the guided UI pages (ui/guided.py) against a live API.
Needs the `api` and `ui` extras."""

from __future__ import annotations

import asyncio
import shutil
import socket
import threading
import time
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from harness_factory import settings
from harness_factory.api.app import create_app
from harness_factory.authoring.service import AuthoringService

SPEC = {"task": "fs-02", "recipe": "v2-base", "model": "scripted/replay", "provider": "scripted"}


def _repo(tmp: Path) -> Path:
    root = tmp / "repo"
    root.mkdir()
    for name in ("catalog", "prompts"):
        shutil.copytree(settings.ROOT / name, root / name)
    return root


@pytest.fixture
def api(tmp_path):
    repo = _repo(tmp_path)
    app = create_app(AuthoringService(repo), results_url=f"sqlite:///{tmp_path / 'results.db'}",
                     var_dir=tmp_path / "var", quota_state=tmp_path / "quota.json")
    return TestClient(app), app


def test_options_list_every_building_block(api):
    c, _ = api
    o = c.get("/api/options").json()
    for key in ("shapes", "recipes", "recipe_fields", "models", "tools", "skills", "prompts", "faults", "runtimes",
                "tasks", "check_kinds", "perturbations", "retrieval_modes"):
        assert o[key], key
    assert {"V1_SINGLE_CALL", "V5_ORCHESTRATOR"} <= {s["shape"] for s in o["shapes"]}
    recover = next(r for r in o["recipes"] if r["id"] == "v2-recover")
    assert recover["kind"] == "matrix" and "config.recover_malformed = 2" in recover["changes"]
    assert any(f["field"] == "config.max_steps" for f in o["recipe_fields"])


def test_building_block_cards(api):
    c, _ = api
    for kind, item in (("harness", "v2-base"), ("model", "scripted/replay"), ("task", "fs-02"), ("tool", "fs"),
                       ("fault", "transient")):
        r = c.get(f"/api/cards/{kind}/{item}")
        assert r.status_code == 200, (kind, r.text)
        assert r.json()["kind"] == kind and r.json()["markdown"].startswith("# ")
    assert c.get("/api/cards/harness/nope").status_code == 404
    assert c.get("/api/cards/nope/x").status_code == 404


def test_spec_card_then_final_card_after_a_run(api, tmp_path):
    c, app = api
    card = c.post("/api/cards/spec", json=SPEC).json()
    assert card["verdict"] == "READY"
    assert c.post("/api/cards/spec/final", json=SPEC).status_code == 404          # nothing has run yet
    from harness_factory.runner.runner import run_one
    f = app.state.hf.factory()
    asyncio.run(run_one(f, f.tasks.get("fs-02"), f.recipes["v2-base"], "scripted/replay", "scripted", 0))
    final = c.post("/api/cards/spec/final", json=SPEC).json()
    assert final["result"]["status"] == "SUCCESS" and final["id"] == card["id"]
    assert Path(final["saved"]).parent == (tmp_path / "var" / "cards" / "spec").resolve()
    saved = c.get("/api/cards/saved").json()
    assert saved[0]["status"] == "SUCCESS"
    assert c.get(f"/api/cards/saved/{saved[0]['name']}").json()["result"]["E0"] == 1.0
    assert c.get("/api/cards/saved/..%2F..%2Fsecret").status_code == 404


@pytest.fixture(scope="module")
def live_api(tmp_path_factory):
    pytest.importorskip("streamlit")
    import uvicorn
    tmp = tmp_path_factory.mktemp("guided")
    app = create_app(AuthoringService(_repo(tmp)), results_url=f"sqlite:///{tmp / 'results.db'}", var_dir=tmp,
                     quota_state=tmp / "quota.json")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 30
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=30)


def test_guided_pages_render_and_build_spec_cards(live_api, monkeypatch):
    import streamlit as st
    from streamlit.testing.v1 import AppTest
    monkeypatch.setenv("HF_API_URL", live_api)
    monkeypatch.delenv("HF_API_TOKEN", raising=False)
    st.cache_resource.clear()
    st.cache_data.clear()
    at = AppTest.from_file(str(settings.ROOT / "src/harness_factory/ui/app.py"), default_timeout=120)
    at.run()
    assert not at.exception and at.header[0].value == "Playbook"                  # the start page
    for page, header in (("What we can test with", "What we can test with"), ("Cards", "Cards"),
                         ("Build an experiment", "Build an experiment")):
        at.sidebar.radio(key="page").set_value(page).run()
        assert not at.exception, (page, at.exception)
        assert at.header[0].value == header
    at.button(key="b:preview").click().run()                                      # fs-02 x v2-base x replay
    assert not at.exception
    assert any("READY" in s.value for s in at.success)


def test_the_built_workspace_is_served_at_app(tmp_path):
    from fastapi import FastAPI

    from harness_factory.api.web import mount
    app = FastAPI()
    assert mount(app, tmp_path / "nothing") is False                       # not built: nothing mounted
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<div id=root></div>")
    (dist / "assets" / "a.js").write_text("1")
    assert mount(app, dist) is True
    c = TestClient(app)
    assert c.get("/app/").text == "<div id=root></div>" and c.get("/app/assets/a.js").status_code == 200
    assert c.get("/", follow_redirects=False).headers["location"] == "/app/"


def test_experiments_are_saved_and_compared(api, tmp_path):
    import time
    _, app = api
    with TestClient(app) as c:                 # the app's event loop keeps running between requests
        _experiment_checks(c, tmp_path, time)


def _experiment_checks(c, tmp_path, time):
    req = {"recipes": ["v2-base", "v2-recover"], "provider": "scripted", "models": ["scripted/replay"],
           "tasks": ["fs-01", "fs-02"], "seeds": 1, "concurrency": 2}
    r = c.post("/api/experiments", json={"name": "recover vs base", "hypothesis": "recovery costs nothing here",
                                         "request": req})
    assert r.status_code == 202, r.text
    eid = r.json()["id"]
    assert (tmp_path / "var" / "experiments" / f"{eid}.json").is_file()
    deadline = time.monotonic() + 240
    while time.monotonic() < deadline:
        d = c.get(f"/api/experiments/{eid}").json()
        if d["state"] != "running":
            break
        time.sleep(1)
    assert d["state"] == "done" and d["done"] == 4 and d["stats"]["mean_e0"] == 1.0
    assert {(p["recipe"], p["n"]) for p in d["per"]} == {("v2-base", 2), ("v2-recover", 2)}
    cmp_ = d["compare"][0]
    assert cmp_["recipe"] == "v2-recover" and cmp_["n"] == 2 and cmp_["verdict"] == "no clear difference"
    assert len(d["cells"]) == 4 and d["changed_since"] == []
    assert c.patch(f"/api/experiments/{eid}", json={"conclusion": "a tie"}).json()["conclusion"] == "a tie"
    assert c.get("/api/experiments").json()[0]["name"] == "recover vs base"
    again = c.post(f"/api/experiments/{eid}/rerun", json={})
    assert again.status_code == 409 and "nothing to run" in again.text           # every run already scored
    dash = c.get("/api/dashboard").json()
    assert dash["totals"]["scored"] == 4 and dash["experiments"][0]["id"] == eid
    assert {h["id"] for h in dash["harnesses"]} == {"v2-base", "v2-recover"}
    assert dash["best_per_class"] and len(dash["timeline"]) == 30
