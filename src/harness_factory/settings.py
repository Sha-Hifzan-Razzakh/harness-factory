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


def remote_mode() -> str:
    """How remote MCP servers are answered (EXT-08): live (default), record (live, and every answer saved) or
    replay (from the recording: offline, no token, no opt-in needed). HF_REMOTE_MODE, read at call time."""
    mode = os.environ.get("HF_REMOTE_MODE", "live")
    if mode not in ("live", "record", "replay"):
        raise ValueError(f"HF_REMOTE_MODE must be live, record or replay, not {mode!r}")
    return mode


def remote_recordings() -> Path:
    return Path(os.environ.get("HF_REMOTE_RECORDINGS", VAR / "remote"))
