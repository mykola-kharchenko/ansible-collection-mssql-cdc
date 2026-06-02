"""Execute a :class:`Plan` against a database (the mutating path).

The order of operations is: take a per-database advisory lock, enable
database-level CDC if needed, create new capture instances, recreate changed
ones, then drop removed ones. Each operation runs in its own try/except and is
committed individually, so a failure never rolls back already-applied work and a
re-run is a safe no-op.

Recreate has two strategies:

- **safe** (default): create a second, ``_vN``-suffixed capture instance with
  the new settings, confirm it exists, then drop the old one. SQL Server allows
  up to two capture instances per source table, so consumers never see a gap.
- **unsafe**: disable then enable in place — simpler, but a brief window where
  consumers see no change table.
"""

from __future__ import absolute_import, division, print_function
from __future__ import annotations

__metaclass__ = type

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import partial
from typing import TYPE_CHECKING

from ansible_collections.mykola_kharchenko.mssql_cdc.plugins.module_utils._engine import db
from ansible_collections.mykola_kharchenko.mssql_cdc.plugins.module_utils._engine.diff import DropAction, Plan, RecreateAction
from ansible_collections.mykola_kharchenko.mssql_cdc.plugins.module_utils._engine.errors import DatabaseError

if TYPE_CHECKING:
    import pyodbc

    from .config import Table

logger = logging.getLogger(__name__)

_LOCK_RESOURCE = "mssql_cdc"
_LOCK_TIMEOUT_MS = 5000

# sp_getapplock returns >= 0 when the lock is granted, < 0 on timeout/error.
_SQL_GETAPPLOCK = (
    "DECLARE @res int; "
    "EXEC @res = sp_getapplock @Resource = ?, @LockMode = 'Exclusive', "
    "@LockOwner = 'Session', @LockTimeout = ?; "
    "SELECT @res AS result;"
)
_SQL_RELEASEAPPLOCK = "EXEC sp_releaseapplock @Resource = ?, @LockOwner = 'Session';"
_SQL_DB_ENABLED = "SELECT is_cdc_enabled FROM sys.databases WHERE name = ?;"
_SQL_INSTANCE_EXISTS = "SELECT 1 AS hit FROM cdc.change_tables WHERE capture_instance = ?;"
_SQL_INSTANCES_FOR_SOURCE = """
SELECT ct.capture_instance AS capture_instance
FROM cdc.change_tables ct
JOIN sys.tables t  ON ct.source_object_id = t.object_id
JOIN sys.schemas s ON t.schema_id = s.schema_id
WHERE s.name = ? AND t.name = ?;
"""


@dataclass
class OpResult:
    """Outcome of a single apply operation."""

    action: str  # enable_db | create | recreate | drop
    source: str
    ok: bool
    detail: str


@dataclass
class ApplyReport:
    """Per-database record of everything apply attempted."""

    database: str
    results: list[OpResult] = field(default_factory=list)

    def add(self, action: str, source: str, ok: bool, detail: str) -> None:
        level = logging.INFO if ok else logging.ERROR
        logger.log(level, "%s %s: %s", action, source, detail)
        self.results.append(OpResult(action=action, source=source, ok=ok, detail=detail))

    def _count(self, action: str) -> int:
        return sum(1 for r in self.results if r.action == action and r.ok)

    @property
    def created(self) -> int:
        return self._count("create")

    @property
    def recreated(self) -> int:
        return self._count("recreate")

    @property
    def removed(self) -> int:
        return self._count("drop")

    @property
    def failed(self) -> int:
        return sum(1 for r in self.results if not r.ok)

    @property
    def ok(self) -> bool:
        return self.failed == 0


def apply_plan(
    conn: pyodbc.Connection,
    plan: Plan,
    *,
    safe: bool = True,
    continue_on_error: bool = False,
) -> ApplyReport:
    """Apply ``plan`` to the database behind ``conn`` and return an :class:`ApplyReport`.

    Operations commit individually. With ``continue_on_error`` a failed
    operation is recorded and the rest proceed; otherwise the first failure
    stops processing (already-committed operations remain).
    """
    report = ApplyReport(database=plan.database)
    _acquire_lock(conn)
    try:
        # functools.partial (rather than a closure) avoids the late-binding loop
        # variable trap and types cleanly as Callable[[], str].
        if plan.enable_db and not _run(
            report,
            conn,
            "enable_db",
            plan.database,
            partial(_enable_db, conn, plan.database),
            continue_on_error,
        ):
            return report

        for create in plan.create:
            if not _run(
                report,
                conn,
                "create",
                create.source,
                partial(_create, conn, create.table),
                continue_on_error,
            ):
                return report

        for recreate in plan.recreate:
            if not _run(
                report,
                conn,
                "recreate",
                recreate.source,
                partial(_recreate, conn, recreate, safe=safe),
                continue_on_error,
            ):
                return report

        for drop in plan.drop:
            if not _run(
                report,
                conn,
                "drop",
                drop.source,
                partial(_drop, conn, drop),
                continue_on_error,
            ):
                return report
    finally:
        _release_lock(conn)
    return report


def _run(
    report: ApplyReport,
    conn: pyodbc.Connection,
    action: str,
    source: str,
    operation: Callable[[], str],
    continue_on_error: bool,
) -> bool:
    """Run one operation, commit/rollback, record it. Return False to stop the run."""
    try:
        detail = operation()
        conn.commit()
        report.add(action, source, True, detail)
        return True
    except Exception as exc:
        conn.rollback()
        report.add(action, source, False, _explain(exc))
        return continue_on_error


def _explain(exc: Exception) -> str:
    text = str(exc).strip()
    if "permission" in text.lower() or "does not have permission" in text.lower():
        return f"insufficient permission (needs db_owner / CDC rights): {text}"
    return text or exc.__class__.__name__


# --- Individual operations -------------------------------------------------


def _require_capture(table: Table) -> str:
    """Return the table's capture instance, asserting merge_defaults populated it."""
    if table.capture_instance is None:  # pragma: no cover - guarded by merge_defaults
        raise DatabaseError(f"{table.key}: capture_instance is unset (merge_defaults not applied?)")
    return table.capture_instance


def _enable_db(conn: pyodbc.Connection, database: str) -> str:
    if _db_cdc_enabled(conn, database):
        return "database CDC already enabled (skipped)"
    db.exec_proc(conn, "sys.sp_cdc_enable_db")
    return "enabled database-level CDC"


def _create(conn: pyodbc.Connection, table: Table) -> str:
    # merge_defaults guarantees a concrete capture_instance before we reach apply.
    capture = _require_capture(table)
    # Idempotency: do not re-enable an instance that already exists.
    if _instance_exists(conn, capture):
        return f"capture instance {capture} already exists (skipped)"
    _enable_table(conn, table, capture)
    return f"enabled CDC (capture: {capture})"


def _recreate(conn: pyodbc.Connection, action: RecreateAction, *, safe: bool) -> str:
    table = action.table
    base = _require_capture(table)
    old = action.instance.capture_instance
    existing = _instances_for_source(conn, table.schema_name, table.table_name)

    if safe:
        if len(existing) >= 2:
            raise DatabaseError(
                f"{action.source}: 2 capture instances already exist "
                f"({', '.join(existing)}); safe recreate needs a free slot "
                "(drop one or use --unsafe)"
            )
        new_name = _next_instance_name(base, existing)
        _enable_table(conn, table, new_name)
        if not _instance_exists(conn, new_name):
            raise DatabaseError(f"{action.source}: new capture instance {new_name} did not appear")
        _disable_table(conn, table.schema_name, table.table_name, old)
        return f"recreated as {new_name}, dropped {old}"

    # Unsafe: brief gap between disable and enable.
    _disable_table(conn, table.schema_name, table.table_name, old)
    _enable_table(conn, table, base)
    return f"recreated {table.capture_instance} in place (brief gap)"


def _drop(conn: pyodbc.Connection, action: DropAction) -> str:
    dropped = []
    for instance in action.instances:
        if not _instance_exists(conn, instance.capture_instance):
            continue
        _disable_table(conn, action.schema_name, action.table_name, instance.capture_instance)
        dropped.append(instance.capture_instance)
    if not dropped:
        return "already not captured (skipped)"
    return f"disabled CDC ({', '.join(dropped)})"


def _enable_table(conn: pyodbc.Connection, table: Table, capture_instance: str) -> None:
    captured = ",".join(table.columns) if table.columns else None
    db.exec_proc(
        conn,
        "sys.sp_cdc_enable_table",
        source_schema=table.schema_name,
        source_name=table.table_name,
        role_name=table.role_name,
        capture_instance=capture_instance,
        supports_net_changes=1 if table.supports_net_changes else 0,
        index_name=table.index_name,
        captured_column_list=captured,
        filegroup_name=table.filegroup_name,
        allow_partition_switch=1 if table.allow_partition_switch else 0,
    )


def _disable_table(conn: pyodbc.Connection, schema: str, table: str, capture_instance: str) -> None:
    db.exec_proc(
        conn,
        "sys.sp_cdc_disable_table",
        source_schema=schema,
        source_name=table,
        capture_instance=capture_instance,
    )


def _next_instance_name(base: str, existing: list[str]) -> str:
    """Pick the next free ``base_vN`` name (N starts at 2, per the plan's convention)."""
    candidate = 2
    while f"{base}_v{candidate}" in existing:
        candidate += 1
    return f"{base}_v{candidate}"


# --- Idempotency / lock helpers --------------------------------------------


def _acquire_lock(conn: pyodbc.Connection) -> None:
    rows = db.run_query(conn, _SQL_GETAPPLOCK, [_LOCK_RESOURCE, _LOCK_TIMEOUT_MS])
    result = rows[0]["result"] if rows else -999
    if result < 0:
        raise DatabaseError(
            f"could not acquire advisory lock {_LOCK_RESOURCE!r} (code {result}); "
            "another apply may be running against this database"
        )


def _release_lock(conn: pyodbc.Connection) -> None:
    try:
        db.run_query(conn, _SQL_RELEASEAPPLOCK, [_LOCK_RESOURCE])
        conn.commit()
    except Exception as exc:  # release is best-effort; the session close frees it anyway
        logger.debug("advisory lock release failed (will free on disconnect): %s", exc)


def _db_cdc_enabled(conn: pyodbc.Connection, database: str) -> bool:
    rows = db.run_query(conn, _SQL_DB_ENABLED, [database])
    return bool(rows and rows[0]["is_cdc_enabled"])


def _instance_exists(conn: pyodbc.Connection, capture_instance: str) -> bool:
    try:
        rows = db.run_query(conn, _SQL_INSTANCE_EXISTS, [capture_instance])
    except Exception:
        # cdc.change_tables does not exist until the database is CDC-enabled.
        return False
    return bool(rows)


def _instances_for_source(conn: pyodbc.Connection, schema: str, table: str) -> list[str]:
    try:
        rows = db.run_query(conn, _SQL_INSTANCES_FOR_SOURCE, [schema, table])
    except Exception:
        return []
    return [row["capture_instance"] for row in rows]
