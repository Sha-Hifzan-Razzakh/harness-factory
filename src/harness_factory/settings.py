"""Paths and environment settings. Everything the factory reads from disk is resolved from here."""

from __future__ import annotations

import os
from pathlib import Path


def _find_root() -> Path:
    env = os.environ.get("HF_ROOT")
    if env:
        return Path(env).resolve()
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "pyproject.toml").exists() and (parent / "catalog").exists():
            return parent
    return Path.cwd()


ROOT = _find_root()


def _load_dotenv(path: Path) -> None:
    """Minimal .env reader: KEY=VALUE lines; real environment variables win."""
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        if value.strip():
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(ROOT / ".env")
CATALOG = ROOT / "catalog"
PROMPTS = ROOT / "prompts"
VAR = Path(os.environ.get("HF_VAR", ROOT / "var"))

TASKS_DIR = CATALOG / "tasks"
RECIPES_DIR = CATALOG / "recipes"
PROVIDERS_FILE = CATALOG / "providers.yaml"
TOOLS_FILE = CATALOG / "tools.yaml"
FAULTS_DIR = CATALOG / "faults"
SKILLS_DIR = CATALOG / "skills"

RESULTS_URL = os.environ.get("HF_RESULTS_URL", f"sqlite:///{VAR / 'results.db'}")
TRACES_DIR = VAR / "traces"
QUOTA_STATE = Path(os.environ.get("HF_QUOTA_STATE", VAR / "quota_ledger.json"))   # provider usage, survives restarts
SANDBOXES_DIR = VAR / "sandboxes"
EXPERIMENTS_DIR = Path(os.environ.get("HF_EXPERIMENTS_DIR", VAR / "experiments"))  # saved experiments (API/UI)
HISTORY_DIR = Path(os.environ.get("HF_HISTORY_DIR", VAR / "catalog_history"))  # catalog versions (authoring)
CORPORA_DIR = VAR / "corpora"        # compiled web corpora, one per version and engine (ADR 0018)

OTLP_ENDPOINT = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")  # e.g. http://localhost:4318
MLFLOW_TRACKING_URI = os.environ.get("MLFLOW_TRACKING_URI")

CALL_TIMEOUT_S = int(os.environ.get("HF_CALL_TIMEOUT_S", "120"))   # per model call, unless the recipe sets one

SANDBOX = os.environ.get("HF_SANDBOX", "local")                                 # local | docker
SANDBOX_BASE_IMAGE = os.environ.get("HF_SANDBOX_BASE_IMAGE", "python:3.12-slim")
SANDBOX_PYTHON = os.environ.get("HF_SANDBOX_PYTHON", "3.12")                     # must match the base image
DOCKER_BIN = os.environ.get("HF_DOCKER", "docker")


def ensure_dirs() -> None:
    for d in (VAR, TRACES_DIR, SANDBOXES_DIR):
        d.mkdir(parents=True, exist_ok=True)


def allow_remote() -> bool:
    """Live calls to remote MCP servers are opt-in (ENV-02): HF_ALLOW_REMOTE=1. Read at call time."""
    return os.environ.get("HF_ALLOW_REMOTE") == "1"


def allow_local_code() -> bool:
    """Code a real model wrote runs on this machine in a local sandbox (the code server, tests_pass checks): opt-in
    with HF_ALLOW_LOCAL_CODE=1, or use HF_SANDBOX=docker (ADR 0012). Scripted runs never need it. Read at call time."""
    return os.environ.get("HF_ALLOW_LOCAL_CODE") == "1"


def remote_mode() -> str:
    """How remote MCP servers are answered (EXT-08): live (default), record (live, and every answer saved) or
    replay (from the recording: offline, no token, no opt-in needed). HF_REMOTE_MODE, read at call time."""
    mode = os.environ.get("HF_REMOTE_MODE", "live")
    if mode not in ("live", "record", "replay"):
        raise ValueError(f"HF_REMOTE_MODE must be live, record or replay, not {mode!r}")
    return mode


def health_timeout_s() -> float:
    """ENV-06: how long a tool server may take to start, answer initialize, list its tools and answer a ping before
    it counts as unhealthy and the run is refused (ERROR, not scored). HF_MCP_HEALTH_TIMEOUT_S, read at call time."""
    return float(os.environ.get("HF_MCP_HEALTH_TIMEOUT_S", "60"))


def remote_recordings() -> Path:
    return Path(os.environ.get("HF_REMOTE_RECORDINGS", VAR / "remote"))


def checkpoint_backend() -> str | None:
    """LangGraph checkpointing of V2-V5 runs, so a paused run resumes from its last node (SUB-09): HF_CHECKPOINT =
    off (default) | sqlite | postgres. Read at call time. Off changes nothing about how runs execute or score."""
    value = os.environ.get("HF_CHECKPOINT", "off").strip().lower()
    if value in ("", "off", "0", "none"):
        return None
    if value not in ("sqlite", "postgres"):
        raise ValueError(f"HF_CHECKPOINT must be off, sqlite or postgres, not {value!r}")
    return value


def checkpoint_url(backend: str) -> str:
    """Where checkpoints go: HF_CHECKPOINT_URL, a file path for sqlite (default var/checkpoints.db) or a Postgres
    conninfo string for postgres (required; keep credentials in .env)."""
    url = os.environ.get("HF_CHECKPOINT_URL", "")
    if url or backend != "sqlite":
        return url
    return str(VAR / "checkpoints.db")


def trace_messages() -> str:
    """What model-call spans send to OpenTelemetry (Phoenix, Langfuse): HF_TRACE_MESSAGES = full | delta (default) |
    off. full: every message of every call; delta: a conversation's messages once, then only those added since its
    previous call; off: token counts only. Observability only: the JSONL trace, scores and hashes never change."""
    value = os.environ.get("HF_TRACE_MESSAGES", "delta").strip().lower() or "delta"
    if value not in ("full", "delta", "off"):
        raise ValueError(f"HF_TRACE_MESSAGES must be full, delta or off, not {value!r}")
    return value


def trace_max_chars() -> int:
    """HF_TRACE_MAX_CHARS: the longest message content or tool argument/result copied to an OpenTelemetry span
    (default 8000; the JSONL trace keeps its own shorter previews)."""
    return max(100, int(os.environ.get("HF_TRACE_MAX_CHARS", "8000")))


def trace_mcp_spans() -> bool:
    """HF_TRACE_MCP_SPANS=1 keeps the MCP SDK's own `MCP send ...` spans under every tool call; by default they are
    dropped from the OpenTelemetry export (our tool span already carries the call and its result)."""
    return os.environ.get("HF_TRACE_MCP_SPANS") == "1"
