"""Tests for the mutating apply path, driven by a fake connection/db layer."""

from __future__ import absolute_import, division, print_function
from __future__ import annotations

__metaclass__ = type

import pytest

from ansible_collections.mykola_kharchenko.mssql_cdc.plugins.module_utils._engine import (
    apply as apply_mod,
)
from ansible_collections.mykola_kharchenko.mssql_cdc.plugins.module_utils._engine import db
from ansible_collections.mykola_kharchenko.mssql_cdc.plugins.module_utils._engine.apply import (
    apply_plan,
)
from ansible_collections.mykola_kharchenko.mssql_cdc.plugins.module_utils._engine.config import (
    Table,
)
from ansible_collections.mykola_kharchenko.mssql_cdc.plugins.module_utils._engine.diff import (
    CreateAction,
    DropAction,
    Plan,
    RecreateAction,
)
from ansible_collections.mykola_kharchenko.mssql_cdc.plugins.module_utils._engine.errors import (
    DatabaseError,
)
from ansible_collections.mykola_kharchenko.mssql_cdc.plugins.module_utils._engine.state import (
    CaptureInstance,
)


class _FakeConn:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class _FakeDB:
    """Stands in for db.run_query / db.exec_proc, tracking simulated CDC state."""

    def __init__(self, *, cdc_enabled=True, lock_result=0):
        self.cdc_enabled = cdc_enabled
        self.lock_result = lock_result
        self.instances = set()
        self.source_instances = {}
        self.exec_calls = []
        self.fail_on_capture = None

    def run_query(self, conn, sql, params=None):
        params = params or []
        if "sp_getapplock" in sql:
            return [{"result": self.lock_result}]
        if "sp_releaseapplock" in sql:
            return []
        if "is_cdc_enabled" in sql:
            return [{"is_cdc_enabled": 1 if self.cdc_enabled else 0}]
        if "WHERE capture_instance" in sql:
            return [{"hit": 1}] if params[0] in self.instances else []
        if "WHERE s.name = ? AND t.name = ?" in sql:
            names = self.source_instances.get((params[0], params[1]), [])
            return [{"capture_instance": n} for n in names]
        return []

    def exec_proc(self, conn, proc, **params):
        self.exec_calls.append((proc, params))
        if proc == "sys.sp_cdc_enable_db":
            self.cdc_enabled = True
        elif proc == "sys.sp_cdc_enable_table":
            capture = params["capture_instance"]
            if capture == self.fail_on_capture:
                raise RuntimeError(f"boom enabling {capture}")
            self.instances.add(capture)
            key = (params["source_schema"], params["source_name"])
            self.source_instances.setdefault(key, []).append(capture)
        elif proc == "sys.sp_cdc_disable_table":
            capture = params["capture_instance"]
            self.instances.discard(capture)
            key = (params["source_schema"], params["source_name"])
            if capture in self.source_instances.get(key, []):
                self.source_instances[key].remove(capture)
        return []


@pytest.fixture
def fake_db(monkeypatch):
    fake = _FakeDB()
    monkeypatch.setattr(db, "run_query", fake.run_query)
    monkeypatch.setattr(db, "exec_proc", fake.exec_proc)
    assert apply_mod.db is db
    return fake


def _table(name, **kwargs):
    schema_name, table_name = name.split(".", 1)
    kwargs.setdefault("capture_instance", f"{schema_name}_{table_name}")
    kwargs.setdefault("supports_net_changes", True)
    return Table(schema_name=schema_name, table_name=table_name, **kwargs)


def _instance(name, **kwargs):
    schema_name, table_name = name.split(".", 1)
    kwargs.setdefault("capture_instance", f"{schema_name}_{table_name}")
    kwargs.setdefault("supports_net_changes", True)
    for key in ("role_name", "index_name", "filegroup_name"):
        kwargs.setdefault(key, None)
    return CaptureInstance(schema_name=schema_name, table_name=table_name, **kwargs)


def _procs(fake):
    return [proc for proc, _params in fake.exec_calls]


def test_create_enables_table(fake_db):
    plan = Plan(
        database="db", create=[CreateAction(table=_table("dbo.orders", columns=["id"]))]
    )
    report = apply_plan(_FakeConn(), plan)
    assert report.ok and report.created == 1
    assert _procs(fake_db) == ["sys.sp_cdc_enable_table"]
    assert fake_db.exec_calls[0][1]["captured_column_list"] == "id"


def test_create_is_idempotent(fake_db):
    fake_db.instances.add("dbo_orders")
    plan = Plan(
        database="db", create=[CreateAction(table=_table("dbo.orders", columns=["id"]))]
    )
    report = apply_plan(_FakeConn(), plan)
    assert report.ok
    # already-present instance -> no enable call (a no-op re-run)
    assert _procs(fake_db) == []


def test_enable_db_runs_first(fake_db):
    fake_db.cdc_enabled = False
    plan = Plan(
        database="db",
        enable_db=True,
        create=[CreateAction(table=_table("dbo.orders", columns=["id"]))],
    )
    report = apply_plan(_FakeConn(), plan)
    assert report.ok
    assert _procs(fake_db) == ["sys.sp_cdc_enable_db", "sys.sp_cdc_enable_table"]


def test_recreate_safe_creates_versioned_then_drops_old(fake_db):
    fake_db.instances.add("dbo_orders")
    fake_db.source_instances[("dbo", "orders")] = ["dbo_orders"]
    action = RecreateAction(
        table=_table("dbo.orders", columns=["id", "new"]),
        instance=_instance("dbo.orders", columns=["id"]),
        reasons=["columns changed"],
    )
    report = apply_plan(_FakeConn(), Plan(database="db", recreate=[action]), safe=True)
    assert report.ok and report.recreated == 1
    assert _procs(fake_db) == ["sys.sp_cdc_enable_table", "sys.sp_cdc_disable_table"]
    assert fake_db.exec_calls[0][1]["capture_instance"] == "dbo_orders_v2"
    assert fake_db.exec_calls[1][1]["capture_instance"] == "dbo_orders"


def test_recreate_safe_refuses_when_two_instances_exist(fake_db):
    fake_db.instances.update({"dbo_orders", "dbo_orders_v2"})
    fake_db.source_instances[("dbo", "orders")] = ["dbo_orders", "dbo_orders_v2"]
    action = RecreateAction(
        table=_table("dbo.orders", columns=["id"]),
        instance=_instance("dbo.orders", columns=["id"]),
        reasons=["columns changed"],
    )
    report = apply_plan(_FakeConn(), Plan(database="db", recreate=[action]), safe=True)
    assert not report.ok and report.failed == 1
    assert "2 capture instances already exist" in report.results[0].detail


def test_drop_disables_instances(fake_db):
    fake_db.instances.add("dbo_legacy")
    action = DropAction(
        schema_name="dbo", table_name="legacy", instances=[_instance("dbo.legacy")]
    )
    report = apply_plan(_FakeConn(), Plan(database="db", drop=[action]))
    assert report.ok and report.removed == 1
    assert _procs(fake_db) == ["sys.sp_cdc_disable_table"]


def test_lock_failure_raises(fake_db):
    fake_db.lock_result = -1
    plan = Plan(
        database="db", create=[CreateAction(table=_table("dbo.orders", columns=["id"]))]
    )
    with pytest.raises(DatabaseError, match="advisory lock"):
        apply_plan(_FakeConn(), plan)


def test_continue_on_error_proceeds(fake_db):
    fake_db.fail_on_capture = "dbo_a"
    plan = Plan(
        database="db",
        create=[
            CreateAction(table=_table("dbo.a", columns=["id"])),
            CreateAction(table=_table("dbo.b", columns=["id"])),
        ],
    )
    report = apply_plan(_FakeConn(), plan, continue_on_error=True)
    assert report.failed == 1 and report.created == 1
    assert "dbo_b" in fake_db.instances
