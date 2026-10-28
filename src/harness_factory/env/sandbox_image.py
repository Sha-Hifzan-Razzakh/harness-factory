"""SandboxImage (B1): the container image DockerSandbox runs MCP servers in.

The image holds only what the MCP servers need: Python, the `mcp` package closure, and the server modules.
It is built without network access inside the build:

  1. dependencies are resolved on the host for the image's Python version and CPU (uv, or pip as a fallback),
     constrained to the versions installed in the factory's own environment (which came from uv.lock);
  2. the Dockerfile only COPYs them in; there is no RUN step.

So the same build works on CI, on a laptop, and behind a proxy that blocks package mirrors inside containers.
The tag is a content hash of everything that goes in, so a changed server module or mcp version rebuilds.
"""

from __future__ import annotations

import importlib.metadata as md
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from harness_factory import settings
from harness_factory.hashing import content_hash
from harness_factory.substrate.errors import ErrorKind, HarnessError

PACKAGE_ROOT = Path(__file__).resolve().parents[1]           # src/harness_factory
SERVER_FILES = [
    "__init__.py",
    "env/__init__.py",
    "env/mcp/__init__.py",
    *sorted(str(p.relative_to(PACKAGE_ROOT)) for p in (PACKAGE_ROOT / "env/mcp/impl").glob("*.py")),
]

DOCKERFILE = """\
ARG BASE
FROM ${BASE}
COPY vendor /opt/hf/vendor
COPY harness_factory /opt/hf/src/harness_factory
ENV PYTHONPATH=/opt/hf/src:/opt/hf/vendor \\
    PYTHONDONTWRITEBYTECODE=1 \\
    PYTHONUNBUFFERED=1
WORKDIR /work
"""

_ARCH = {"amd64": "x86_64", "x86_64": "x86_64", "arm64": "aarch64", "aarch64": "aarch64"}
_ensured: dict[str, str] = {}


def docker(*args: str, timeout: float = 120, check: bool = True) -> subprocess.CompletedProcess:
    try:
        proc = subprocess.run([settings.DOCKER_BIN, *args], check=False, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as e:
        raise HarnessError(ErrorKind.SANDBOX, f"{settings.DOCKER_BIN!r} not found; install Docker or use "
                                              f"HF_SANDBOX=local") from e
    if check and proc.returncode != 0:
        raise HarnessError(ErrorKind.SANDBOX, f"docker {args[0]} failed: {proc.stderr.strip()[:500]}")
    return proc


def docker_available() -> bool:
    try:
        return docker("version", "--format", "{{.Server.Version}}", timeout=15, check=False).returncode == 0
    except HarnessError:
        return False


def server_arch() -> str:
    arch = docker("version", "--format", "{{.Server.Arch}}", timeout=15).stdout.strip()
    if arch not in _ARCH:
        raise HarnessError(ErrorKind.SANDBOX, f"unsupported docker server architecture {arch!r}")
    return _ARCH[arch]


def image_tag(base: str, python: str, arch: str) -> str:
    files = {f: (PACKAGE_ROOT / f).read_text() for f in SERVER_FILES}
    return "hf-sandbox:" + content_hash({"base": base, "python": python, "arch": arch, "dockerfile": DOCKERFILE,
                                         "mcp": md.version("mcp"), "files": files}, 12)


def _constraints() -> str:
    """Pin the vendored closure to what this environment runs (installed from uv.lock)."""
    lines = sorted({f"{d.metadata['Name']}=={d.version}" for d in md.distributions() if d.metadata["Name"]})
    return "\n".join(lines) + "\n"


def _vendor(target: Path, python: str, arch: str, workdir: Path) -> None:
    cons = workdir / "constraints.txt"
    cons.write_text(_constraints())
    req = f"mcp=={md.version('mcp')}"
    uv = shutil.which("uv")
    if uv:
        cmd = [uv, "pip", "install", "--quiet", "--target", str(target), "--python-version", python,
               "--python-platform", f"{arch}-manylinux_2_28", "--only-binary", ":all:", "-c", str(cons), req]
    else:
        cmd = [sys.executable, "-m", "pip", "install", "--quiet", "--target", str(target), "--python-version",
               python, "--platform", f"manylinux_2_28_{arch}", "--only-binary", ":all:", "-c", str(cons), req]
    proc = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=600)
    if proc.returncode != 0:
        raise HarnessError(ErrorKind.SANDBOX, f"vendoring mcp for the sandbox failed: {proc.stderr.strip()[:800]}")


def ensure_image(base: str | None = None, python: str | None = None) -> str:
    """Return the sandbox image tag, building it first if it does not exist yet."""
    base = base or settings.SANDBOX_BASE_IMAGE
    python = python or settings.SANDBOX_PYTHON
    key = f"{base}|{python}"
    if key in _ensured:
        return _ensured[key]
    tag = image_tag(base, python, server_arch())
    if docker("image", "inspect", tag, check=False, timeout=30).returncode != 0:
        with tempfile.TemporaryDirectory(prefix="hf-image-") as tmp:
            ctx = Path(tmp)
            _vendor(ctx / "vendor", python, server_arch(), ctx)
            for f in SERVER_FILES:
                dest = ctx / "harness_factory" / f
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(PACKAGE_ROOT / f, dest)
            (ctx / "Dockerfile").write_text(DOCKERFILE)
            try:
                docker("build", "--quiet", "--build-arg", f"BASE={base}", "-t", tag, str(ctx), timeout=900)
            except HarnessError as e:
                raise HarnessError(ErrorKind.SANDBOX, f"{e.message}\nHint: the base image {base!r} must be "
                                   f"pullable or already present locally. Point HF_SANDBOX_BASE_IMAGE at an image "
                                   f"you can reach (and HF_SANDBOX_PYTHON at its Python version), or use "
                                   f"HF_SANDBOX=local.") from e
        probe = docker("run", "--rm", "--network", "none", tag, "python", "-c",
                       "import mcp, harness_factory.env.mcp.impl.fs_server, harness_factory.env.mcp.impl.sql_server",
                       check=False, timeout=120)
        if probe.returncode != 0:
            raise HarnessError(ErrorKind.SANDBOX, f"sandbox image {tag} cannot import the MCP servers "
                                                  f"(base {base}, python {python}): {probe.stderr.strip()[:500]}")
    _ensured[key] = tag
    return tag
