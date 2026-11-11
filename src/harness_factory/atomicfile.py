"""Atomic file replacement that survives Windows readers.

`os.replace` is atomic on every OS, but on Windows it fails with PermissionError (WinError 5 or 32) while another
process has the destination open, even just to read it. A reader closes the file within moments, so the rename
is retried briefly before the error is raised as before. On POSIX the first attempt succeeds whenever the
filesystem allows it, so nothing changes there.

MCP server modules (env/mcp/impl/) keep their own `os.replace`: they may import only mcp, the standard library and
their allow-listed libraries, and each one's state file is read only by that server.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable

# Waits between attempts: about 1.6 s in all. A synchronous section (the quota ledger's lock) may sit in it, so it
# stays short; a reader that holds a file longer than this is not a brief read.
RETRY_DELAYS_S: tuple[float, ...] = (0.02, 0.05, 0.1, 0.2, 0.4, 0.8)


def replace(src: str | os.PathLike, dst: str | os.PathLike, *, delays: tuple[float, ...] = RETRY_DELAYS_S,
            sleep: Callable[[float], None] = time.sleep) -> None:
    """`os.replace(src, dst)`, retried on PermissionError only. Any other error, and a PermissionError that outlasts
    every retry, propagates unchanged."""
    for delay in delays:
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            sleep(delay)
    os.replace(src, dst)

