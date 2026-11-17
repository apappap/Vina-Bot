"""Read-only SQL guard built on a syntax tree, not on regular expressions.

The model's SQL is only a proposal. Before anything runs, it is parsed with
sqlglot and every table reference is checked. Matching table names with
regular expressions is easy to slip past (comments, unusual join keywords,
MySQL's executable comments); walking the parsed tree is not.

This is the inner fence. The outer fence is that the database connection is
read-only (see ``tenancy.connect``).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

# Statement types that change data or schema, wherever they appear in the tree.
FORBIDDEN_NODES = tuple(
    getattr(exp, name)
    for name in ("Insert", "Update", "Delete", "Merge", "Drop", "Create", "Alter", "TruncateTable", "Command", "Into")
    if hasattr(exp, name)
)

# Functions that sleep, read files or reach other servers.
FORBIDDEN_FUNCTIONS = {
    "sleep", "benchmark", "pg_sleep", "load_file", "pg_read_file", "readfile", "writefile",
    "load_extension", "dblink", "xp_cmdshell", "openrowset", "opendatasource",
}

SENSITIVE_COLUMN = re.compile(r"pass(word)?|pwd|secret|token|salary|hash", re.IGNORECASE)


@dataclass
class GuardResult:
    ok: bool
    reason: str = ""
    sql: str = ""            # the query to run: target dialect, with a row limit
    sql_unlimited: str = ""  # the same query without the row limit (used for full statistics)
    tables: list[str] = field(default_factory=list)


def _refuse(reason: str) -> GuardResult:
    return GuardResult(False, reason)


def _with_limit(stmt: exp.Expression, max_rows: int) -> exp.Expression:
    if isinstance(stmt, exp.Select):
        limit = stmt.args.get("limit")
        if limit is not None:
            try:
                if int(limit.expression.this) <= max_rows:
                    return stmt
            except (AttributeError, TypeError, ValueError):
                pass
        return stmt.limit(max_rows)
    # UNION / INTERSECT / EXCEPT: limit the combined result.
    return exp.select("*").from_(stmt.subquery("q")).limit(max_rows)


def check(
    sql: str,
    *,
    allowed_tables: set[str],
    table_columns: dict[str, set[str]] | None = None,
    max_rows: int = 10_000,
    dialect: str = "sqlite",
) -> GuardResult:
    raw = sql or ""
    if "/*!" in raw:
        return _refuse("MySQL executable comments are not allowed")
    try:
        statements = [s for s in sqlglot.parse(raw, read=dialect) if s is not None]
    except ParseError as e:
        return _refuse(f"the SQL could not be parsed: {str(e).splitlines()[0]}")
    if len(statements) != 1:
        return _refuse("exactly one statement is allowed")
    stmt = statements[0]

    if not isinstance(stmt, (exp.Select, exp.SetOperation)):
        return _refuse("only SELECT queries are allowed")
    for node in stmt.walk():
        if isinstance(node, FORBIDDEN_NODES):
            return _refuse(f"{type(node).__name__.upper()} is not allowed")
    for select in stmt.find_all(exp.Select):
        if select.args.get("into") is not None:
            return _refuse("SELECT ... INTO is not allowed")
    for func in stmt.find_all(exp.Func):
        name = (func.name if isinstance(func, exp.Anonymous) else func.sql_name()).lower()
        if name in FORBIDDEN_FUNCTIONS:
            return _refuse(f"function {name} is not allowed")

    allowed = {t.lower() for t in allowed_tables}
    cte_names = {cte.alias_or_name.lower() for cte in stmt.find_all(exp.CTE)}
    tables: list[str] = []
    for table in stmt.find_all(exp.Table):
        if table.args.get("db") is not None or table.args.get("catalog") is not None:
            return _refuse(f"cross-database reference {table.sql(dialect=dialect)} is not allowed")
        name = table.name.lower()
        if name in cte_names:
            continue
        if name not in allowed:
            return _refuse(f"table {table.name} is not available")
        tables.append(name)

    for column in stmt.find_all(exp.Column):
        if SENSITIVE_COLUMN.search(column.name or ""):
            return _refuse(f"column {column.name} is sensitive")
    if table_columns:
        for select in stmt.find_all(exp.Select):
            star = any(
                isinstance(e, exp.Star) or (isinstance(e, exp.Column) and isinstance(e.this, exp.Star))
                for e in select.expressions
            )
            if not star:
                continue
            for name in tables:
                if any(SENSITIVE_COLUMN.search(col) for col in table_columns.get(name, ())):
                    return _refuse(f"SELECT * would expose sensitive columns of {name}")

    unlimited = stmt.sql(dialect=dialect)
    limited = _with_limit(stmt.copy(), max_rows).sql(dialect=dialect)
    return GuardResult(True, sql=limited, sql_unlimited=unlimited, tables=sorted(set(tables)))
