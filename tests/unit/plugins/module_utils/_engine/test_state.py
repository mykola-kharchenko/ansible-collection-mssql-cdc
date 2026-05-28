"""Tests for the live-state parser and the dict-cursor helper."""

from __future__ import absolute_import, annotations, division, print_function

__metaclass__ = type

from ansible_collections.mykola_kharchenko.mssql_cdc.plugins.module_utils._engine import (
    db,
)
from ansible_collections.mykola_kharchenko.mssql_cdc.plugins.module_utils._engine.state import (
    build_state,
)


class _FakeCursor:
    def __init__(self, description, rows):
        self.description = description
        self._rows = rows
        self.executed = None

    def execute(self, sql, params):
        self.executed = (sql, params)

    def fetchall(self):
        return self._rows

    def close(self):
        pass


class _FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor


def test_run_query_returns_dicts():
    cursor = _FakeCursor(description=[("id",), ("name",)], rows=[(1, "a"), (2, "b")])
    rows = db.run_query(_FakeConn(cursor), "SELECT id, name FROM t WHERE x = ?", [5])
    assert rows == [{"id": 1, "name": "a"}, {"id": 2, "name": "b"}]
    assert cursor.executed == ("SELECT id, name FROM t WHERE x = ?", [5])


def test_run_query_handles_no_result_set():
    assert db.run_query(_FakeConn(_FakeCursor(None, [])), "EXEC something") == []


def test_build_state_parses_instances_and_columns():
    change_rows = [
        {
            "schema_name": "dbo",
            "table_name": "orders",
            "capture_instance": "dbo_orders",
            "supports_net_changes": 1,
            "role_name": "cdc_reader",
            "index_name": None,
            "filegroup_name": None,
        },
        {
            "schema_name": "dbo",
            "table_name": "products",
            "capture_instance": "dbo_products",
            "supports_net_changes": 0,
            "role_name": None,
            "index_name": None,
            "filegroup_name": None,
        },
    ]
    column_rows = [
        {"capture_instance": "dbo_orders", "column_name": "id", "column_ordinal": 1},
        {"capture_instance": "dbo_orders", "column_name": "status", "column_ordinal": 2},
        {"capture_instance": "dbo_products", "column_name": "id", "column_ordinal": 1},
        {"capture_instance": "dbo_products", "column_name": "name", "column_ordinal": 2},
    ]
    source_rows = [
        {"schema_name": "dbo", "table_name": "orders", "column_name": "id", "column_id": 1},
        {"schema_name": "dbo", "table_name": "orders", "column_name": "status", "column_id": 2},
        {"schema_name": "dbo", "table_name": "orders", "column_name": "secret", "column_id": 3},
        {"schema_name": "dbo", "table_name": "products", "column_name": "id", "column_id": 1},
        {"schema_name": "dbo", "table_name": "products", "column_name": "name", "column_id": 2},
    ]
    state = build_state(
        database="test_cdc",
        cdc_enabled=True,
        change_table_rows=change_rows,
        column_rows=column_rows,
        source_column_rows=source_rows,
    )
    assert state.cdc_enabled is True
    by_source = state.by_source()
    assert set(by_source) == {"dbo.orders", "dbo.products"}

    orders = by_source["dbo.orders"][0]
    assert orders.supports_net_changes is True
    assert orders.role_name == "cdc_reader"
    assert orders.columns == ["id", "status"]
    # orders captures 2 of 3 source columns -> not "all"
    assert orders.captures_all_columns is False

    products = by_source["dbo.products"][0]
    assert products.supports_net_changes is False
    assert products.captures_all_columns is True
