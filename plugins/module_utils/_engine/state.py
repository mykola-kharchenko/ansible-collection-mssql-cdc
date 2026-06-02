"""Read the live CDC state of a database into :class:`ActualState`.

The structure mirrors the desired :class:`Config` so the diff engine can
compare like with like. Reading is split into a pure :func:`build_state` (which
unit tests drive with plain row dicts) and a thin :func:`read_state` that
issues the catalog queries and feeds the rows in.
"""

from __future__ import absolute_import, division, print_function
from __future__ import annotations

__metaclass__ = type

from collections import defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ansible_collections.mykola_kharchenko.mssql_cdc.plugins.module_utils._engine import db

if TYPE_CHECKING:
    import pyodbc

# Is server-level CDC marked enabled for this database?
_SQL_DB_ENABLED = "SELECT is_cdc_enabled FROM sys.databases WHERE name = ?;"

# One row per capture instance. cdc.change_tables carries role_name, index_name
# and filegroup_name directly, so we read those columns rather than resolving ids.
_SQL_CHANGE_TABLES = """
SELECT
    s.name              AS schema_name,
    t.name              AS table_name,
    ct.capture_instance AS capture_instance,
    ct.supports_net_changes AS supports_net_changes,
    ct.role_name        AS role_name,
    ct.index_name       AS index_name,
    ct.filegroup_name   AS filegroup_name
FROM cdc.change_tables ct
JOIN sys.tables t  ON ct.source_object_id = t.object_id
JOIN sys.schemas s ON t.schema_id = s.schema_id;
"""

# Captured columns per capture instance, in capture order. cdc.captured_columns
# keys on the change table's object_id, so we join cdc.change_tables to recover
# the capture instance name.
_SQL_CAPTURED_COLUMNS = """
SELECT ct.capture_instance AS capture_instance,
       cc.column_name       AS column_name,
       cc.column_ordinal    AS column_ordinal
FROM cdc.captured_columns cc
JOIN cdc.change_tables ct ON cc.object_id = ct.object_id
ORDER BY ct.capture_instance, cc.column_ordinal;
"""

# Every column of every source table that is under CDC, in table order. Used to
# decide whether a capture instance captures *all* columns (so pull can omit the
# explicit list and diff can compare a "capture all" desired state correctly).
_SQL_SOURCE_COLUMNS = """
SELECT s.name AS schema_name, t.name AS table_name, c.name AS column_name, c.column_id
FROM cdc.change_tables ct
JOIN sys.tables t   ON ct.source_object_id = t.object_id
JOIN sys.schemas s  ON t.schema_id = s.schema_id
JOIN sys.columns c  ON c.object_id = t.object_id
ORDER BY s.name, t.name, c.column_id;
"""

# Columns of one source table in table order, regardless of CDC status. Unlike
# _SQL_SOURCE_COLUMNS this does not join cdc.change_tables, so it works for a
# table that is not captured yet (used to resolve the new-table merge baseline
# and to validate that named captured_columns exist).
_SQL_TABLE_COLUMNS = """
SELECT c.name AS column_name
FROM sys.columns c
JOIN sys.tables t  ON c.object_id = t.object_id
JOIN sys.schemas s ON t.schema_id = s.schema_id
WHERE s.name = ? AND t.name = ?
ORDER BY c.column_id;
"""


@dataclass
class CaptureInstance:
    """A single live capture instance on a source table."""

    schema_name: str
    table_name: str
    capture_instance: str
    supports_net_changes: bool
    role_name: str | None
    index_name: str | None
    filegroup_name: str | None
    columns: list[str] = field(default_factory=list)
    source_columns: list[str] = field(default_factory=list)

    @property
    def source(self) -> str:
        """The ``schema.table`` of the underlying source table."""
        return f"{self.schema_name}.{self.table_name}"

    @property
    def captures_all_columns(self) -> bool:
        """True when the captured set equals the source table's full column set."""
        return bool(self.source_columns) and set(self.columns) == set(self.source_columns)


@dataclass
class ActualState:
    """The live CDC state of one database."""

    database: str
    cdc_enabled: bool
    instances: list[CaptureInstance] = field(default_factory=list)

    def by_source(self) -> dict[str, list[CaptureInstance]]:
        """Group capture instances by their source ``schema.table`` (1-2 each)."""
        grouped: dict[str, list[CaptureInstance]] = defaultdict(list)
        for instance in self.instances:
            grouped[instance.source].append(instance)
        return dict(grouped)


def build_state(
    *,
    database: str,
    cdc_enabled: bool,
    change_table_rows: list[dict[str, Any]],
    column_rows: list[dict[str, Any]],
    source_column_rows: list[dict[str, Any]],
) -> ActualState:
    """Assemble an :class:`ActualState` from raw catalog rows (pure function)."""
    captured: dict[str, list[str]] = defaultdict(list)
    for row in column_rows:
        captured[row["capture_instance"]].append(row["column_name"])

    source_cols: dict[str, list[str]] = defaultdict(list)
    for row in source_column_rows:
        source_cols[f"{row['schema_name']}.{row['table_name']}"].append(row["column_name"])

    instances: list[CaptureInstance] = []
    for row in change_table_rows:
        instance = CaptureInstance(
            schema_name=row["schema_name"],
            table_name=row["table_name"],
            capture_instance=row["capture_instance"],
            supports_net_changes=bool(row["supports_net_changes"]),
            role_name=row["role_name"],
            index_name=row["index_name"],
            filegroup_name=row["filegroup_name"],
            columns=captured.get(row["capture_instance"], []),
            source_columns=source_cols.get(f"{row['schema_name']}.{row['table_name']}", []),
        )
        instances.append(instance)

    return ActualState(database=database, cdc_enabled=cdc_enabled, instances=instances)


def read_state(conn: pyodbc.Connection, database: str) -> ActualState:
    """Read the live CDC state of ``database`` through ``conn``."""
    enabled_rows = db.run_query(conn, _SQL_DB_ENABLED, [database])
    cdc_enabled = bool(enabled_rows and enabled_rows[0]["is_cdc_enabled"])

    if not cdc_enabled:
        # No cdc.* views exist until the database is CDC-enabled.
        return ActualState(database=database, cdc_enabled=False, instances=[])

    return build_state(
        database=database,
        cdc_enabled=True,
        change_table_rows=db.run_query(conn, _SQL_CHANGE_TABLES),
        column_rows=db.run_query(conn, _SQL_CAPTURED_COLUMNS),
        source_column_rows=db.run_query(conn, _SQL_SOURCE_COLUMNS),
    )


def read_source_columns(
    conn: pyodbc.Connection, tables: list[tuple[str, str]]
) -> dict[str, list[str]]:
    """Return ``{"schema.table": [column, ...]}`` for each ``(schema, name)``.

    Reads ``sys.columns`` directly so it works whether or not the table is under
    CDC. A table that does not exist (or is a view, not a base table) maps to an
    empty list, which callers treat as "not a usable source table".
    """
    result: dict[str, list[str]] = {}
    for schema, name in tables:
        rows = db.run_query(conn, _SQL_TABLE_COLUMNS, [schema, name])
        result[f"{schema}.{name}"] = [row["column_name"] for row in rows]
    return result
