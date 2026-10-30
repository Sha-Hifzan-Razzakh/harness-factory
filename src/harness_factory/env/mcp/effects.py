"""Per-call side-effect escalation: a tool whose declared side effect holds for most calls, but not all.

`sql.execute` is REVERSIBLE (R2): a bounded row change such as `UPDATE ... WHERE id = 5` or `DELETE ... WHERE id = 11`
is the work data tasks ask for. A statement that destroys data wholesale is not: `DELETE FROM orders` (no WHERE),
`DROP TABLE`, `ALTER TABLE ... DROP COLUMN`. Those are IRREVERSIBLE (R3), so a recipe's `IRREVERSIBLE: DENY` refuses
them like `fs.delete_file`. The tool declares this in tools.yaml (`escalation:`); the ToolExecutor applies it per call,
before the permission check.

A classifier is a pure function of the call's arguments. It sees the arguments only, never the environment, so the
same call always gets the same side effect (run identity stays a function of the declared catalog).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

# String literals, quoted identifiers and comments are removed before matching, so text inside them never counts.
_LITERALS = re.compile(r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"|`[^`]*`|\[[^\]]*\]|--[^\n]*|/\*.*?\*/", re.DOTALL)
_DROP = re.compile(r"^\s*DROP\b", re.IGNORECASE)
_TRUNCATE = re.compile(r"^\s*TRUNCATE\b", re.IGNORECASE)
_ALTER_DROP = re.compile(r"^\s*ALTER\s+TABLE\b.*\bDROP\b", re.IGNORECASE | re.DOTALL)
_DELETE = re.compile(r"^\s*(?:WITH\b.*?\)\s*)?DELETE\s+FROM\b(?P<rest>.*)$", re.IGNORECASE | re.DOTALL)
_WHERE = re.compile(r"\bWHERE\b(?P<cond>.*)$", re.IGNORECASE | re.DOTALL)
# Words that are not a reference to a column: a WHERE made only of these is true for every row (`WHERE 1`, `WHERE
# 1 = 1`, `WHERE TRUE`, `WHERE NOT NULL IS NULL`).
_NOT_COLUMNS = {"and", "or", "not", "is", "null", "true", "false", "like", "glob", "in", "between"}
_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _strip(sql: str) -> str:
    return _LITERALS.sub(" '' ", sql or "").strip().rstrip(";").strip()


def _scoped(where_cond: str) -> bool:
    """A WHERE condition that names at least one column; a constant one matches every row."""
    words = {w.lower() for w in _WORD.findall(where_cond)}
    return bool(words - _NOT_COLUMNS)


def sql_destructive(args: dict[str, Any]) -> bool:
    """True for statements that destroy rows or schema wholesale: DROP, TRUNCATE, ALTER TABLE ... DROP, and a DELETE
    whose WHERE is missing or names no column. A DELETE scoped by a condition on a column stays the tool's declared
    side effect, like an UPDATE ... WHERE."""
    sql = _strip(str(args.get("sql", "")))
    if _DROP.match(sql) or _TRUNCATE.match(sql) or _ALTER_DROP.match(sql):
        return True
    m = _DELETE.match(sql)
    if m is None:
        return False
    where = _WHERE.search(m.group("rest"))
    return where is None or not _scoped(where.group("cond"))


CLASSIFIERS: dict[str, Callable[[dict[str, Any]], bool]] = {
    "sql_destructive": sql_destructive,
}
