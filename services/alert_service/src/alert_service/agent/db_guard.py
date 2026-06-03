"""SQL safety guard for agent tools.

Enforces read-only access at the code level:
- Allow-list of readable tables
- SELECT-only validator (rejects DDL/DML)
"""

from __future__ import annotations

import re

ALLOWED_TABLES: frozenset[str] = frozenset(
    {
        "reference.financial_metrics",
        "reference.bronze_consensus_report",
        "reference.bronze_market_ticker",
        "reference.bronze_stock_overview",
        "serving.symbol_snapshot",
        "serving.symbol_intraday_5m",
        "serving.symbol_signal_timeline",
    }
)

# Keywords that indicate write/DDL operations
_FORBIDDEN_KEYWORDS = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|GRANT|REVOKE|COPY|MERGE|CALL|DO)\b",
    re.IGNORECASE,
)

# Strip leading whitespace and SQL comments (-- line comments and /* block comments */)
_STRIP_LEADING = re.compile(
    r"^(\s*(--[^\n]*\n\s*|/\*.*?\*/\s*))*",
    re.DOTALL,
)

# Schema-qualified table references in known schemas
_SCHEMA_TABLE_RE = re.compile(
    r"\b(reference|serving|bronze|silver|gold|alert_service|agent)\.\w+",
    re.IGNORECASE,
)

# FROM/JOIN context to find table references
_FROM_JOIN_RE = re.compile(
    r"\b(?:FROM|JOIN)\s+([^\s,;()]+)",
    re.IGNORECASE,
)


class SqlGuardError(Exception):
    """Raised when SQL fails the safety guard checks."""


def assert_select_only(sql: str) -> None:
    """Raise SqlGuardError if sql is not a single read-only SELECT (or CTE SELECT).

    Rules:
    - After stripping leading whitespace/comments, the first keyword must be SELECT or WITH.
    - No forbidden DML/DDL keywords anywhere in the statement.
    - No stacked queries (semicolon followed by another statement).
    """
    stripped = _STRIP_LEADING.sub("", sql).strip()

    if not stripped:
        raise SqlGuardError("Empty SQL statement")

    # Check first keyword
    first_keyword_match = re.match(r"^(\w+)", stripped, re.IGNORECASE)
    if not first_keyword_match:
        raise SqlGuardError(f"Cannot determine SQL statement type: {sql!r}")

    first_keyword = first_keyword_match.group(1).upper()
    if first_keyword not in ("SELECT", "WITH"):
        raise SqlGuardError(
            f"Only SELECT statements are allowed; got leading keyword: {first_keyword!r}"
        )

    # Check for forbidden keywords anywhere in the statement
    forbidden_match = _FORBIDDEN_KEYWORDS.search(sql)
    if forbidden_match:
        raise SqlGuardError(
            f"Forbidden SQL keyword detected: {forbidden_match.group(0)!r}"
        )

    # Check for stacked queries: semicolon followed by non-whitespace content
    # Allow trailing semicolon but not "SELECT 1; DROP TABLE x"
    semicolon_pos = sql.find(";")
    if semicolon_pos != -1:
        after_semicolon = sql[semicolon_pos + 1 :].strip()
        if after_semicolon:
            raise SqlGuardError(
                "Stacked queries are not allowed (semicolon followed by another statement)"
            )


def assert_tables_allowed(sql: str) -> None:
    """Raise SqlGuardError if any schema-qualified table reference is not in ALLOWED_TABLES.

    Extracts schema.table references from FROM/JOIN clauses and checks against ALLOWED_TABLES.
    CTE alias names (defined in WITH clauses) are not checked.
    """
    # Find CTE names to exclude them from table checks
    cte_names: set[str] = set()
    cte_pattern = re.compile(r"\bWITH\b(.*?)\bSELECT\b", re.IGNORECASE | re.DOTALL)
    cte_match = cte_pattern.search(sql)
    if cte_match:
        cte_defs = cte_match.group(1)
        # CTE names are identifiers before AS (
        for cte_name in re.findall(r"\b(\w+)\s+AS\s*\(", cte_defs, re.IGNORECASE):
            cte_names.add(cte_name.lower())

    # Find all schema-qualified references in FROM/JOIN context
    for from_join_match in _FROM_JOIN_RE.finditer(sql):
        table_ref = from_join_match.group(1).strip().rstrip(",;)")
        # Check if it's schema-qualified with a known schema
        schema_table_match = _SCHEMA_TABLE_RE.match(table_ref)
        if schema_table_match:
            # Normalize to lowercase for comparison
            normalized = table_ref.lower()
            # Strip any alias or extra chars
            normalized = re.match(r"[\w.]+", normalized)
            if normalized:
                normalized = normalized.group(0)
                if normalized not in ALLOWED_TABLES:
                    raise SqlGuardError(
                        f"Table {normalized!r} is not in the allowed tables list"
                    )

    # Also scan entire SQL for schema-qualified references (catches subqueries etc.)
    for match in _SCHEMA_TABLE_RE.finditer(sql):
        ref = match.group(0).lower()
        # Skip if it's a CTE alias (won't be schema-qualified anyway, but be safe)
        table_part = ref.split(".")[-1]
        if table_part in cte_names:
            continue
        if ref not in ALLOWED_TABLES:
            raise SqlGuardError(
                f"Table {ref!r} is not in the allowed tables list"
            )


def guard(sql: str) -> str:
    """Run both assert_select_only and assert_tables_allowed; return sql unchanged.

    Usage:
        session.execute(text(guard(SQL)), params)
    """
    assert_select_only(sql)
    assert_tables_allowed(sql)
    return sql
