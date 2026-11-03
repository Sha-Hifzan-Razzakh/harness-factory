ARG BASE
FROM ${BASE}
# git for the git server and git-seeded tasks (ADR 0012). Skipped when the base image already has it, so a base
# with git preinstalled builds without network access; otherwise the base must be Debian-based (apt).
RUN command -v git >/dev/null 2>&1 || (apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*)
COPY vendor /opt/hf/vendor
COPY harness_factory /opt/hf/src/harness_factory
ENV PYTHONPATH=/opt/hf/src:/opt/hf/vendor \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /work
