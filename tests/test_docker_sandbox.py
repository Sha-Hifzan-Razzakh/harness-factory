"""DockerSandbox: same runs, same grades, inside a locked-down container.

Skipped when no Docker daemon is reachable, except when HF_REQUIRE_DOCKER=1 (CI), where it fails instead,
so the isolation tests can never silently stop running.
"""

import json
import os
import subprocess

import pytest

from harness_factory import settings
from harness_factory.env.sandbox import DockerSandbox, make_sandbox
from harness_factory.env.sandbox_image import docker_available, image_tag
from harness_factory.mcp.pool import MCPSessionPool
from harness_factory.runner.runner import run_one
from harness_factory.substrate.errors import HarnessError


@pytest.fixture
def docker_required():
    if not docker_available():
        if os.environ.get("HF_REQUIRE_DOCKER") == "1":
            pytest.fail("HF_REQUIRE_DOCKER=1 but no Docker daemon is reachable")
        pytest.skip("no Docker daemon")


@pytest.fixture
def sandbox(factory, docker_required):
    sb = DockerSandbox("dk-test")
    sb.reset(factory.tasks.get("fs-01"))
    yield sb
    sb.teardown()


def _exec(sb, *code_lines):
    return subprocess.run([settings.DOCKER_BIN, "exec", sb.container, "python", "-c", "\n".join(code_lines)],
                          check=False, capture_output=True, text=True, timeout=60)


def _existing(names) -> set[str]:
    """Which of these containers still exist. Only this test's own containers are checked: tests run in
    parallel, so other hf containers may be alive at any moment."""
    out = subprocess.run([settings.DOCKER_BIN, "ps", "-a", "--format", "{{.Names}}", "--filter", "label=hf.sandbox=1"],
                         check=True, capture_output=True, text=True, timeout=30)
    return set(out.stdout.split()) & set(names)


# ------------------------------------------------------------- no Docker needed

def test_isolation_contract_in_run_args(tmp_path, monkeypatch):
    sb = DockerSandbox.__new__(DockerSandbox)            # no daemon needed to inspect the contract
    sb.label, sb.image, sb.container = "x", "img", "hf-x"
    sb.workspace, sb.db_path = tmp_path / "workspace", tmp_path / "state" / "task.sqlite"
    args = sb.run_args()
    joined = " ".join(args)
    for flag in ("--network none", "--read-only", "--cap-drop ALL", "--security-opt no-new-privileges",
                 "--pids-limit", "--memory", "--cpus"):
        assert flag in joined, flag
    assert f"{sb.workspace}:/work" in args and f"{sb.db_path.parent}:/state" in args
    assert f"{sb.db_path.parent / 'web'}:/state/web:ro" in args       # the web corpus is read-only (ADR 0018)
    if hasattr(os, "getuid"):
        assert f"{os.getuid()}:{os.getgid()}" in args


def test_image_tag_is_content_addressed():
    a = image_tag("python:3.12-slim", "3.12", "x86_64")
    assert a == image_tag("python:3.12-slim", "3.12", "x86_64")
    assert a != image_tag("python:3.12-slim", "3.13", "x86_64")
    assert a != image_tag("python:3.12-slim", "3.12", "aarch64")


def test_docker_unavailable_leaves_no_directories(monkeypatch):
    """If the image can't be resolved (Docker down, build failed), the sandbox must not have created anything."""
    import harness_factory.env.sandbox_image as si
    monkeypatch.setattr(settings, "DOCKER_BIN", "/nonexistent/docker")
    monkeypatch.setattr(si, "_ensured", {})
    settings.ensure_dirs()
    before = set(settings.SANDBOXES_DIR.iterdir())
    with pytest.raises(HarnessError):
        make_sandbox("x", kind="docker")
    assert set(settings.SANDBOXES_DIR.iterdir()) == before


def test_unknown_sandbox_kind_is_rejected():
    with pytest.raises(ValueError):
        make_sandbox("x", kind="vm")


# ------------------------------------------------------------- Docker

async def test_runs_and_grades_identically_in_docker(factory, docker_required, monkeypatch):
    monkeypatch.setattr(settings, "SANDBOX", "docker")
    started = []
    real_run_args = DockerSandbox.run_args

    def recording_run_args(self):
        started.append(self.container)
        return real_run_args(self)

    monkeypatch.setattr(DockerSandbox, "run_args", recording_run_args)
    for task_id in ("fs-02", "sql-02"):
        rec = await run_one(factory, factory.tasks.get(task_id), factory.recipes["v2-base"], "scripted/replay",
                            "scripted")
        assert rec.status.value == "SUCCESS", rec.grades
        snap = next(g for g in rec.grades if g["grader_id"] == "state_diff")["snapshot"]
        assert snap["sandbox"] == "docker" and snap["sandbox_image"].startswith("hf-sandbox:")
    assert len(started) == 2 and not _existing(started)    # every container removed after its run


def test_no_network(sandbox):
    r = _exec(sandbox, "import socket",
              "try:",
              "    socket.create_connection(('1.1.1.1', 53), timeout=3); print('CONNECTED')",
              "except OSError as e:",
              "    print('blocked', type(e).__name__)")
    assert "blocked" in r.stdout and "CONNECTED" not in r.stdout
    interfaces = _exec(sandbox, "import socket; print(sorted(n for _, n in socket.if_nameindex()))")
    assert interfaces.stdout.strip() == "['lo']"


def test_root_is_read_only_and_only_mounts_are_writable(sandbox):
    r = _exec(sandbox, "import os",
              "for p in ('/etc/hf-probe', '/opt/hf/probe', '/work/probe', '/state/probe', '/tmp/probe',",
              "          '/state/web/probe'):",
              "    try:",
              "        open(p, 'w').write('x'); print(p, 'writable')",
              "    except OSError:",
              "        print(p, 'denied')")
    status = dict(line.split() for line in r.stdout.strip().splitlines())
    assert status == {"/etc/hf-probe": "denied", "/opt/hf/probe": "denied", "/work/probe": "writable",
                      "/state/probe": "writable", "/tmp/probe": "writable", "/state/web/probe": "denied"}


def test_runs_unprivileged_and_files_stay_owned_by_the_host_user(sandbox):
    r = _exec(sandbox, "import os; open('/work/owned.txt', 'w').write('x'); print(os.getuid(), os.geteuid())")
    if hasattr(os, "getuid"):
        assert r.stdout.split() == [str(os.getuid())] * 2
        assert (sandbox.workspace / "owned.txt").stat().st_uid == os.getuid()
    caps = _exec(sandbox, "print([l for l in open('/proc/self/status') if l.startswith('CapEff')][0].split()[1])")
    assert int(caps.stdout.strip(), 16) == 0                # no effective capabilities


async def test_mcp_servers_run_inside_the_container(factory, sandbox):
    async with MCPSessionPool(factory.declared, ["fs", "sql"], sandbox.env(), sandbox.launch) as pool:
        listing = await pool.call("fs.list_dir", {"path": "."})
        assert not listing.is_error and "config/" in listing.text
        escape = await pool.call("fs.read_file", {"path": "../state/task.sqlite"})
        assert escape.is_error and "escapes" in escape.text
        rows = await pool.call("sql.query", {"sql": "SELECT COUNT(*) AS n FROM customers"})
        assert json.loads(rows.text)["rows"] == [[5]]
        hostname = await pool.call("fs.read_file", {"path": "/etc/hostname"})
        assert hostname.is_error                                     # absolute paths are outside /work too


async def test_fsx_server_runs_inside_the_container(factory, sandbox):
    async with MCPSessionPool(factory.declared, ["fsx"], sandbox.env(), sandbox.launch) as pool:
        edit = await pool.call("fsx.edit", {"path": "config/app.ini", "old": "port = 8080", "new": "port = 9090"})
        assert not edit.is_error, edit.text
        moved = await pool.call("fsx.move", {"src": "notes/README.txt", "dst": "notes/old/README.txt"})
        assert not moved.is_error, moved.text
        assert "port = 9090" in (sandbox.workspace / "config/app.ini").read_text()
        assert (sandbox.workspace / "notes/old/README.txt").is_file()        # on the host, through the mount
        tree = await pool.call("fsx.tree", {"depth": 3})
        assert "    README.txt" in tree.text
        escape = await pool.call("fsx.append", {"path": "../state/task.sqlite", "text": "x"})
        assert escape.is_error and "escapes" in escape.text


def test_failed_start_leaves_no_container(factory, docker_required):
    sb = DockerSandbox("dk-fail")
    sb.run_args = lambda: ["run", "-d", "--name", sb.container, "--label", "hf.sandbox=1", sb.image, "/nonexistent"]
    with pytest.raises(HarnessError):
        sb.reset(factory.tasks.get("fs-01"))
    sb.teardown()
    assert not _existing([sb.container])


async def test_skills_on_demand_inside_the_container(factory, docker_required, monkeypatch):
    """Skills are installed in the state folder, mounted at /state: the server inside the container reads them."""
    from harness_factory.substrate.gateway import ScriptedBackend
    monkeypatch.setattr(settings, "SANDBOX", "docker")
    script = [{"tool_calls": [{"name": "skills__load", "arguments": {"name": "sql-analysis"}}]}, {"content": "3"}]
    rec = await run_one(factory, factory.tasks.get("sql-01"), factory.recipes["v2-skills-on-demand"],
                        "scripted/replay", "scripted", backend=ScriptedBackend(script))
    tool = next(m for m in rec.messages if m["role"] == "tool")
    assert "List the tables and describe" in tool["content"]


async def test_code_and_git_tasks_inside_the_container(factory, docker_required, monkeypatch):
    """ADR 0012: the code and git servers, the git seed and the tests_pass / git_log_contains probes all work in
    the container, and the agent's code runs there (the container's Python, no network), not on the host."""
    monkeypatch.setattr(settings, "SANDBOX", "docker")
    for task_id in ("code-02", "code-03"):
        rec = await run_one(factory, factory.tasks.get(task_id), factory.recipes["v2-code"], "scripted/replay",
                            "scripted")
        assert rec.status.value == "SUCCESS", rec.grades
        assert next(g for g in rec.grades if g["grader_id"] == "state_diff")["snapshot"]["sandbox"] == "docker"
    sb = DockerSandbox("dk-code")
    try:
        sb.reset(factory.tasks.get("code-02"))
        (sb.workspace / "where.py").write_text(
            "import socket, sys\nprint(sys.executable)\ntry:\n    socket.create_connection(('1.1.1.1', 53), "
            "timeout=3); print('CONNECTED')\nexcept OSError:\n    print('offline')\n")
        async with MCPSessionPool(factory.declared, ["code", "git"], sb.env(), sb.launch) as pool:
            out = json.loads((await pool.call("code.run_python", {"path": "where.py"})).text)["output"]
            assert out.split() == ["/usr/local/bin/python", "offline"]
            tests = json.loads((await pool.call("code.run_tests", {"path": "tests"})).text)
            assert tests["exit_code"] == 1 and tests["counts"]["failed"] == 2
            assert (await pool.call("git.status", {})).text.splitlines()[0] == "## main"
        answer = sb.probe({"op": "pytest", "targets": ["tests/test_discount.py"]}, 60)
        assert answer["exit_code"] == 1 and answer["counts"]["failed"] == 1
        assert sb.probe({"op": "git_commits", "ref": "main", "since": sb.git_seed}, 30) == {"commits": []}
    finally:
        sb.teardown()


async def test_business_state_inside_the_container(factory, docker_required, monkeypatch):
    """The biz server runs in the container and writes the business state in /state (the only writable mount
    next to /work); graders read the host side of the same file."""
    monkeypatch.setattr(settings, "SANDBOX", "docker")
    rec = await run_one(factory, factory.tasks.get("biz-01"), factory.recipes["v2-biz"], "scripted/replay",
                        "scripted")
    assert rec.status.value == "SUCCESS", rec.grades
    state = next(g for g in rec.grades if g["grader_id"] == "state_diff")
    assert state["snapshot"]["sandbox"] == "docker" and "+biz credit_notes/CN-0004" in state["detail"]


async def test_web_corpus_inside_the_container(factory, docker_required, monkeypatch):
    """ADR 0018: the web server runs in the container over the corpus mounted read-only at /state/web; nothing in
    the container can change it, and the run records which corpus version it saw."""
    monkeypatch.setattr(settings, "SANDBOX", "docker")
    task = factory.tasks.get("web-01")
    rec = await run_one(factory, task, factory.recipes["v2-web"], "scripted/replay", "scripted")
    assert rec.status.value == "SUCCESS", rec.grades
    snap = next(g for g in rec.grades if g["grader_id"] == "state_diff")["snapshot"]
    assert snap["sandbox"] == "docker" and snap["web_corpus"]["version"] == task.env_seed.web_corpus(
        task.source_dir).version
    sb = DockerSandbox("dk-web")
    try:
        sb.reset(task)
        r = _exec(sb, "import os",
                  "for p in ('/state/web/corpus.json', '/state/web/new'):",
                  "    try:",
                  "        open(p, 'a').write('x'); print(p, 'writable')",
                  "    except OSError:",
                  "        print(p, 'denied')")
        assert dict(line.split() for line in r.stdout.strip().splitlines()) == {
            "/state/web/corpus.json": "denied", "/state/web/new": "denied"}
        async with MCPSessionPool(factory.declared, ["web"], sb.env(), sb.launch) as pool:
            page = json.loads((await pool.call("web.fetch", {"url": "https://docs.relaymail.example/rate-limits"})).text)
            assert page["url"] == "https://docs.relaymail.example/v2/rate-limits" and "600" in page["text"]
    finally:
        sb.teardown()


async def test_outbox_and_calendar_states_inside_the_container(factory, docker_required, monkeypatch):
    """The outbox and calendar servers run in the container and write their states in /state, next to the
    database and biz.json; graders read the host side of the same files."""
    monkeypatch.setattr(settings, "SANDBOX", "docker")
    sb = make_sandbox("comms-docker")
    try:
        assert (sb.env()["HF_OUTBOX"], sb.env()["HF_CALENDAR"]) == ("/state/outbox.json", "/state/calendar.json")
        assert sb.state_path("outbox").parent == sb.db_path.parent == sb.biz_path.parent
    finally:
        sb.teardown()
    rec = await run_one(factory, factory.tasks.get("cal-01"), factory.recipes["v2-outbox"], "scripted/replay",
                        "scripted")
    assert rec.status.value == "SUCCESS", rec.grades
    state = next(g for g in rec.grades if g["grader_id"] == "state_diff")
    assert state["snapshot"]["sandbox"] == "docker"
    assert "+outbox deliveries/D-0004" in state["detail"] and "~calendar events/EV-0107" in state["detail"]
