"""The libraries a built-in MCP server may import beyond `mcp` and the standard library (ADR 0013).

A server runs inside every run's sandbox, which has no network, so whatever it imports must already be there.
This module is the allow-list: each library is pinned to one exact version, and each server names the libraries
it uses. The same pins are

- the `docs` optional extra in pyproject.toml (uv.lock locks them and their dependencies), for local sandboxes and
  for the graders that read office files in the factory process;
- vendored into the Docker sandbox image next to `mcp` (env/sandbox_image.py), and part of its tag;
- part of `env_hash` for the servers that use them (env/mcp/servers.py), so a library upgrade is a new
  experiment rather than a silent change to what a tool returns.

Adding a library: pure Python or wheels for manylinux x86_64 and aarch64, small, maintained, no network or
subprocess use; pin it here and in the extra (tests/test_docs_server.py checks they agree), run `uv lock`.
"""

from __future__ import annotations

import importlib.metadata as md
import importlib.util
from dataclasses import dataclass

from harness_factory.substrate.errors import ErrorKind, HarnessError

EXTRA = "docs"     # the optional extra in pyproject.toml that installs the allow-list


@dataclass(frozen=True)
class Library:
    dist: str          # distribution name on PyPI
    version: str       # the one version allowed
    imports: str       # top-level import name

    @property
    def requirement(self) -> str:
        return f"{self.dist}=={self.version}"


ALLOWED: dict[str, Library] = {lib.dist: lib for lib in (
    Library("python-docx", "1.2.0", "docx"),        # .docx; needs lxml (wheels) and typing_extensions
    Library("openpyxl", "3.1.5", "openpyxl"),       # .xlsx; needs et-xmlfile (pure Python)
    Library("pypdf", "6.20.1", "pypdf"),            # .pdf, read only; pure Python
)}

# Server module -> the allow-listed libraries it imports. A module that isn't listed imports only mcp and the
# standard library.
SERVER_LIBRARIES: dict[str, tuple[str, ...]] = {
    "harness_factory.env.mcp.impl.docs_server": ("python-docx", "openpyxl", "pypdf"),
}

# Check kinds whose grader reads office files in the factory process (grading/state.py).
CHECK_LIBRARIES: dict[str, tuple[str, ...]] = {
    "xlsx_cell": ("openpyxl",),
    "docx_contains": ("python-docx",),
    "docx_paragraph_regex": ("python-docx",),
}


def requirements(module: str | None) -> list[str]:
    """Pinned requirements of one server module (empty: it imports only mcp and the standard library)."""
    return requirements_for(SERVER_LIBRARIES.get(module or "", ()))


def image_requirements() -> list[str]:
    """The whole allow-list, pinned: what the sandbox image vendors next to mcp."""
    return requirements_for(tuple(ALLOWED))


def problems(dists: tuple[str, ...] | list[str]) -> list[str]:
    """What is wrong with these libraries in this Python environment: not installed, or another version."""
    out = []
    for d in dists:
        lib = ALLOWED[d]
        if importlib.util.find_spec(lib.imports) is None:
            out.append(f"{lib.dist} is not installed")
            continue
        try:
            have = md.version(lib.dist)
        except md.PackageNotFoundError:
            out.append(f"{lib.dist} is not installed (only an import named {lib.imports!r} is)")
            continue
        if have != lib.version:
            out.append(f"{lib.dist} {have} is installed, but the allow-list pins {lib.version}")
    return out


def require(dists: tuple[str, ...] | list[str], what: str) -> None:
    """Refuse to go on (a setup error, never a scored run) when `what` needs libraries this environment lacks."""
    found = problems(dists)
    if found:
        raise HarnessError(ErrorKind.PROVISION, f"{what} needs the optional extra {EXTRA!r} "
                                                f"({', '.join(requirements_for(dists))}): {'; '.join(found)}. "
                                                f"Install it with `uv sync --extra {EXTRA}` (keep your other "
                                                f"extras on the same command line)")


def requirements_for(dists: tuple[str, ...] | list[str]) -> list[str]:
    return [ALLOWED[d].requirement for d in dists]


def require_server(module: str | None, server_id: str) -> None:
    """A local sandbox runs the server with the factory's own Python: its libraries must be installed here."""
    dists = SERVER_LIBRARIES.get(module or "", ())
    if dists:
        require(dists, f"MCP server {server_id!r}")


def check_libraries(kinds: set[str]) -> list[str]:
    return sorted({d for k in kinds for d in CHECK_LIBRARIES.get(k, ())})
