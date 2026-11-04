"""What makes a runner run a different experiment (RUN-04, FWK-03): the runner id and the exact version of every
framework package that shapes the agent's behaviour (its loop, prompts, tool formatting, MCP tool adapter).

Versions are read from the installed distributions (pinned in uv.lock), so upgrading a framework changes the recipe
hash of every recipe that runs on it and starts new cells, while native recipes never see these packages.
"""

from __future__ import annotations

from importlib import metadata

# Packages whose code sits between the model client and the tool callables for each runner. Not listed: packages a
# runner imports but whose code doesn't touch what the model sees or how its calls are run (e.g. fastmcp, which
# langchain.mcp imports but whose client the runner doesn't use: tools run through ToolExecutor).
RUNNER_PACKAGES: dict[str, tuple[str, ...]] = {
    "langgraph-prebuilt": ("langchain", "langchain-core", "langgraph", "langgraph-prebuilt"),
    "llamaindex-agent": ("llama-index-core", "llama-index-workflows", "llama-index-tools-mcp"),
}

# The optional extra that installs each runner (pyproject.toml).
RUNNER_EXTRAS: dict[str, str] = {"langgraph-prebuilt": "langgraph-prebuilt", "llamaindex-agent": "llamaindex"}


def package_version(dist: str) -> str | None:
    try:
        return metadata.version(dist)
    except metadata.PackageNotFoundError:
        return None


def framework_versions(runtime: str) -> dict[str, str | None]:
    """{distribution: installed version or None} for one runner. None (not installed, e.g. the factory image without
    the extra) still hashes, but such a recipe is refused when a run is provisioned (`runner_installed`)."""
    return {dist: package_version(dist) for dist in RUNNER_PACKAGES[runtime]}


def missing_packages(runtime: str) -> list[str]:
    return [d for d, v in framework_versions(runtime).items() if v is None]


def runner_identity(runtime: str) -> dict:
    """Folded into the recipe hash of a recipe whose runtime is a framework runner (never for native)."""
    return {"runtime": runtime, "packages": framework_versions(runtime)}
