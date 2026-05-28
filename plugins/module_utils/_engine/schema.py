"""Read source-table structure for documentation rendering.

Modules call :func:`read_schema` to enrich the CDC facts with column types,
primary keys, identity columns, foreign keys and ``MS_Description`` comments.
The return shape is intentionally JSON-friendly so a Jinja2 template can render
it as DBML (or anything else) without further processing.

Pure :func:`build_schema` keeps the parsing logic testable; :func:`read_schema`
is a thin wrapper that issues the catalog queries and hands the rows in.
"""

from __future__ import absolute_import, division, print_function
from __future__ import annotations

__metaclass__ = type

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ansible_collections.mykola_kharchenko.mssql_cdc.plugins.module_utils._engine import db

if TYPE_CHECKING:
    import pyodbc


# User tables only — skip the system/CDC bookkeeping schemas.
_EXCLUDED = "'sys', 'cdc', 'INFORMATION_SCHEMA'"

_SQL_COLUMNS = f"""
SELECT c.TABLE_SCHEMA AS schema_name, c.TABLE_NAME AS table_name,
       c.COLUMN_NAME AS column_name, c.ORDINAL_POSITION AS ordinal,
       c.DATA_TYPE AS data_type, c.CHARACTER_MAXIMUM_LENGTH AS char_len,
       c.NUMERIC_PRECISION AS num_precision, c.NUMERIC_SCALE AS num_scale,
       c.IS_NULLABLE AS is_nullable
FROM INFORMATION_SCHEMA.COLUMNS c
JOIN INFORMATION_SCHEMA.TABLES t
  ON c.TABLE_SCHEMA = t.TABLE_SCHEMA AND c.TABLE_NAME = t.TABLE_NAME
WHERE t.TABLE_TYPE = 'BASE TABLE' AND c.TABLE_SCHEMA NOT IN ({_EXCLUDED})
ORDER BY c.TABLE_SCHEMA, c.TABLE_NAME, c.ORDINAL_POSITION;
"""

_SQL_PRIMARY_KEYS = """
SELECT s.name AS schema_name, t.name AS table_name, col.name AS column_name
FROM sys.indexes i
JOIN sys.index_columns ic ON i.object_id = ic.object_id AND i.index_id = ic.index_id
JOIN sys.columns col ON ic.object_id = col.object_id AND ic.column_id = col.column_id
JOIN sys.tables t ON i.object_id = t.object_id
JOIN sys.schemas s ON t.schema_id = s.schema_id
WHERE i.is_primary_key = 1;
"""

_SQL_IDENTITY = """
SELECT s.name AS schema_name, t.name AS table_name, c.name AS column_name
FROM sys.columns c
JOIN sys.tables t ON c.object_id = t.object_id
JOIN sys.schemas s ON t.schema_id = s.schema_id
WHERE c.is_identity = 1;
"""

_SQL_FOREIGN_KEYS = """
SELECT ps.name AS schema_name, pt.name AS table_name, pc.name AS column_name,
       rs.name AS ref_schema, rt.name AS ref_table, rc.name AS ref_column
FROM sys.foreign_key_columns fkc
JOIN sys.tables pt   ON fkc.parent_object_id = pt.object_id
JOIN sys.schemas ps  ON pt.schema_id = ps.schema_id
JOIN sys.columns pc  ON fkc.parent_object_id = pc.object_id
                    AND fkc.parent_column_id = pc.column_id
JOIN sys.tables rt   ON fkc.referenced_object_id = rt.object_id
JOIN sys.schemas rs  ON rt.schema_id = rs.schema_id
JOIN sys.columns rc  ON fkc.referenced_object_id = rc.object_id
                    AND fkc.referenced_column_id = rc.column_id;
"""

_SQL_TABLE_NOTES = """
SELECT s.name AS schema_name, t.name AS table_name,
       CAST(ep.value AS NVARCHAR(MAX)) AS note
FROM sys.extended_properties ep
JOIN sys.tables t ON ep.major_id = t.object_id
JOIN sys.schemas s ON t.schema_id = s.schema_id
WHERE ep.name = 'MS_Description' AND ep.minor_id = 0;
"""

_SQL_COLUMN_NOTES = """
SELECT s.name AS schema_name, t.name AS table_name, c.name AS column_name,
       CAST(ep.value AS NVARCHAR(MAX)) AS note
FROM sys.extended_properties ep
JOIN sys.tables t  ON ep.major_id = t.object_id
JOIN sys.columns c ON ep.major_id = c.object_id AND ep.minor_id = c.column_id
JOIN sys.schemas s ON t.schema_id = s.schema_id
WHERE ep.name = 'MS_Description' AND ep.minor_id > 0;
"""


@dataclass
class SchemaColumn:
    """One column of a source table, with its DBML-relevant attributes."""

    name: str
    data_type: str
    nullable: bool
    is_pk: bool = False
    is_identity: bool = False
    ref: str | None = None  # "schema.table.column" target for a foreign key
    note: str | None = None


@dataclass
class SchemaTable:
    """A source table's structure (columns + table-level note)."""

    schema_name: str
    table_name: str
    columns: list[SchemaColumn] = field(default_factory=list)
    note: str | None = None

    @property
    def full_name(self) -> str:
        return f"{self.schema_name}.{self.table_name}"


def map_mssql_type(
    data_type: str,
    char_len: int | None = None,
    num_precision: int | None = None,
    num_scale: int | None = None,
) -> str:
    """Map an ``INFORMATION_SCHEMA.COLUMNS`` row's type to a DBML type string.

    Lengths are appended for string/binary types (``-1`` becomes ``(max)``) and
    precision/scale for ``decimal``/``numeric``; everything else maps as-is.
    """
    base = data_type.lower()
    if base in {"varchar", "nvarchar", "char", "nchar", "binary", "varbinary"}:
        if char_len is None:
            return base
        return f"{base}(max)" if char_len == -1 else f"{base}({char_len})"
    if base in {"decimal", "numeric"} and num_precision is not None:
        if num_scale:
            return f"{base}({num_precision},{num_scale})"
        return f"{base}({num_precision})"
    return base


def _key(schema_name: str, table_name: str) -> str:
    return f"{schema_name}.{table_name}"


def build_schema(
    *,
    column_rows: list[dict[str, Any]],
    pk_rows: list[dict[str, Any]],
    identity_rows: list[dict[str, Any]],
    fk_rows: list[dict[str, Any]],
    table_note_rows: list[dict[str, Any]],
    column_note_rows: list[dict[str, Any]],
    only_tables: set[str] | None = None,
) -> dict[str, SchemaTable]:
    """Assemble :class:`SchemaTable` objects keyed by ``schema.table`` (pure function).

    ``only_tables`` (a set of ``schema.table`` keys) filters the output —
    pass the CDC-captured source set to skip the rest of the database.
    """

    def included(schema_name: str, table_name: str) -> bool:
        return only_tables is None or _key(schema_name, table_name) in only_tables

    pks = {(r["schema_name"], r["table_name"], r["column_name"]) for r in pk_rows}
    identities = {(r["schema_name"], r["table_name"], r["column_name"]) for r in identity_rows}
    refs = {
        (r["schema_name"], r["table_name"], r["column_name"]):
            f"{r['ref_schema']}.{r['ref_table']}.{r['ref_column']}"
        for r in fk_rows
    }
    col_notes = {
        (r["schema_name"], r["table_name"], r["column_name"]): r["note"]
        for r in column_note_rows
    }
    table_notes = {(r["schema_name"], r["table_name"]): r["note"] for r in table_note_rows}

    tables: dict[str, SchemaTable] = {}
    for row in column_rows:
        schema_name, table_name = row["schema_name"], row["table_name"]
        if not included(schema_name, table_name):
            continue
        key = _key(schema_name, table_name)
        table = tables.get(key)
        if table is None:
            table = SchemaTable(
                schema_name=schema_name,
                table_name=table_name,
                note=table_notes.get((schema_name, table_name)),
            )
            tables[key] = table
        ident = (schema_name, table_name, row["column_name"])
        table.columns.append(
            SchemaColumn(
                name=row["column_name"],
                data_type=map_mssql_type(
                    row["data_type"],
                    row.get("char_len"),
                    row.get("num_precision"),
                    row.get("num_scale"),
                ),
                nullable=str(row["is_nullable"]).upper() == "YES",
                is_pk=ident in pks,
                is_identity=ident in identities,
                ref=refs.get(ident),
                note=col_notes.get(ident),
            )
        )

    return tables


def read_schema(
    conn: pyodbc.Connection,
    only_tables: set[str] | None = None,
) -> dict[str, SchemaTable]:
    """Read source-table structure through ``conn``, optionally filtered."""
    return build_schema(
        column_rows=db.run_query(conn, _SQL_COLUMNS),
        pk_rows=db.run_query(conn, _SQL_PRIMARY_KEYS),
        identity_rows=db.run_query(conn, _SQL_IDENTITY),
        fk_rows=db.run_query(conn, _SQL_FOREIGN_KEYS),
        table_note_rows=db.run_query(conn, _SQL_TABLE_NOTES),
        column_note_rows=db.run_query(conn, _SQL_COLUMN_NOTES),
        only_tables=only_tables,
    )
