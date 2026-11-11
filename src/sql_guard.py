"""Validates SQL written by the LLM before it reaches the database.

This is the first line of defence; the second is the database login itself, which can only
SELECT from llm_views.Active_FLEET (see scripts/security.sql).
"""
import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

ALLOWED_TABLES = {("llm_views", "active_fleet")}

_WRITE_NODES = (
    exp.Into, exp.Insert, exp.Update, exp.Delete, exp.Merge,
    exp.Create, exp.Drop, exp.Alter, exp.Command, exp.Execute,
)


class UnsafeQueryError(ValueError):
    """The query is not a single read-only SELECT against the allowed view."""


def validate_select(sql: str) -> str:
    """Returns the normalised T-SQL to execute, or raises UnsafeQueryError.

    The returned string is regenerated from the parsed tree, so what runs is exactly what was checked.
    """
    if not sql or not sql.strip():
        raise UnsafeQueryError("Empty query.")

    try:
        statements = [s for s in sqlglot.parse(sql, read="tsql") if s is not None]
    except SqlglotError as e:
        raise UnsafeQueryError(f"Could not parse the query as T-SQL: {e}") from None

    if len(statements) != 1:
        raise UnsafeQueryError("Only one statement is allowed per query.")
    stmt = statements[0]

    if not isinstance(stmt, exp.Query):
        raise UnsafeQueryError("Only SELECT queries are allowed.")

    for node in stmt.walk():
        if isinstance(node, _WRITE_NODES):
            raise UnsafeQueryError(f"'{node.key.upper()}' is not allowed; queries must be read-only.")

    cte_names = {cte.alias_or_name.lower() for cte in stmt.find_all(exp.CTE)}
    for table in stmt.find_all(exp.Table):
        schema, name = table.db.lower(), table.name.lower()
        if not schema and name in cte_names:
            continue
        if table.catalog or (schema, name) not in ALLOWED_TABLES:
            raise UnsafeQueryError(
                f"Table '{table.sql(dialect='tsql')}' is not allowed. Query llm_views.Active_FLEET only."
            )

    # Drop comments: T-SQL nests /* */ comments and sqlglot doesn't, so comment text is the one
    # place the two parsers could disagree.
    return stmt.sql(dialect="tsql", comments=False)
