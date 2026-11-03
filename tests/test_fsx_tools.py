"""The fsx server (tools dimension): finer file edits than fs.write_file. What each tool does, what it refuses, who
gets it (granted_by, write tools only where the task already allows writes), that the permission policy applies to
it by side effect, and that adding it moved no existing identity."""

import os

import pytest

from harness_factory.env.mcp.servers import SideEffect, ToolCatalogFile
from harness_factory.env.sandbox import LocalSandbox
from harness_factory.mcp.pool import MCPSessionPool
from harness_factory.provision.provisioner import allowed_tools, task_class
from harness_factory.runner.runner import run_one
from harness_factory.substrate.gateway import ScriptedBackend

FSX = {"fsx.edit", "fsx.append", "fsx.move", "fsx.mkdir", "fsx.tree"}
APP_INI = "[server]\nhost = 0.0.0.0\nport = 8080\n\n[logging]\nlevel = info\n"


@pytest.fixture
def sandbox(factory):
    with LocalSandbox("fsx-test") as sb:
        sb.reset(factory.tasks.get("fs-01"))                     # base seed: config/app.ini, notes/README.txt
        yield sb


def fsx_pool(factory, sandbox) -> MCPSessionPool:
    return MCPSessionPool(factory.declared, ["fsx"], sandbox.env())    # opened in the test's own task


async def refused(pool, tool, args, *needles):
    res = await pool.call(tool, args)
    assert res.is_error, (tool, args, res.text)
    for n in needles:
        assert n in res.text, (tool, args, res.text)               # ToolError: the model is told why
    return res.text


# ------------------------------------------------------------------ server: confinement

async def test_every_fsx_tool_stays_inside_the_workspace(factory, sandbox, tmp_path):
    async with fsx_pool(factory, sandbox) as pool:
        for tool, args in (("fsx.edit", {"path": "../state/task.sqlite", "old": "a", "new": "b"}),
                           ("fsx.append", {"path": "../state/x.txt", "text": "x"}),
                           ("fsx.move", {"src": "notes/README.txt", "dst": "../outside.txt"}),
                           ("fsx.move", {"src": "../state/task.sqlite", "dst": "db.sqlite"}),
                           ("fsx.mkdir", {"path": "../escaped"}),
                           ("fsx.tree", {"path": ".."}),
                           ("fsx.edit", {"path": "/etc/hostname", "old": "a", "new": "b"})):
            await refused(pool, tool, args, "escapes")
        assert (sandbox.workspace / "notes/README.txt").is_file()             # the refused move left it in place
        assert not (sandbox.workspace.parent / "escaped").exists()
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("secret")
        try:
            os.symlink(outside, sandbox.workspace / "link")
        except (OSError, NotImplementedError):
            pytest.skip("no symlinks here")
        await refused(pool, "fsx.append", {"path": "link/secret.txt", "text": "x"}, "escapes")
        await refused(pool, "fsx.move", {"src": "notes/README.txt", "dst": "link/README.txt"}, "escapes")
        listing = (await pool.call("fsx.tree", {"depth": 3})).text
        assert "link/" in listing and "secret.txt" not in listing             # listed, never followed
        assert (outside / "secret.txt").read_text() == "secret"


# ------------------------------------------------------------------ server: edit

async def test_edit_replaces_exact_text(factory, sandbox):
    async with fsx_pool(factory, sandbox) as pool:
        ok = await pool.call("fsx.edit", {"path": "config/app.ini", "old": "port = 8080", "new": "port = 9090"})
        assert not ok.is_error and "replaced 1 occurrence in config/app.ini" in ok.text
        assert (sandbox.workspace / "config/app.ini").read_text() == APP_INI.replace("8080", "9090")
        (sandbox.workspace / "rep.txt").write_text("a-a-a")
        three = await pool.call("fsx.edit", {"path": "rep.txt", "old": "a", "new": "b", "count": 3})
        assert not three.is_error and (sandbox.workspace / "rep.txt").read_text() == "b-b-b"


async def test_edit_refuses_and_changes_nothing(factory, sandbox):
    async with fsx_pool(factory, sandbox) as pool:
        ws = sandbox.workspace
        (ws / "rep.txt").write_text("a-a-a")
        (ws / "bin.dat").write_bytes(b"\xff\xfe\x00\x81")
        await refused(pool, "fsx.edit", {"path": "config/app.ini", "old": "port = 7070", "new": "x"}, "not found")
        await refused(pool, "fsx.edit", {"path": "rep.txt", "old": "a", "new": "b"}, "occurs 3 times", "count=3")
        await refused(pool, "fsx.edit", {"path": "rep.txt", "old": "a", "new": "b", "count": 2}, "occurs 3 times", "not 2")
        await refused(pool, "fsx.edit", {"path": "config/app.ini", "old": "port", "new": "p", "count": 2}, "occurs once")
        await refused(pool, "fsx.edit", {"path": "rep.txt", "old": "a", "new": "b", "count": 0}, "count must be 1")
        await refused(pool, "fsx.edit", {"path": "rep.txt", "old": "", "new": "b"}, "must not be empty")
        await refused(pool, "fsx.edit", {"path": "rep.txt", "old": "a", "new": "a", "count": 3}, "the same")
        await refused(pool, "fsx.edit", {"path": "nope.txt", "old": "a", "new": "b"}, "no such file")
        await refused(pool, "fsx.edit", {"path": "config", "old": "a", "new": "b"}, "is a folder")
        await refused(pool, "fsx.edit", {"path": "bin.dat", "old": "a", "new": "b"}, "not a UTF-8 text file")
        assert (ws / "rep.txt").read_text() == "a-a-a" and (ws / "config/app.ini").read_text() == APP_INI


# ------------------------------------------------------------------ server: append, move, mkdir

async def test_append_extends_an_existing_file_only(factory, sandbox):
    async with fsx_pool(factory, sandbox) as pool:
        ws = sandbox.workspace
        ok = await pool.call("fsx.append", {"path": "config/app.ini", "text": "format = json\n"})
        assert not ok.is_error and "appended 14 characters" in ok.text
        assert (ws / "config/app.ini").read_text() == APP_INI + "format = json\n"        # exactly as given
        await refused(pool, "fsx.append", {"path": "notes/new.txt", "text": "x"}, "no such file", "fs.write_file")
        assert not (ws / "notes/new.txt").exists()                                       # never creates
        await refused(pool, "fsx.append", {"path": "notes", "text": "x"}, "is a folder")
        await refused(pool, "fsx.append", {"path": "config/app.ini", "text": ""}, "nothing to append")


async def test_move_renames_inside_the_workspace_and_never_overwrites(factory, sandbox):
    async with fsx_pool(factory, sandbox) as pool:
        ws = sandbox.workspace
        readme = (ws / "notes/README.txt").read_text()
        ok = await pool.call("fsx.move", {"src": "notes/README.txt", "dst": "archive/2026/readme.txt"})
        assert not ok.is_error and "moved notes/README.txt to archive/2026/readme.txt" in ok.text
        assert not (ws / "notes/README.txt").exists() and (ws / "archive/2026/readme.txt").read_text() == readme
        await refused(pool, "fsx.move", {"src": "archive/2026/readme.txt", "dst": "config/app.ini"},
                      "already exists (a file)", "never overwrites")
        assert (ws / "config/app.ini").read_text() == APP_INI and (ws / "archive/2026/readme.txt").is_file()
        await refused(pool, "fsx.move", {"src": "archive/2026/readme.txt", "dst": "config"}, "already exists (a folder)")
        await refused(pool, "fsx.move", {"src": "config/app.ini", "dst": "config/../config/app.ini"}, "same file")
        await refused(pool, "fsx.move", {"src": "nope.txt", "dst": "x.txt"}, "no such file")
        await refused(pool, "fsx.move", {"src": "config", "dst": "settings"}, "moves files only")
        assert (ws / "config").is_dir() and not (ws / "settings").exists()


async def test_mkdir_creates_parents_and_refuses_files(factory, sandbox):
    async with fsx_pool(factory, sandbox) as pool:
        ws = sandbox.workspace
        ok = await pool.call("fsx.mkdir", {"path": "reports/2026/q4"})
        assert not ok.is_error and "created folder reports/2026/q4" in ok.text and (ws / "reports/2026/q4").is_dir()
        again = await pool.call("fsx.mkdir", {"path": "reports/2026"})
        assert not again.is_error and "already exists" in again.text
        await refused(pool, "fsx.mkdir", {"path": "config/app.ini"}, "is a file")
        await refused(pool, "fsx.mkdir", {"path": "config/app.ini/sub"}, "cannot create")
        assert (ws / "config/app.ini").read_text() == APP_INI


# ------------------------------------------------------------------ server: tree

async def test_tree_lists_to_depth_and_caps_entries(factory, sandbox):
    async with fsx_pool(factory, sandbox) as pool:
        ws = sandbox.workspace
        one = (await pool.call("fsx.tree", {"depth": 1})).text
        assert one.splitlines() == ["./", "  config/", "  notes/"]
        two = (await pool.call("fsx.tree", {})).text
        assert two.splitlines() == ["./", "  config/", "    app.ini", "  notes/", "    README.txt"]
        (ws / "empty").mkdir()
        assert (await pool.call("fsx.tree", {"path": "empty"})).text.splitlines() == ["empty/", "  (empty)"]
        (ws / "many").mkdir()
        for i in range(250):
            (ws / "many" / f"f{i:03}.txt").write_text("x")
        capped = (await pool.call("fsx.tree", {"path": "many"})).text.splitlines()
        assert len(capped) == 1 + 200 + 1 and capped[-1].startswith("... 50 more entries")
        await refused(pool, "fsx.tree", {"depth": 0}, "between 1 and 6")
        await refused(pool, "fsx.tree", {"depth": 7}, "between 1 and 6")
        await refused(pool, "fsx.tree", {"path": "config/app.ini"}, "not a folder")
        await refused(pool, "fsx.tree", {"path": "nope"}, "no such folder")


# ------------------------------------------------------------------ who gets fsx

def test_fsx_write_tools_only_where_the_task_already_allows_writes(factory):
    d, fsx = factory.declared, factory.recipes["v2-fsx"]
    get = factory.tasks.get
    assert allowed_tools(fsx, get("fs-02"), d) & FSX == FSX - {"fsx.move"}       # list + read + write
    assert allowed_tools(fsx, get("mix-01"), d) & FSX == {"fsx.append", "fsx.mkdir", "fsx.tree"}   # no read_file
    assert allowed_tools(fsx, get("ops-03"), d) & FSX == {"fsx.tree"}             # read-only task: no writes
    assert allowed_tools(fsx, get("mix-04"), d) & FSX == {"fsx.tree"}             # writes SQL, not files
    assert not allowed_tools(fsx, get("sql-01"), d) & FSX
    # move makes its source disappear, which write_file can't: only a task that allows deleting files gets it.
    deleter = get("fs-02").model_copy(update={"allowed_tools": ["fs.*"]})
    assert "fsx.move" in allowed_tools(fsx, deleter, d)
    direct = get("fs-02").model_copy(update={"allowed_tools": ["fs.read_file", "fsx.move"]})
    assert allowed_tools(fsx, direct, d) & FSX == {"fsx.move"}                   # a task may also name it
    for rid in ("v2-base", "v2-search"):
        assert not allowed_tools(factory.recipes[rid], get("fs-02"), d) & FSX    # recipes that don't open fsx
    for t in factory.tasks.all(include_held_out=True):                           # classes don't move
        plain = ToolCatalogFile(servers=[s for s in d.servers if s.id != "fsx"])
        assert task_class(t, d) == task_class(t, plain), t.id


def test_a_write_is_granted_only_by_a_grantor_as_strong(factory):
    d = factory.declared
    assert all(d.grantable(r) for r in FSX)
    stronger = d.model_copy(deep=True)
    stronger.spec("fsx.edit").side_effect = SideEffect.IRREVERSIBLE                 # stronger than write_file
    stronger.spec("fsx.mkdir").risk = "R3"                                         # riskier than write_file
    fs02 = factory.tasks.get("fs-02")
    reach = allowed_tools(factory.recipes["v2-fsx"], fs02, stronger)
    assert "fsx.edit" not in reach and "fsx.mkdir" not in reach and "fsx.append" in reach
    data = d.model_dump(mode="json")
    fsx = next(s for s in data["servers"] if s["id"] == "fsx")
    next(t for t in fsx["tools"] if t["name"] == "edit")["granted_by"] = ["fs.read_file", "fs.list_dir"]
    with pytest.raises(ValueError, match="fsx.edit: granted_by must include a tool at least as strong"):
        ToolCatalogFile.model_validate(data)
    next(t for t in fsx["tools"] if t["name"] == "edit")["granted_by"] = ["fs.write_file", "fs.nope"]
    with pytest.raises(ValueError, match="undeclared tools: fs.nope"):
        ToolCatalogFile.model_validate(data)


def test_adding_fsx_moved_no_existing_identity(factory):
    d, base, fsx = factory.declared, factory.recipes["v2-base"], factory.recipes["v2-fsx"]
    assert d.env_hash(base.tools.mcp_servers, "openai/gpt-oss-120b") == "a55c2fb739f1"   # as in test_search_tools
    assert base.content_hash() == "79abd03818943f65"
    assert d.env_hash(fsx.tools.mcp_servers, "openai/gpt-oss-120b") != "a55c2fb739f1"
    db, dx = base.model_dump(), fsx.model_dump()
    assert sorted(k for k in db if db[k] != dx[k]) == ["base", "id", "tools"]        # one dimension
    assert dx["policies"] == db["policies"]


# ------------------------------------------------------------------ runs

def _call(tool: str, **args) -> dict:
    return {"tool_calls": [{"name": tool.replace(".", "__"), "arguments": args}]}


EDIT_PORT = [_call("fs.read_file", path="config/app.ini"),
             _call("fsx.edit", path="config/app.ini", old="port = 8080", new="port = 9090"),
             {"content": "Changed the port to 9090."}]


async def test_a_model_can_edit_via_fsx_in_v2_fsx_but_not_in_v2_base(factory):
    task = factory.tasks.get("fs-02")
    rec = await run_one(factory, task, factory.recipes["v2-fsx"], "scripted/replay", "scripted",
                        backend=ScriptedBackend(list(EDIT_PORT)))
    assert rec.status.value == "SUCCESS" and rec.scores.outcome == 1.0 and rec.scores.trajectory == 1.0
    base = await run_one(factory, task, factory.recipes["v2-base"], "scripted/replay", "scripted",
                         backend=ScriptedBackend(list(EDIT_PORT)))
    assert any(v.kind == "tool_not_allowed" and "fsx.edit" in v.detail for v in base.violations)
    assert base.status.value != "SUCCESS" and base.scores.outcome == 0


async def test_replay_solves_with_fsx_offered(factory):
    """The CI matrix gate runs v2-fsx with scripted/replay: the reference plans (fs tools) still solve."""
    rec = await run_one(factory, factory.tasks.get("fs-02"), factory.recipes["v2-fsx"], "scripted/replay", "scripted")
    assert rec.status.value == "SUCCESS" and rec.composite == 1.0


async def test_permission_policy_applies_to_fsx_by_side_effect(factory):
    fsx = factory.recipes["v2-fsx"]
    read_only = fsx.model_copy(update={"policies": fsx.policies.model_copy(
        update={"permission_mode": {"READ": "AUTO", "REVERSIBLE": "DENY", "IRREVERSIBLE": "DENY"}})})
    script = [_call("fsx.tree"), *EDIT_PORT[1:]]
    rec = await run_one(factory, factory.tasks.get("fs-02"), read_only, "scripted/replay", "scripted",
                        backend=ScriptedBackend(script))
    denied = [v.detail for v in rec.violations if v.kind == "policy_denied"]
    assert denied == ["fsx.edit (REVERSIBLE)"]                      # tree (READ) ran, edit (REVERSIBLE) was refused
    tools = [m["content"] for m in rec.messages if m["role"] == "tool"]
    assert "config/" in tools[0] and tools[1].startswith("Permission denied")
    assert rec.status.value == "FAIL" and rec.scores.outcome == 0

    # v2-fsx denies IRREVERSIBLE: fs.delete_file is refused, fsx.move (REVERSIBLE) is not.
    task = factory.tasks.get("fs-01").model_copy(update={"allowed_tools": ["fs.*"]})
    script = [_call("fs.delete_file", path="notes/README.txt"),
              _call("fsx.move", src="notes/README.txt", dst="notes/old/README.txt"), {"content": "done"}]
    rec = await run_one(factory, task, fsx, "scripted/replay", "scripted", backend=ScriptedBackend(script))
    assert [v.detail for v in rec.violations if v.kind == "policy_denied"] == ["fs.delete_file (IRREVERSIBLE)"]
    moved = [m["content"] for m in rec.messages if m["role"] == "tool"][1]
    assert moved == "moved notes/README.txt to notes/old/README.txt"
