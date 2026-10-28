"""ErrorKind (B3): one typed error taxonomy shared by every shape."""

from __future__ import annotations

from enum import Enum


class ErrorKind(str, Enum):
    TOOL_ARGS = "TOOL_ARGS"
    TOOL_TIMEOUT = "TOOL_TIMEOUT"
    TOOL_ERROR = "TOOL_ERROR"
    TOOL_NOT_ALLOWED = "TOOL_NOT_ALLOWED"
    MODEL_PARSE = "MODEL_PARSE"
    MODEL_ERROR = "MODEL_ERROR"
    RATE_LIMITED = "RATE_LIMITED"          # never a harness failure
    QUOTA_EXHAUSTED = "QUOTA_EXHAUSTED"    # never a harness failure
    BUDGET = "BUDGET"
    POLICY = "POLICY"
    SANDBOX = "SANDBOX"
    PROVISION = "PROVISION"


class HarnessError(Exception):
    def __init__(self, kind: ErrorKind, message: str) -> None:
        super().__init__(f"{kind.value}: {message}")
        self.kind = kind
        self.message = message
