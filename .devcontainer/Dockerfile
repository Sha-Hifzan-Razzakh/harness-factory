# Development container: Python 3.13 + uv + git (+ Node from the devcontainer feature, for web/).
# Only the project folder is mounted (devcontainer.json); nothing else on the host is visible.
FROM mcr.microsoft.com/devcontainers/python:1-3.13-bookworm
COPY --from=ghcr.io/astral-sh/uv:0.11.32 /uv /uvx /usr/local/bin/
