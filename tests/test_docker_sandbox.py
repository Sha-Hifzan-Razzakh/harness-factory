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
              "for p in ('/etc/hf-probe', '/opt/hf/probe', '/work/probe', '/state/probe', '/tmp/probe'):",
              "    try:",
              "        open(p, 'w').write('x'); print(p, 'writable')",
              "    except OSError:",
              "        print(p, 'denied')")
    status = dict(line.split() for line in r.stdout.strip().splitlines())
    assert status == {"/etc/hf-probe": "denied", "/opt/hf/probe": "denied", "/work/probe": "writable",
                      "/state/probe": "writable", "/tmp/probe": "writable"}


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
