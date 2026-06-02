"""Tests for the pure reconciliation diff engine."""

from __future__ import absolute_import, division, print_function
from __future__ import annotations

__metaclass__ = type

from ansible_collections.mykola_kharchenko.mssql_cdc.plugins.module_utils._engine.config import (
    ColumnDirective,
    Config,
    Table,
)
from ansible_collections.mykola_kharchenko.mssql_cdc.plugins.module_utils._engine.diff import (
    compute_diff,
)
from ansible_collections.mykola_kharchenko.mssql_cdc.plugins.module_utils._engine.state import (
    ActualState,
    CaptureInstance,
)


def _table(name, **kwargs):
    schema_name, table_name = name.split(".", 1)
    kwargs.setdefault("capture_instance", f"{schema_name}_{table_name}")
    kwargs.setdefault("supports_net_changes", True)
    return Table(schema_name=schema_name, table_name=table_name, **kwargs)


def _config(*tables, database="db"):
    return Config(
        version=1, database=database, host="h", tables={t.key: t for t in tables}
    )


def _instance(name, **kwargs):
    schema_name, table_name = name.split(".", 1)
    kwargs.setdefault("capture_instance", f"{schema_name}_{table_name}")
    kwargs.setdefault("supports_net_changes", True)
    for key in ("role_name", "index_name", "filegroup_name"):
        kwargs.setdefault(key, None)
    return CaptureInstance(schema_name=schema_name, table_name=table_name, **kwargs)


def _state(*instances, cdc_enabled=True):
    return ActualState(database="db", cdc_enabled=cdc_enabled, instances=list(instances))


def test_create_when_table_not_captured():
    plan = compute_diff(_config(_table("dbo.orders", columns=["id"])), _state())
    assert [a.source for a in plan.create] == ["dbo.orders"]
    assert not plan.recreate and not plan.drop and not plan.unchanged


def test_enable_db_when_not_enabled_and_tables_desired():
    plan = compute_diff(
        _config(_table("dbo.orders", columns=["id"])), _state(cdc_enabled=False)
    )
    assert plan.enable_db is True
    assert plan.has_changes is True


def test_drop_when_captured_but_not_desired():
    plan = compute_diff(_config(), _state(_instance("dbo.legacy", columns=["id"])))
    assert [a.source for a in plan.drop] == ["dbo.legacy"]


def test_unchanged_when_settings_match():
    desired = _config(_table("dbo.orders", columns=["id", "status"], role_name="r"))
    actual = _state(
        _instance(
            "dbo.orders",
            columns=["id", "status"],
            role_name="r",
            source_columns=["id", "status", "extra"],
        )
    )
    plan = compute_diff(desired, actual)
    assert [a.source for a in plan.unchanged] == ["dbo.orders"]
    assert not plan.recreate


def test_recreate_on_column_change_has_reason():
    desired = _config(_table("dbo.orders", columns=["id", "status", "updated_by"]))
    actual = _state(_instance("dbo.orders", columns=["id", "status"]))
    plan = compute_diff(desired, actual)
    assert len(plan.recreate) == 1
    assert any(
        "columns changed" in r and "updated_by" in r for r in plan.recreate[0].reasons
    )


def test_recreate_on_role_change():
    desired = _config(_table("dbo.orders", columns=["id"], role_name="new"))
    actual = _state(_instance("dbo.orders", columns=["id"], role_name="old"))
    plan = compute_diff(desired, actual)
    assert any("role_name changed" in r for r in plan.recreate[0].reasons)


def test_unspecified_index_name_does_not_drift():
    # CDC auto-populates index_name with the PK index even when not requested;
    # a desired None must accept that and not trigger a perpetual recreate.
    desired = _config(_table("dbo.orders", columns=["id"]))
    actual = _state(
        _instance("dbo.orders", columns=["id"], index_name="PK__orders__ABC123")
    )
    assert [a.source for a in compute_diff(desired, actual).unchanged] == ["dbo.orders"]


def test_explicit_index_name_mismatch_recreates():
    desired = _config(_table("dbo.orders", columns=["id"], index_name="ix_custom"))
    actual = _state(_instance("dbo.orders", columns=["id"], index_name="PK__orders"))
    plan = compute_diff(desired, actual)
    assert any("index_name changed" in r for r in plan.recreate[0].reasons)


def test_versioned_instance_is_unchanged_when_settings_match():
    # After a safe recreate the live instance is dbo_orders_v2; the config still
    # names the base dbo_orders. It must read as unchanged, not a perpetual rename.
    desired = _config(_table("dbo.orders", capture_instance="dbo_orders", columns=["id"]))
    actual = _state(
        _instance("dbo.orders", capture_instance="dbo_orders_v2", columns=["id"])
    )
    plan = compute_diff(desired, actual)
    assert [a.source for a in plan.unchanged] == ["dbo.orders"]


def test_columns_all_matches_full_source_set():
    desired = _config(_table("dbo.orders", columns=None))
    actual = _state(
        _instance("dbo.orders", columns=["id", "name"], source_columns=["id", "name"])
    )
    assert [a.source for a in compute_diff(desired, actual).unchanged] == ["dbo.orders"]


def _cc(*pairs):
    """Build a captured_columns directive list from (name, state) pairs."""
    return [ColumnDirective(name, state) for name, state in pairs]


def test_merge_present_adds_column_keeping_others():
    desired = _config(_table("dbo.orders", captured_columns=_cc(("email", "present"))))
    actual = _state(_instance("dbo.orders", columns=["id", "name"]))
    plan = compute_diff(desired, actual)
    assert len(plan.recreate) == 1
    assert any("added email" in r for r in plan.recreate[0].reasons)
    assert set(plan.recreate[0].table.columns) == {"id", "name", "email"}


def test_merge_absent_removes_only_named_column():
    desired = _config(_table("dbo.orders", captured_columns=_cc(("email", "absent"))))
    actual = _state(_instance("dbo.orders", columns=["id", "name", "email"]))
    plan = compute_diff(desired, actual)
    assert any("removed email" in r for r in plan.recreate[0].reasons)
    assert set(plan.recreate[0].table.columns) == {"id", "name"}


def test_merge_unmentioned_column_is_left_alone():
    # Only 'id' is named present; 'name' is unmentioned and must stay captured,
    # so the live state already matches and nothing changes.
    desired = _config(_table("dbo.orders", captured_columns=_cc(("id", "present"))))
    actual = _state(_instance("dbo.orders", columns=["id", "name"]))
    plan = compute_diff(desired, actual)
    assert [a.source for a in plan.unchanged] == ["dbo.orders"]
    assert not plan.recreate


def test_new_table_present_list_defines_capture_set():
    # Option ii: on a brand-new table, the present list IS the captured set.
    desired = _config(
        _table("dbo.orders", captured_columns=_cc(("id", "present"), ("name", "present")))
    )
    plan = compute_diff(desired, _state())
    assert plan.create[0].table.columns == ["id", "name"]


def test_new_table_absent_only_uses_all_source_columns_minus_absent():
    desired = _config(_table("dbo.orders", captured_columns=_cc(("secret", "absent"))))
    plan = compute_diff(
        desired, _state(), source_columns={"dbo.orders": ["id", "name", "secret"]}
    )
    assert plan.create[0].table.columns == ["id", "name"]


def test_index_name_ignored_when_net_changes_off():
    # index_name only supports net changes; with them off the live value is
    # meaningless and must not provoke a perpetual recreate.
    desired = _config(
        _table(
            "dbo.orders",
            columns=["id"],
            supports_net_changes=False,
            index_name="ix_custom",
        )
    )
    actual = _state(
        _instance(
            "dbo.orders",
            columns=["id"],
            supports_net_changes=False,
            index_name="PK__orders",
        )
    )
    assert [a.source for a in compute_diff(desired, actual).unchanged] == ["dbo.orders"]


def test_mixed_plan_counts():
    desired = _config(
        _table("dbo.add_me", columns=["id"]),
        _table("dbo.change_me", columns=["id", "new"]),
        _table("dbo.keep_me", columns=["id"]),
    )
    actual = _state(
        _instance("dbo.change_me", columns=["id"]),
        _instance("dbo.keep_me", columns=["id"], source_columns=["id"]),
        _instance("dbo.drop_me", columns=["id"]),
    )
    plan = compute_diff(desired, actual)
    assert plan.counts() == {"add": 1, "recreate": 1, "remove": 1, "unchanged": 1}
