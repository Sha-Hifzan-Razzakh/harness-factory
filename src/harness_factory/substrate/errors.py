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
    PROVIDER_ERROR = "PROVIDER_ERROR"      # 5xx, timeout, connection: infrastructure, never a harness failure
    CONFIG = "CONFIG"                      # auth, unknown model id, rejected request: fix the setup
    BUDGET = "BUDGET"
    POLICY = "POLICY"
    SANDBOX = "SANDBOX"
    PROVISION = "PROVISION"


# How a stop reason counts. Infrastructure and configuration failures say nothing about the harness, so they
# are excluded from cell means (results.store) and reported as their own rate.
INFRA_KINDS = frozenset({ErrorKind.RATE_LIMITED, ErrorKind.QUOTA_EXHAUSTED, ErrorKind.PROVIDER_ERROR})
RETRYABLE_KINDS = frozenset({ErrorKind.RATE_LIMITED, ErrorKind.PROVIDER_ERROR})


class HarnessError(Exception):
    def __init__(self, kind: ErrorKind, message: str) -> None:
        super().__init__(f"{kind.value}: {message}")
        self.kind = kind
        self.message = message
