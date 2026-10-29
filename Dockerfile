# The factory itself: hfx, the catalog and the prompts, in one image.
#
#   docker build -t harness-factory .
#   docker run --rm harness-factory smoke --models scripted/replay --strict
#
# compose.yaml wraps this (`docker compose run --rm factory ...`), including Docker sandboxes from inside the
# container. The sandbox image the factory builds for each run is a different, smaller image:
# src/harness_factory/env/sandbox.Dockerfile.
#
# Every base is a build argument, so a mirror or an air-gapped registry can stand in for Docker Hub and GHCR.
ARG PYTHON_IMAGE=python:3.13-slim
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.11.32
ARG DOCKER_CLI_IMAGE=docker:29-cli

FROM ${UV_IMAGE} AS uv
FROM ${DOCKER_CLI_IMAGE} AS docker-cli

# ---------------------------------------------------------------- build: locked dependencies into /app/.venv
FROM ${PYTHON_IMAGE} AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /app
# Optional extras, space-separated: postgres (results store in Postgres), mlops (MLflow), inspect (Inspect AI).
ARG EXTRAS="postgres"
COPY pyproject.toml uv.lock README.md ./
# Dependencies first, so editing the code does not reinstall them.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project $(for e in $EXTRAS; do printf -- '--extra %s ' "$e"; done)
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev $(for e in $EXTRAS; do printf -- '--extra %s ' "$e"; done)

# ---------------------------------------------------------------- runtime
FROM ${PYTHON_IMAGE}
# uv vendors the MCP closure into the sandbox image; the Docker CLI talks to the host daemon (HF_SANDBOX=docker).
COPY --from=uv /uv /usr/local/bin/uv
COPY --from=docker-cli /usr/local/bin/docker /usr/local/bin/docker
RUN useradd --create-home --uid 10001 hf
WORKDIR /app
COPY --from=build /app/.venv ./.venv
COPY pyproject.toml ./
COPY src ./src
COPY catalog ./catalog
COPY prompts ./prompts
ENV PATH=/app/.venv/bin:$PATH \
    HF_ROOT=/app \
    HF_VAR=/app/var \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
RUN mkdir -p /app/var && chown hf:hf /app/var
USER hf
ENTRYPOINT ["hfx"]
CMD ["--help"]
