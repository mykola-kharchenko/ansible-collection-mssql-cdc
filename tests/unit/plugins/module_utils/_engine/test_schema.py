"""Tests for the source-schema reader (column types, PKs, FKs, comments)."""

from __future__ import absolute_import, division, print_function
from __future__ import annotations

__metaclass__ = type

from ansible_collections.mykola_kharchenko.mssql_cdc.plugins.module_utils._engine.schema import (
    build_schema,
    map_mssql_type,
)


def test_map_mssql_type_lengths_and_precision():
    assert map_mssql_type("int") == "int"
    assert map_mssql_type("varchar", 50) == "varchar(50)"
    assert map_mssql_type("nvarchar", -1) == "nvarchar(max)"
    assert map_mssql_type("decimal", None, 10, 2) == "decimal(10,2)"
    assert map_mssql_type("decimal", None, 10, 0) == "decimal(10)"
    assert map_mssql_type("datetime2") == "datetime2"


def _columns():
    return [
        {
            "schema_name": "dbo", "table_name": "orders", "column_name": "id",
            "ordinal": 1, "data_type": "int", "char_len": None,
            "num_precision": 10, "num_scale": 0, "is_nullable": "NO",
        },
        {
            "schema_name": "dbo", "table_name": "orders", "column_name": "customer_id",
            "ordinal": 2, "data_type": "int", "char_len": None,
            "num_precision": 10, "num_scale": 0, "is_nullable": "NO",
        },
        {
            "schema_name": "dbo", "table_name": "orders", "column_name": "status",
            "ordinal": 3, "data_type": "varchar", "char_len": 50,
            "num_precision": None, "num_scale": None, "is_nullable": "YES",
        },
        {
            "schema_name": "dbo", "table_name": "customers", "column_name": "id",
            "ordinal": 1, "data_type": "int", "char_len": None,
            "num_precision": 10, "num_scale": 0, "is_nullable": "NO",
        },
    ]


def _built(**overrides):
    kwargs = dict(
        column_rows=_columns(),
        pk_rows=[
            {"schema_name": "dbo", "table_name": "orders", "column_name": "id"},
            {"schema_name": "dbo", "table_name": "customers", "column_name": "id"},
        ],
        identity_rows=[{"schema_name": "dbo", "table_name": "orders", "column_name": "id"}],
        fk_rows=[
            {
                "schema_name": "dbo", "table_name": "orders", "column_name": "customer_id",
                "ref_schema": "dbo", "ref_table": "customers", "ref_column": "id",
            }
        ],
        table_note_rows=[
            {"schema_name": "dbo", "table_name": "orders", "note": "Customer orders"}
        ],
        column_note_rows=[],
    )
    kwargs.update(overrides)
    return build_schema(**kwargs)


def test_build_schema_attaches_pk_identity_fk_note():
    tables = _built()
    orders = tables["dbo.orders"]
    assert orders.note == "Customer orders"
    by_name = {c.name: c for c in orders.columns}
    assert by_name["id"].is_pk and by_name["id"].is_identity
    assert by_name["status"].data_type == "varchar(50)"
    assert by_name["status"].nullable is True
    assert by_name["customer_id"].ref == "dbo.customers.id"


def test_only_tables_filter():
    tables = _built(only_tables={"dbo.orders"})
    assert list(tables) == ["dbo.orders"]
