"""The UI (#32) against a live API on a copy of the catalog: every page renders, and a matrix recipe made on the
New experiment page goes through the API's checks into a file. Needs the `api` and `ui` extras."""

from __future__ import annotations

import shutil
import socket
import threading
import time
from pathlib import Path

import pytest

pytest.importorskip("streamlit")
pytest.importorskip("fastapi")
import streamlit as st
import uvicorn
from streamlit.testing.v1 import AppTest

from harness_factory import settings
from harness_factory.api.app import create_app
from harness_factory.authoring.service import AuthoringService

APP = str(Path(__file__).resolve().parents[1] / "src/harness_factory/ui/app.py")


@pytest.fixture(scope="module")
def live_api(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("ui")
    repo = tmp / "repo"
    repo.mkdir()
    for name in ("catalog", "prompts"):
        shutil.copytree(settings.ROOT / name, repo / name)
    app = create_app(AuthoringService(repo), results_url=f"sqlite:///{tmp / 'results.db'}", var_dir=tmp,
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
    yield f"http://127.0.0.1:{port}", repo
    server.should_exit = True
    thread.join(timeout=30)


def _app(monkeypatch, url: str) -> AppTest:
    monkeypatch.setenv("HF_API_URL", url)
    monkeypatch.delenv("HF_API_TOKEN", raising=False)
    st.cache_resource.clear()
    st.cache_data.clear()
    at = AppTest.from_file(APP, default_timeout=120)
    at.run()
    return at


def test_every_page_renders(live_api, monkeypatch):
    at = _app(monkeypatch, live_api[0])
    assert not at.exception and not at.error
    for page in ("Catalog", "New experiment", "Add", "Run", "Results"):
        at.sidebar.radio(key="page").set_value(page).run()
        assert not at.exception, (page, at.exception)
        assert at.header[0].value in ("Catalog", "New experiment", "Add to the catalog", "Run a campaign", "Results")


def test_new_experiment_writes_a_matrix_recipe(live_api, monkeypatch):
    url, repo = live_api
    at = _app(monkeypatch, url)
    at.sidebar.radio(key="page").set_value("New experiment").run()
    at.text_input(key="exp:id").set_value("v2-steps-12").run()
    at.radio(key="exp:dim").set_value("Config").run()
    at.number_input(key="exp:max_steps").set_value(12).run()
    at.button(key="check:exp").click().run()
    assert not at.exception and "Checks pass" in at.info[0].value
    assert not (repo / "catalog/recipes/matrix/v2_steps_12.yaml").exists()
    at.button(key="save:exp").click().run()
    assert "Written" in at.success[0].value
    text = (repo / "catalog/recipes/matrix/v2_steps_12.yaml").read_text()
    assert "base: v2-base" in text and "max_steps: 12" in text


def test_unreachable_api_says_how_to_start_it(monkeypatch):
    at = _app(monkeypatch, "http://127.0.0.1:9")
    assert "Can't reach the API" in at.error[0].value
