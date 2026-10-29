ARG BASE
FROM ${BASE}
COPY vendor /opt/hf/vendor
COPY harness_factory /opt/hf/src/harness_factory
ENV PYTHONPATH=/opt/hf/src:/opt/hf/vendor \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /work
