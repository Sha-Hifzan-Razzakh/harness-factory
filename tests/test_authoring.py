"""Authoring (ADR 0009): every write is staged and checked by the factory's own validators; nothing that would
fail `hfx validate` is written; a change to an existing identity needs confirmation; files are all-or-nothing.

Each test works on its own copy of the catalog and prompts (tmp_path), never on the repository's.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from harness_factory import settings
from harness_factory.authoring import fsops, toolcode
from harness_factory.authoring.fsops import FileOp
from harness_factory.authoring.service import AuthoringError, AuthoringService
from harness_factory.recipes.model import load_recipes

TERSE = "Be brief.\n{% if tools %}Tools: {{ tools | join(', ') }}.{% endif %}\n"


@pytest.fixture
def repo(tmp_path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    for name in ("catalog", "prompts"):
        shutil.copytree(settings.ROOT / name, root / name)
    shutil.copy(settings.ROOT / "pyproject.toml", root / "pyproject.toml")
    return root


@pytest.fixture
def svc(repo) -> AuthoringService:
    return AuthoringService(repo)


def _hfx(repo: Path, *args: str) -> subprocess.CompletedProcess:
    # Everything the run writes stays in the copy, even where CI sets its own results store.
    env = {**os.environ, "HF_ROOT": str(repo), "HF_VAR": str(repo / "var"),
           "HF_RESULTS_URL": f"sqlite:///{repo / 'var/results.db'}", "HF_QUOTA_STATE": str(repo / "var/quota.json")}
    return subprocess.run([sys.executable, "-c", "from harness_factory.cli.main import app; app()", *args],
                          env=env, capture_output=True, text=True, timeout=300, check=False)


def test_snapshot_hashes_match_the_factory(svc):
    """The child-process snapshot computes the same identities as the factory itself."""
    listed = {it.id: it.hash for it in svc.list("recipe")}
    real = {rid: r.content_hash() for rid, r in load_recipes(settings.RECIPES_DIR).items()}
    assert listed == real
    assert svc.errors() == []


def test_matrix_recipe_from_a_new_prompt_runs(svc, repo):
    r = svc.create("recipe", {"id": "v2-terse", "fields": {"prompts": {"system": "system/terse"}}})
    assert r.status == "rejected" and "prompt_exists" in r.issues[0].detail
    assert not (repo / "catalog/recipes/matrix/v2_terse.yaml").exists()      # a rejected change writes nothing

    assert svc.create("prompt", {"ref": "system/terse", "text": TERSE}).status == "applied"
    r = svc.create("recipe", {"id": "v2-terse", "fields": {"prompts": {"system": "system/terse"}},
                              "note": "Matrix (prompt): a terse system prompt."})
    assert r.status == "applied" and r.impact.recipes_added == ["v2-terse"] and not r.impact.touches_existing
    text = (repo / "catalog/recipes/matrix/v2_terse.yaml").read_text()
    assert text.startswith("# Matrix (prompt)") and "base: v2-base" in text
    assert "canonical_model" not in text

    out = _hfx(repo, "run", "--task", "sql-01", "--recipe", "v2-terse", "--model", "scripted/replay",
               "--provider", "scripted")
    assert out.returncode == 0, out.stdout + out.stderr
    from harness_factory.results.store import ResultsStore
    rows = ResultsStore(f"sqlite:///{repo / 'var/results.db'}").rows()
    assert [(row["recipe_id"], row["status"]) for row in rows] == [("v2-terse", "SUCCESS")]


def test_changing_an_identity_needs_confirmation(svc, repo):
    """Editing a skill changes the hash of every recipe that uses it: shown, and written only with confirm."""
    before = (repo / "catalog/skills/sql-analysis/SKILL.md").read_text()
    payload = {"id": "sql-analysis", "name": "Answering from SQL", "description": "Read the schema first.",
               "body": "1. Describe the tables.\n2. Compute in SQL."}
    r = svc.update("skill", payload)
    assert r.status == "needs_confirm"
    assert r.impact.recipes_changed == ["v2-skills-on-demand", "v2-skills-upfront"]
    assert (repo / "catalog/skills/sql-analysis/SKILL.md").read_text() == before

    assert svc.update("skill", payload, dry_run=True, confirm=True).status == "dry_run"
    assert (repo / "catalog/skills/sql-analysis/SKILL.md").read_text() == before
    assert svc.update("skill", payload, confirm=True).status == "applied"
    assert "Compute in SQL." in (repo / "catalog/skills/sql-analysis/SKILL.md").read_text()


def test_prompt_rules(svc):
    r = svc.create("prompt", {"ref": "system/broken", "text": "{% if tools %}no end"})
    assert r.status == "rejected" and "line 1" in r.issues[0].detail
    r = svc.create("prompt", {"ref": "system/wrong-vars", "text": "Do {{ task.instruction }}"})
    assert r.status == "rejected" and "rendered with recipe, tools only" in r.issues[0].detail
    assert svc.create("prompt", {"ref": "system/default", "text": "x"}).status == "rejected"      # exists
    with pytest.raises(AuthoringError):
        svc.create("prompt", {"ref": "no-layer", "text": "x"})


def test_recipe_rules(svc):
    r = svc.create("recipe", {"id": "v2-pinned", "fields": {"model_slots": {"executor": {"canonical_model": "x"}}}})
    assert r.status == "rejected" and "campaign axis" in r.issues[0].detail
    assert svc.create("recipe", {"id": "v2-same", "fields": {}}).status == "rejected"
    assert svc.create("recipe", {"id": "v2-orphan", "base": "nope", "fields": {"config": {"max_steps": 5}}}).status \
        == "rejected"
    r = svc.create("recipe", {"id": "v2-two", "fields": {"config": {"max_steps": 10}, "tier": "T3"}}, dry_run=True)
    assert r.status == "dry_run" and "2 dimensions" in r.notes[0]


def test_delete_only_what_nothing_uses(svc, repo):
    r = svc.delete("skill", "sql-analysis")
    assert r.status == "rejected" and "v2-skills-upfront" in r.issues[0].detail
    assert svc.delete("recipe", "v2-base").status == "rejected"         # a base, a delegate, the selector's pick
    assert svc.delete("server", "fs").status == "rejected"              # built-in

    assert svc.delete("fault", "transient").status == "applied"
    assert not (repo / "catalog/faults/transient.yaml").exists()
    r = svc.delete("recipe", "v2-recover", confirm=True)
    assert r.status == "applied" and r.impact.recipes_removed == ["v2-recover"]


def test_local_tool_server_stub(svc, repo):
    payload = {"id": "calc", "instructions": "Arithmetic.",
               "tools": [{"name": "add", "description": "Add two numbers.", "side_effect": "READ", "risk": "R0",
                          "params": [{"name": "a", "type": "float"}, {"name": "b", "type": "float"}]}]}
    r = svc.create("server", payload)
    assert r.status == "applied", r.issues
    assert any("stub" in n for n in r.notes) and any("no task allows calc" in n for n in r.notes)
    module = repo / toolcode.module_path("calc")
    entry = next(s for s in yaml.safe_load((repo / "catalog/tools.yaml").read_text())["servers"] if s["id"] == "calc")
    assert entry["module"] == "harness_factory.env.mcp.impl.calc_server"
    from harness_factory.hashing import content_hash
    assert entry["tools"][0]["schema_hash"] == content_hash(toolcode.discover(module, "calc")["add"])
    assert svc.errors() == []

    # A new server never changes another recipe's environment; changing this one shows who uses it.
    r = svc.create("recipe", {"id": "v2-calc", "fields": {"tools": {"tool_ids": ["fs.*", "sql.*", "calc.*"],
                                                                    "mcp_servers": ["fs", "sql", "calc"]}}})
    assert r.status == "applied", r.issues
    payload["tools"][0]["params"].append({"name": "c", "type": "float", "required": False, "default": 0.0})
    r = svc.update("server", payload)
    assert r.status == "needs_confirm" and r.impact.env_changed == {"calc": ["v2-calc"]}


def test_local_tool_code_rules(svc, repo):
    base = {"id": "net", "tools": [{"name": "fetch", "side_effect": "READ", "risk": "R0"}]}
    bad = "import requests\nfrom mcp.server.mcpserver import MCPServer\napp = MCPServer('net')\n"
    r = svc.create("server", {**base, "code": bad})
    details = " ".join(i.detail for i in r.issues)
    assert r.status == "rejected" and "imports requests" in details and "app.run()" in details

    other = toolcode.stub("net", "", [toolcode.ToolDef(name="get", side_effect="READ", risk="R0")])
    r = svc.create("server", {**base, "code": other})
    details = " ".join(i.detail for i in r.issues)
    assert r.status == "rejected" and "not served: fetch" in details and "not declared" in details
    assert not (repo / toolcode.module_path("net")).exists()


def test_remote_tool_server(svc):
    tools = [{"name": "refund", "side_effect": "REVERSIBLE", "risk": "R1"}]
    remote = {"id": "orders", "transport": "streamable_http", "url": "https://orders.example/mcp", "version": "5.1",
              "auth_env": "ORDERS_TOKEN", "tools": tools}
    r = svc.create("server", remote)
    assert r.status == "rejected" and "test tenant" in r.issues[0].detail
    r = svc.create("server", {**remote, "tenant": {"header": "HF-Tenant"}})
    assert r.status == "applied" and any("tools freeze" in n for n in r.notes)
    with pytest.raises(AuthoringError):                     # a token is never stored, only the variable's name
        svc.create("server", {**remote, "id": "kb", "auth_env": "sk-live-123"})


def test_apply_is_all_or_nothing(tmp_path):
    root = tmp_path / "r"
    (root / "catalog").mkdir(parents=True)
    (root / "catalog/a.txt").write_text("old")
    with pytest.raises(ValueError):
        fsops.apply(root, [FileOp(path="catalog/a.txt", text="new"), FileOp(path="catalog/b.txt", text="b"),
                           FileOp(path="../escape.txt", text="x")])
    assert (root / "catalog/a.txt").read_text() == "old"
    assert not (root / "catalog/b.txt").exists() and not (tmp_path / "escape.txt").exists()


def test_lock_is_exclusive(tmp_path):
    lock = tmp_path / "authoring.lock"
    with fsops.catalog_lock(lock), pytest.raises(fsops.LockTimeout), fsops.catalog_lock(lock, timeout_s=0.3):
        pass
    with fsops.catalog_lock(lock, timeout_s=0.3):           # released
        pass


def test_cli_exit_codes(repo):
    out = _hfx(repo, "new", "recipe", "v2-short", "--set", "config.max_steps=8", "--dry-run")
    assert out.returncode == 0 and "dry run" in out.stdout and not (repo / "catalog/recipes/matrix/v2_short.yaml").exists()
    out = _hfx(repo, "new", "recipe", "v2-base", "--base", "none", "--set", "config.max_steps=8", "--update")
    assert out.returncode == 1                               # a base recipe needs its whole definition
    out = _hfx(repo, "catalog", "check")
    assert out.returncode == 0 and "catalog ok" in out.stdout
