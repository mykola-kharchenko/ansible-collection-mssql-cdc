"""Pre-apply validation of a source table against CDC's requirements.

``sp_cdc_enable_table`` fails with terse errors when a table cannot be captured
— it is memory-optimized, it has no key to support net changes, the named
``index_name`` is missing or not unique, or a requested column does not exist.
This module reads the handful of catalog facts those rules depend on and turns a
violation into an actionable message *before* anything mutates.

Structured like :mod:`...state`: a pure :func:`build_table_facts` /
:func:`validate_table` pair (unit-tested with plain dicts) plus a thin
:func:`read_table_facts` that issues the catalog queries.
"""

from __future__ import absolute_import, division, print_function
from __future__ import annotations

__metaclass__ = type

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ansible_collections.mykola_kharchenko.mssql_cdc.plugins.module_utils._engine import db
from ansible_collections.mykola_kharchenko.mssql_cdc.plugins.module_utils._engine.state import (
    read_source_columns,
)

if TYPE_CHECKING:
    import pyodbc

    from .config import Table

# is_memory_optimized exists since SQL Server 2014; the read is wrapped in
# try/except so an older server simply reads as "not memory-optimized".
_SQL_IS_MEMORY_OPTIMIZED = """
SELECT t.is_memory_optimized AS is_memory_optimized
FROM sys.tables t
JOIN sys.schemas s ON t.schema_id = s.schema_id
WHERE s.name = ? AND t.name = ?;
"""

# One row per named index on the table (heaps have a NULL name and are skipped).
_SQL_INDEXES = """
SELECT i.name           AS index_name,
       i.is_unique      AS is_unique,
       i.is_primary_key AS is_primary_key
FROM sys.indexes i
JOIN sys.tables t  ON i.object_id = t.object_id
JOIN sys.schemas s ON t.schema_id = s.schema_id
WHERE s.name = ? AND t.name = ? AND i.name IS NOT NULL;
"""


@dataclass
class IndexInfo:
    """A named index on the source table (only the bits CDC cares about)."""

    name: str
    is_unique: bool
    is_primary_key: bool


@dataclass
class TableFacts:
    """The catalog facts CDC's enable rules depend on, for one source table."""

    exists: bool
    is_memory_optimized: bool = False
    columns: list[str] = field(default_factory=list)
    indexes: list[IndexInfo] = field(default_factory=list)


def build_table_facts(
    *,
    columns: list[str],
    memory_rows: list[dict[str, Any]],
    index_rows: list[dict[str, Any]],
) -> TableFacts:
    """Assemble :class:`TableFacts` from raw catalog rows (pure function).

    A base table always has at least one column, so an empty ``columns`` means
    the table does not exist (or is not a base table, e.g. a view).
    """
    return TableFacts(
        exists=bool(columns),
        is_memory_optimized=bool(memory_rows and memory_rows[0]["is_memory_optimized"]),
        columns=list(columns),
        indexes=[
            IndexInfo(
                name=row["index_name"],
                is_unique=bool(row["is_unique"]),
                is_primary_key=bool(row["is_primary_key"]),
            )
            for row in index_rows
        ],
    )


def read_table_facts(conn: pyodbc.Connection, schema: str, name: str) -> TableFacts:
    """Read the :class:`TableFacts` for ``schema.name`` through ``conn``."""
    columns = read_source_columns(conn, [(schema, name)])[f"{schema}.{name}"]
    if not columns:
        # Not a usable base table — skip the dependent reads.
        return build_table_facts(columns=[], memory_rows=[], index_rows=[])

    try:
        memory_rows = db.run_query(conn, _SQL_IS_MEMORY_OPTIMIZED, [schema, name])
    except Exception:
        # Older servers lack is_memory_optimized; treat as not memory-optimized.
        memory_rows = []
    index_rows = db.run_query(conn, _SQL_INDEXES, [schema, name])
    return build_table_facts(columns=columns, memory_rows=memory_rows, index_rows=index_rows)


def validate_table(table: Table, facts: TableFacts) -> list[str]:
    """Return the reasons ``table`` cannot be CDC-enabled given ``facts`` (empty if ok).

    Surfaces, as readable messages, the conditions ``sp_cdc_enable_table`` would
    otherwise reject deep in the proc: a missing/non-base table, a
    memory-optimized table, captured columns that do not exist, and the net-change
    key requirement (a primary key, or an explicit unique ``index_name``).
    """
    key = table.key
    if not facts.exists:
        return [f"source table {key} not found (or is not a base table)"]

    errors: list[str] = []
    if facts.is_memory_optimized:
        errors.append(f"{key}: CDC is not supported on memory-optimized tables")

    named = {d.name for d in (table.captured_columns or [])}
    unknown = sorted(named - set(facts.columns))
    if unknown:
        errors.append(
            f"{key}: captured_columns reference columns not on the table: "
            + ", ".join(unknown)
        )

    has_pk = any(idx.is_primary_key for idx in facts.indexes)
    if table.index_name is not None:
        idx = next((i for i in facts.indexes if i.name == table.index_name), None)
        if idx is None:
            errors.append(f"{key}: index_name {table.index_name!r} does not exist on the table")
        elif not idx.is_unique:
            errors.append(
                f"{key}: index_name {table.index_name!r} is not a unique index "
                "(CDC requires a unique index)"
            )
    elif table.supports_net_changes and not has_pk:
        errors.append(
            f"{key}: supports_net_changes requires a primary key or an explicit "
            "unique index_name"
        )

    return errors
