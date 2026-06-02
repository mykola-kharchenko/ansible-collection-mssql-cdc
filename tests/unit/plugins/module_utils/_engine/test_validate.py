"""Tests for the pre-apply source-table validation."""

from __future__ import absolute_import, division, print_function
from __future__ import annotations

__metaclass__ = type

from ansible_collections.mykola_kharchenko.mssql_cdc.plugins.module_utils._engine import (
    db,
)
from ansible_collections.mykola_kharchenko.mssql_cdc.plugins.module_utils._engine.config import (
    ColumnDirective,
    Table,
)
from ansible_collections.mykola_kharchenko.mssql_cdc.plugins.module_utils._engine.validate import (
    IndexInfo,
    TableFacts,
    build_table_facts,
    read_table_facts,
    validate_table,
)


def _table(name, **kwargs):
    schema_name, table_name = name.split(".", 1)
    kwargs.setdefault("supports_net_changes", True)
    return Table(schema_name=schema_name, table_name=table_name, **kwargs)


def _facts(**kwargs):
    kwargs.setdefault("exists", True)
    kwargs.setdefault("columns", ["id", "name"])
    return TableFacts(**kwargs)


def _pk(name="PK_orders"):
    return IndexInfo(name=name, is_unique=True, is_primary_key=True)


# --- build_table_facts -----------------------------------------------------


def test_build_table_facts_empty_columns_means_not_exists():
    facts = build_table_facts(columns=[], memory_rows=[], index_rows=[])
    assert facts.exists is False


def test_build_table_facts_parses_flags_and_indexes():
    facts = build_table_facts(
        columns=["id", "name"],
        memory_rows=[{"is_memory_optimized": 1}],
        index_rows=[
            {"index_name": "PK_orders", "is_unique": 1, "is_primary_key": 1},
            {"index_name": "ix_name", "is_unique": 0, "is_primary_key": 0},
        ],
    )
    assert facts.exists is True
    assert facts.is_memory_optimized is True
    assert [(i.name, i.is_unique, i.is_primary_key) for i in facts.indexes] == [
        ("PK_orders", True, True),
        ("ix_name", False, False),
    ]


# --- validate_table --------------------------------------------------------


def test_missing_table_is_the_only_error():
    errors = validate_table(_table("dbo.orders"), _facts(exists=False, columns=[]))
    assert len(errors) == 1 and "not found" in errors[0]


def test_memory_optimized_rejected():
    errors = validate_table(
        _table("dbo.orders", index_name=None, supports_net_changes=False),
        _facts(is_memory_optimized=True),
    )
    assert any("memory-optimized" in e for e in errors)


def test_unknown_captured_column_rejected():
    table = _table(
        "dbo.orders",
        supports_net_changes=False,
        captured_columns=[ColumnDirective("id"), ColumnDirective("ghost")],
    )
    errors = validate_table(table, _facts(columns=["id", "name"]))
    assert any("ghost" in e and "not on the table" in e for e in errors)


def test_index_name_missing_rejected():
    table = _table("dbo.orders", index_name="ix_nope")
    errors = validate_table(table, _facts(indexes=[_pk()]))
    assert any("does not exist" in e for e in errors)


def test_index_name_not_unique_rejected():
    table = _table("dbo.orders", index_name="ix_name")
    facts = _facts(indexes=[IndexInfo("ix_name", is_unique=False, is_primary_key=False)])
    errors = validate_table(table, facts)
    assert any("not a unique index" in e for e in errors)


def test_unique_index_name_accepted():
    table = _table("dbo.orders", index_name="ux_email")
    facts = _facts(indexes=[IndexInfo("ux_email", is_unique=True, is_primary_key=False)])
    assert validate_table(table, facts) == []


def test_net_changes_without_pk_or_index_rejected():
    table = _table("dbo.orders", supports_net_changes=True, index_name=None)
    errors = validate_table(table, _facts(indexes=[]))
    assert any("supports_net_changes requires a primary key" in e for e in errors)


def test_net_changes_with_pk_accepted():
    table = _table("dbo.orders", supports_net_changes=True, index_name=None)
    assert validate_table(table, _facts(indexes=[_pk()])) == []


def test_net_changes_off_without_key_is_fine():
    table = _table("dbo.orders", supports_net_changes=False, index_name=None)
    assert validate_table(table, _facts(indexes=[])) == []


# --- read_table_facts ------------------------------------------------------


def test_read_table_facts_skips_dependent_reads_when_missing(monkeypatch):
    calls = []

    def fake_run_query(conn, sql, params=None):
        calls.append(sql)
        if "WHERE s.name = ? AND t.name = ?" in sql and "sys.columns" in sql:
            return []  # no columns -> table does not exist
        return []

    monkeypatch.setattr(db, "run_query", fake_run_query)
    facts = read_table_facts(None, "dbo", "ghost")
    assert facts.exists is False
    # Only the column read ran; no memory-optimized / index queries.
    assert all("is_memory_optimized" not in s and "sys.indexes" not in s for s in calls)


def test_read_table_facts_reads_indexes_and_memory(monkeypatch):
    def fake_run_query(conn, sql, params=None):
        if "sys.columns" in sql:
            return [{"column_name": "id"}, {"column_name": "name"}]
        if "is_memory_optimized" in sql:
            return [{"is_memory_optimized": 0}]
        if "sys.indexes" in sql:
            return [{"index_name": "PK_orders", "is_unique": 1, "is_primary_key": 1}]
        return []

    monkeypatch.setattr(db, "run_query", fake_run_query)
    facts = read_table_facts(None, "dbo", "orders")
    assert facts.exists is True
    assert facts.columns == ["id", "name"]
    assert facts.is_memory_optimized is False
    assert facts.indexes[0].is_primary_key is True
