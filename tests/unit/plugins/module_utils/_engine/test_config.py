"""Tests for the typed config dataclasses and merge_defaults."""

from __future__ import absolute_import, division, print_function
from __future__ import annotations

__metaclass__ = type

import pytest

from ansible_collections.mykola_kharchenko.mssql_cdc.plugins.module_utils._engine.config import (
    ColumnDirective,
    Config,
    Defaults,
    Table,
    merge_defaults,
    normalize_captured_columns,
)
from ansible_collections.mykola_kharchenko.mssql_cdc.plugins.module_utils._engine.errors import (
    ConfigError,
)


def _table(name, **fields):
    schema_name, table_name = name.split(".", 1)
    table = Table(schema_name=schema_name, table_name=table_name, **fields)
    # Mark explicitly-supplied fields so merge_defaults knows what to inherit.
    table._explicit = frozenset(k for k, v in fields.items() if v is not None or k == "role_name")
    return table


def test_merge_defaults_fills_inherited_fields():
    config = Config(
        version=1,
        database="d",
        host="h",
        defaults=Defaults(role_name="cdc_reader", supports_net_changes=True),
        tables={
            "dbo.orders": _table(
                "dbo.orders",
                capture_instance="dbo_orders",
                columns=["id", "status"],
            ),
            "dbo.customers": _table("dbo.customers", role_name="cdc_pii"),
        },
    )
    merged = merge_defaults(config)
    orders = merged.tables["dbo.orders"]
    customers = merged.tables["dbo.customers"]

    # orders did not set role_name -> inherits the default
    assert orders.role_name == "cdc_reader"
    assert orders.supports_net_changes is True
    # customers overrode role_name explicitly
    assert customers.role_name == "cdc_pii"
    # missing capture_instance defaults to <schema>_<table>
    assert customers.capture_instance == "dbo_customers"
    # columns omitted -> capture all
    assert customers.columns is None


def test_normalize_none_passes_through():
    assert normalize_captured_columns(None) is None


def test_normalize_bare_strings_default_present():
    assert normalize_captured_columns(["Id", "Name"]) == [
        ColumnDirective("Id", "present"),
        ColumnDirective("Name", "present"),
    ]


def test_normalize_dicts_with_state():
    result = normalize_captured_columns(
        [{"name": "Id"}, {"name": "Name", "state": "absent"}]
    )
    assert result == [
        ColumnDirective("Id", "present"),
        ColumnDirective("Name", "absent"),
    ]


def test_normalize_mixed_strings_and_dicts():
    result = normalize_captured_columns(["Id", {"name": "Name", "state": "absent"}])
    assert result == [ColumnDirective("Id", "present"), ColumnDirective("Name", "absent")]


def test_normalize_rejects_bad_state():
    with pytest.raises(ConfigError):
        normalize_captured_columns([{"name": "Id", "state": "maybe"}])


def test_normalize_rejects_missing_name():
    with pytest.raises(ConfigError):
        normalize_captured_columns([{"state": "present"}])


def test_normalize_rejects_unknown_keys():
    with pytest.raises(ConfigError):
        normalize_captured_columns([{"name": "Id", "captured": True}])


def test_normalize_rejects_non_list():
    with pytest.raises(ConfigError):
        normalize_captured_columns("Id")


def test_explicit_null_role_overrides_default():
    table = _table("dbo.t", role_name=None)
    # The role_name=None kwarg becomes part of _explicit in the helper above.
    config = Config(
        version=1,
        database="d",
        host="h",
        defaults=Defaults(role_name="cdc_reader"),
        tables={"dbo.t": table},
    )
    merged = merge_defaults(config)
    # role_name: null in config means "no role", not "inherit default".
    assert merged.tables["dbo.t"].role_name is None
