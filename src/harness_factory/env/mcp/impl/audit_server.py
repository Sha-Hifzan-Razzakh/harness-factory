"""audit MCP server: the degree audit engine over the run's SIS database (ADR 0021). Read-only.

The engine is degree_audit_core, pinned in the server allow-list (env/mcp/deps.py) to one commit of the DegreeAudit
repository. This server only adapts it to MCP: it opens the SIS in HF_DB read-only, loads the compiled rules from
HF_REFERENCE (env_seed.reference, mounted read-only next to the database) and calls the core's three tools. Their
names, descriptions and refusals are the core's own (contracts C01-C03), so the agent sees the same tools a
DegreeAudit service would offer. A refusal reaches the model as a ToolError that starts with the core's code
(UNKNOWN_STUDENT, CONFIG_INVALID, ...).
"""

from __future__ import annotations

import functools
import os
from pathlib import Path
from typing import Literal

from degree_audit_core import tools as core
from degree_audit_core.adapters.sqlite_sis import SqliteSis
from degree_audit_core.descriptions import description
from degree_audit_core.issues import ToolError as Refusal
from degree_audit_core.reference import Reference
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError  # message reaches the model; ValueError would be masked

app = MCPServer("audit", instructions="Degree audit tools over the student information system (SIS). Read-only. "
                                      "The engine applies the institution's rules; a registrar decides.")

Mode = Literal["clearance", "projected", "what_if"]


@functools.lru_cache(maxsize=1)
def _load(path: str) -> Reference:
    return Reference.load(path)


def _reference() -> Reference:
    path = os.environ.get("HF_REFERENCE")
    if not path or not Path(path).is_file():
        raise ToolError("no reference data is mounted for this run (the task sets no env_seed.reference)")
    return _load(path)


def _run(fn, *args, **kwargs) -> dict:
    """Call one core tool on a read-only SIS connection; a core refusal becomes a ToolError with its code."""
    ref = _reference()
    sis = SqliteSis(os.environ["HF_DB"])
    try:
        return fn(sis, *args, ref=ref, **kwargs)
    except Refusal as e:
        raise ToolError(f"{e.code}: {e}") from e
    finally:
        sis.close()


@app.tool(name="validate_record", description=description("audit.validate_record"))
def validate_record(student_id: str, as_of_term: str | None = None) -> dict:
    return _run(core.validate_record, student_id, as_of_term)


@app.tool(name="evaluate", description=description("audit.evaluate"))
def evaluate(student_id: str, as_of_term: str | None = None, mode: Mode = "clearance",
             approvals_as_of: str | None = None, study_plan_version: str | None = None) -> dict:
    return _run(core.evaluate, student_id, as_of_term, mode, approvals_as_of, study_plan_version)


@app.tool(name="explain", description=description("audit.explain"))
def explain(student_id: str, requirement_id: str, as_of_term: str | None = None, mode: Mode = "clearance",
            approvals_as_of: str | None = None) -> dict:
    return _run(core.explain, student_id, requirement_id, as_of_term, mode, approvals_as_of)


if __name__ == "__main__":
    app.run()
