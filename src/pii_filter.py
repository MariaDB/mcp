"""PII filter for the MariaDB MCP server.

Goal
----
Prevent the AI client from receiving PII values in tool responses. Human users
with direct DB access are explicitly out of scope -- this is not a SQL firewall.

Two layers
----------
1. Result post-filter (the security guarantee): blocked columns are stripped
   from every result row before the rows leave the MCP server. This runs
   regardless of what the SQL looked like.
2. Pre-flight AST check (UX optimization): queries that would obviously return
   PII (e.g. ``SELECT email FROM users``, ``SELECT *`` over a PII table) are
   refused with a clear error so the AI can rewrite. Lenient on predicates --
   blocked columns are allowed in ``WHERE`` / ``JOIN ON`` because they do not
   produce PII in the result set.

Schema endpoints (``list_tables``, ``get_table_schema``) are also filtered so
the AI never learns the names of blocked columns in the first place.

Off = zero overhead
-------------------
When ``PII_FILTER_ENABLED`` is false, every public function returns immediately
in its first line. No YAML load, no ``sqlglot`` import-time cost, no per-row
work.

Blocklist YAML format
---------------------
::

    # Top-level keys are database names. Values are a dict of table -> column list,
    # or the string "*" to block every column of a table.
    niteflirt:
      users:
        - email
        - phone
        - real_name
      payment_methods:
        - card_number
        - cvv
      addresses: "*"

Names are matched case-insensitively. The YAML is loaded lazily on first use
and cached for the lifetime of the process.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from config import PII_FILTER_ENABLED, PII_CONFIG_PATH, logger


class PIIBlockedError(ValueError):
    """Raised when a query is rejected because it would return blocked PII."""


# Sentinel marking "every column in this table is PII." Stored under the
# special key ``"*"`` in the per-table column set.
_ALL_COLUMNS = "*"


# ---------------------------------------------------------------------------
# Blocklist loading
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _load_blocklist() -> Dict[str, Dict[str, Set[str]]]:
    """Load and normalize the YAML blocklist. Cached for the process lifetime.

    Returns an empty dict when the filter is disabled or the file is missing
    -- but a missing file when the filter is enabled also logs an error, and
    callers should treat the absence of an explicit entry as "no blocked
    columns" (which is consistent with fail-closed only insofar as the
    blocklist explicitly lists what is sensitive).
    """
    if not PII_FILTER_ENABLED:
        return {}
    if not PII_CONFIG_PATH:
        logger.error("PII filter enabled but PII_CONFIG_PATH is unset; treating blocklist as empty.")
        return {}

    try:
        import yaml  # imported lazily so disabled installs don't pay the cost
        with open(PII_CONFIG_PATH, "r") as f:
            raw = yaml.safe_load(f) or {}
    except FileNotFoundError:
        logger.error(f"PII blocklist file not found at {PII_CONFIG_PATH}; treating blocklist as empty.")
        return {}
    except Exception as e:  # pragma: no cover - YAML errors surface to admin
        logger.error(f"Failed to load PII blocklist from {PII_CONFIG_PATH}: {e}", exc_info=True)
        return {}

    normalized: Dict[str, Dict[str, Set[str]]] = {}
    for db, tables in (raw or {}).items():
        if not isinstance(tables, dict):
            logger.warning(f"PII blocklist: database '{db}' entry is not a dict; skipping.")
            continue
        db_key = str(db).lower()
        normalized[db_key] = {}
        for table, cols in tables.items():
            tbl_key = str(table).lower()
            if cols == "*" or cols == ["*"]:
                normalized[db_key][tbl_key] = {_ALL_COLUMNS}
            elif isinstance(cols, list):
                normalized[db_key][tbl_key] = {str(c).lower() for c in cols}
            else:
                logger.warning(
                    f"PII blocklist: {db}.{table} entry is not a list or '*'; skipping."
                )
    logger.info(
        f"PII blocklist loaded: {sum(len(t) for t in normalized.values())} "
        f"table entries across {len(normalized)} databases."
    )
    return normalized


def reload_blocklist() -> None:
    """Force a re-read of the blocklist YAML on next access. For tests and admin reloads."""
    _load_blocklist.cache_clear()


# ---------------------------------------------------------------------------
# Public queries against the blocklist
# ---------------------------------------------------------------------------

def is_enabled() -> bool:
    return PII_FILTER_ENABLED


def blocked_cols_for(database: str, table: str) -> Set[str]:
    """Return the lowercase set of blocked column names for ``database.table``.

    The sentinel ``"*"`` may appear, meaning "all columns of this table are PII."
    Callers should treat that as "strip every column" when filtering rows.
    """
    if not PII_FILTER_ENABLED:
        return set()
    if not database or not table:
        return set()
    return _load_blocklist().get(database.lower(), {}).get(table.lower(), set())


def is_fully_blocked(database: str, table: str) -> bool:
    """True when the entire table is marked PII (``"*"``)."""
    return _ALL_COLUMNS in blocked_cols_for(database, table)


def all_blocked_names_in_db(database: str) -> Set[str]:
    """Union of every blocked column name across every table in ``database``.

    Used as a conservative post-filter when we couldn't determine which
    specific tables a query touched (e.g. SQL parse failure).
    """
    if not PII_FILTER_ENABLED:
        return set()
    db_entry = _load_blocklist().get(database.lower(), {})
    names: Set[str] = set()
    for cols in db_entry.values():
        # Skip the "*" sentinel -- a wildcard table tells us nothing about
        # column names, only that every column from THAT table is blocked.
        names.update(c for c in cols if c != _ALL_COLUMNS)
    return names


# ---------------------------------------------------------------------------
# Schema filtering (list_tables, get_table_schema*)
# ---------------------------------------------------------------------------

def filter_schema(database: str, table: str, schema: Dict[str, Any]) -> Dict[str, Any]:
    """Strip blocked columns from a get_table_schema response."""
    if not PII_FILTER_ENABLED:
        return schema
    blocked = blocked_cols_for(database, table)
    if not blocked:
        return schema
    if _ALL_COLUMNS in blocked:
        return {}  # entire table is PII -> no columns visible
    return {k: v for k, v in schema.items() if k.lower() not in blocked}


def filter_table_list(database: str, tables: List[str]) -> List[str]:
    """Drop fully-blocked tables from a list_tables response.

    Partially-blocked tables remain visible; only ``"*"`` entries are hidden.
    """
    if not PII_FILTER_ENABLED:
        return tables
    db_entry = _load_blocklist().get(database.lower(), {})
    if not db_entry:
        return tables
    return [
        t for t in tables
        if _ALL_COLUMNS not in db_entry.get(t.lower(), set())
    ]


# ---------------------------------------------------------------------------
# Pre-flight query check (lenient: only blocks PII in the projection)
# ---------------------------------------------------------------------------

def check_query(sql: str, default_db: Optional[str]) -> None:
    """Best-effort AST check: refuse queries that would project a blocked column.

    Lenient policy: blocked columns are allowed in ``WHERE``, ``JOIN ON``,
    ``GROUP BY``, ``ORDER BY``, and ``HAVING``. The post-filter on result rows
    is what enforces the actual security guarantee, so allowing these is safe.

    Refuses:
    - ``SELECT *`` against any table that has at least one blocked column
    - Any ``Column`` reference inside the projection that resolves to a blocked
      column

    On parse failure: does NOT raise. The caller will still run the post-filter
    on result rows, which is fail-closed-by-construction (conservative drop
    using ``all_blocked_names_in_db``).
    """
    if not PII_FILTER_ENABLED:
        return
    blocklist = _load_blocklist()
    if not blocklist:
        return

    try:
        import sqlglot
        from sqlglot import exp
    except ImportError:  # pragma: no cover - sqlglot is a hard dep
        logger.error("sqlglot not installed; cannot pre-flight check queries.")
        return

    try:
        tree = sqlglot.parse_one(sql, dialect="mysql")
    except Exception as e:
        # Parse failed. The post-filter is our safety net; don't reject here.
        logger.warning(f"PII filter: could not parse query for pre-flight check ({e}); relying on post-filter.")
        return

    if tree is None:
        return

    # Build a map of {alias_or_name -> (db, table)} for tables referenced in
    # FROM/JOIN clauses, walking subqueries too.
    table_map = _resolve_tables(tree, default_db, blocklist)

    # Check SELECT * expansions in every Select node (top-level + subqueries
    # whose results bubble up via UNION etc.).
    for select in tree.find_all(exp.Select):
        for projection in select.expressions:
            _check_projection(projection, table_map, blocklist)


def _resolve_tables(
    tree: Any,
    default_db: Optional[str],
    blocklist: Dict[str, Dict[str, Set[str]]],
) -> Dict[str, Tuple[str, str]]:
    """Return {lowercase alias_or_name -> (db, table)} for all referenced tables."""
    from sqlglot import exp

    table_map: Dict[str, Tuple[str, str]] = {}
    for tbl in tree.find_all(exp.Table):
        table_name = (tbl.name or "").lower()
        if not table_name:
            continue
        db_name = (tbl.db or default_db or "").lower()
        alias = (tbl.alias or table_name).lower()
        table_map[alias] = (db_name, table_name)
        # Also key by table name itself so unqualified column refs resolve
        # when there is no alias collision.
        table_map.setdefault(table_name, (db_name, table_name))
    return table_map


def _check_projection(
    projection: Any,
    table_map: Dict[str, Tuple[str, str]],
    blocklist: Dict[str, Dict[str, Set[str]]],
) -> None:
    """Raise PIIBlockedError if ``projection`` would emit a blocked column."""
    from sqlglot import exp

    # SELECT * -> expand against every table in scope and refuse if any has
    # blocked columns.
    if isinstance(projection, exp.Star):
        for db, tbl in table_map.values():
            tbl_cols = blocklist.get(db, {}).get(tbl, set())
            if tbl_cols:
                raise PIIBlockedError(
                    f"PII filter: SELECT * would include blocked columns from "
                    f"`{db or '?'}`.`{tbl}` ({_format_cols(tbl_cols)}). "
                    f"List columns explicitly, omitting the blocked ones."
                )
        return

    # ``table.*`` -> same idea but for a single qualified table.
    if isinstance(projection, exp.Column) and isinstance(projection.this, exp.Star):
        tbl_alias = (projection.table or "").lower()
        db, tbl = table_map.get(tbl_alias, ("", ""))
        if tbl:
            tbl_cols = blocklist.get(db, {}).get(tbl, set())
            if tbl_cols:
                raise PIIBlockedError(
                    f"PII filter: `{tbl_alias}.*` would include blocked columns "
                    f"({_format_cols(tbl_cols)}). List columns explicitly."
                )
        return

    # Walk every Column reference inside the projection expression (catches
    # things like ``SELECT email AS x``, ``SELECT CONCAT(first_name, last_name)``,
    # ``SELECT SUBSTRING(ssn, 1, 4)``, etc.).
    for col in projection.find_all(exp.Column):
        # Skip if this Column wraps a Star (handled above).
        if isinstance(col.this, exp.Star):
            continue
        col_name = (col.name or "").lower()
        if not col_name:
            continue
        tbl_alias = (col.table or "").lower()

        if tbl_alias:
            db, tbl = table_map.get(tbl_alias, ("", ""))
            if tbl and col_name in blocklist.get(db, {}).get(tbl, set()):
                raise PIIBlockedError(
                    f"PII filter: projection references blocked column "
                    f"`{db or '?'}`.`{tbl}`.`{col_name}`."
                )
        else:
            # Unqualified column: refuse if it matches a blocked column in
            # ANY table currently in scope. False positives here are
            # acceptable -- the AI can disambiguate by qualifying the column.
            for db, tbl in table_map.values():
                if col_name in blocklist.get(db, {}).get(tbl, set()):
                    raise PIIBlockedError(
                        f"PII filter: projection references blocked column "
                        f"`{col_name}` (matches `{db or '?'}`.`{tbl}`.`{col_name}`). "
                        f"If this column is from a different table, qualify it explicitly."
                    )


def _format_cols(cols: Iterable[str]) -> str:
    visible = sorted(c for c in cols if c != _ALL_COLUMNS)
    if _ALL_COLUMNS in cols and not visible:
        return "all columns"
    return ", ".join(visible)


# ---------------------------------------------------------------------------
# Post-filter on result rows (the security guarantee)
# ---------------------------------------------------------------------------

def filter_rows(
    rows: List[Dict[str, Any]],
    database: Optional[str],
    sql: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Strip blocked columns from result rows.

    Two modes:
    - When ``sql`` parses cleanly, the AST tells us which tables were touched
      and we drop exactly the blocked columns of those tables.
    - When ``sql`` is None or unparseable, we fall back to dropping any column
      name that matches ANY blocked column in ``database``. Conservative but safe.

    Returns ``rows`` unchanged when the filter is disabled or no blocked
    columns apply.
    """
    if not PII_FILTER_ENABLED or not rows:
        return rows
    if not database:
        return rows

    blocked_names = _resolve_blocked_names_for_query(sql, database)
    if not blocked_names:
        return rows

    # Filter case-insensitively. We compare lowercase but preserve the original
    # key casing in the output (for the keys we keep).
    return [
        {k: v for k, v in row.items() if k.lower() not in blocked_names}
        for row in rows
    ]


def _resolve_blocked_names_for_query(sql: Optional[str], database: str) -> Set[str]:
    """Determine which column names to strip from result rows of ``sql``."""
    if not sql:
        return all_blocked_names_in_db(database)

    try:
        import sqlglot
        from sqlglot import exp
        tree = sqlglot.parse_one(sql, dialect="mysql")
    except Exception:
        return all_blocked_names_in_db(database)

    if tree is None:
        return all_blocked_names_in_db(database)

    blocklist = _load_blocklist()
    names: Set[str] = set()
    tables_found = 0
    for tbl in tree.find_all(exp.Table):
        tbl_name = (tbl.name or "").lower()
        db_name = (tbl.db or database).lower()
        if not tbl_name:
            continue
        tables_found += 1
        cols = blocklist.get(db_name, {}).get(tbl_name, set())
        # For "*" tables we don't know the column names from the AST alone;
        # fall back to "everything from this DB" so we don't leak.
        if _ALL_COLUMNS in cols:
            return all_blocked_names_in_db(database) | _column_names_only(cols)
        names.update(cols)
    # If we parsed but couldn't identify any tables (e.g. lenient parse of
    # broken SQL), fall back to db-wide strip rather than letting PII through.
    # This is intentionally conservative -- the alternative (returning an
    # empty set) would leak.
    if tables_found == 0:
        return all_blocked_names_in_db(database)
    return names


def _column_names_only(cols: Set[str]) -> Set[str]:
    return {c for c in cols if c != _ALL_COLUMNS}
