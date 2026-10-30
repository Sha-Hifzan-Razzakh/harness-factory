"""sql MCP server: SQLite tools over the run's task database (HF_DB)."""

from __future__ import annotations

import os
import sqlite3

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError  # message reaches the model; ValueError would be masked

app = MCPServer("sql", instructions="SQL tools for the task database (SQLite dialect).")

_WRITE_PREFIXES = ("insert", "update", "delete", "replace")


def _connect(read_only: bool) -> sqlite3.Connection:
    db = os.environ["HF_DB"]
    if read_only:
        return sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    return sqlite3.connect(db)


@app.tool()
def list_tables() -> list[str]:
    """List the tables in the database."""
    with _connect(True) as c:
        return [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]


@app.tool()
def describe_table(table: str) -> list[dict]:
    """Describe a table's columns: name, type, primary key."""
    with _connect(True) as c:
        rows = c.execute("SELECT name, type, pk FROM pragma_table_info(?)", (table,)).fetchall()
    if not rows:
        raise ToolError(f"no such table: {table}")
    return [{"name": n, "type": t, "primary_key": bool(pk)} for n, t, pk in rows]


@app.tool()
def query(sql: str) -> dict:
    """Run one read-only SELECT statement and return its columns and rows (at most 200 rows)."""
    if not sql.lstrip().lower().startswith(("select", "with")):
        raise ToolError("query only runs SELECT statements; use execute for changes")
    try:
        with _connect(True) as c:
            cur = c.execute(sql)
            cols = [d[0] for d in cur.description or []]
            rows = cur.fetchmany(200)
    except sqlite3.Error as e:
        raise ToolError(f"SQLite error: {e}") from e
    return {"columns": cols, "rows": [list(r) for r in rows]}


@app.tool()
def execute(sql: str) -> dict:
    """Run one INSERT, UPDATE, DELETE or REPLACE statement and commit it. Returns the number of rows changed."""
    stmt = sql.strip().rstrip(";")
    if ";" in stmt:
        raise ToolError("execute runs exactly one statement")
    if not stmt.lower().startswith(_WRITE_PREFIXES):
        raise ToolError("execute only runs INSERT, UPDATE, DELETE or REPLACE")
    try:
        with _connect(False) as c:
            cur = c.execute(stmt)
            c.commit()
            return {"rows_changed": cur.rowcount}
    except sqlite3.Error as e:
        raise ToolError(f"SQLite error: {e}") from e


if __name__ == "__main__":
    app.run()
