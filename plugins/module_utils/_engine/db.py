"""pyodbc connection management and query/proc helpers.

Centralises the ODBC Driver 18 connection-string construction (with value
quoting so passwords containing ``;{}=`` survive), and exposes small helpers so
the rest of the tool never touches a raw cursor: :func:`run_query` returns rows
as dicts and :func:`exec_proc` runs a stored procedure with named parameters.
Connections are opened with autocommit OFF; use :func:`session` for a scope that
commits on success and rolls back on error.
"""

from __future__ import absolute_import, division, print_function
from __future__ import annotations

__metaclass__ = type

import logging
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

from ansible_collections.mykola_kharchenko.mssql_cdc.plugins.module_utils._engine.errors import DatabaseError

if TYPE_CHECKING:
    import pyodbc

logger = logging.getLogger(__name__)

ODBC_DRIVER = "ODBC Driver 18 for SQL Server"


def _quote(value: object) -> str:
    """Quote a connection-string value, escaping ODBC's brace/delimiter chars."""
    text = str(value)
    if any(ch in text for ch in ";{}="):
        return "{" + text.replace("}", "}}") + "}"
    return text


def build_connection_string(
    *,
    host: str,
    port: int,
    user: str,
    password: str,
    database: str,
    encrypt: bool = True,
    trust_cert: bool = False,
    timeout: int = 30,
    driver: str = ODBC_DRIVER,
) -> str:
    """Assemble an ODBC connection string for SQL Server."""
    fields = {
        "DRIVER": "{" + driver + "}",
        "SERVER": f"{host},{port}",
        "DATABASE": _quote(database),
        "UID": _quote(user),
        "PWD": _quote(password),
        "Encrypt": "yes" if encrypt else "no",
        "TrustServerCertificate": "yes" if trust_cert else "no",
        "Connection Timeout": str(timeout),
    }
    return ";".join(f"{key}={value}" for key, value in fields.items()) + ";"


def connect(
    *,
    host: str,
    port: int,
    user: str,
    password: str,
    database: str,
    encrypt: bool = True,
    trust_cert: bool = False,
    timeout: int = 30,
) -> pyodbc.Connection:
    """Open a pyodbc connection (autocommit OFF) to ``database`` on ``host``.

    Raises:
        DatabaseError: If the ODBC driver is missing or the connection fails,
            translated to an actionable message (never a raw pyodbc traceback).
    """
    import pyodbc

    conn_str = build_connection_string(
        host=host,
        port=port,
        user=user,
        password=password,
        database=database,
        encrypt=encrypt,
        trust_cert=trust_cert,
        timeout=timeout,
    )
    logger.debug("connecting to %s:%s/%s as %s", host, port, database, user)
    try:
        return pyodbc.connect(conn_str, autocommit=False, timeout=timeout)
    except pyodbc.Error as exc:
        raise DatabaseError(_explain_connect_error(exc, host, database)) from exc


def _explain_connect_error(exc: Exception, host: str, database: str) -> str:
    text = str(exc)
    if "Can't open lib" in text or "IM002" in text or "Data source name not found" in text:
        return (
            f"ODBC Driver 18 for SQL Server not found while connecting to {host}; "
            "install msodbcsql18 + unixODBC (see the README Requirements section)"
        )
    if "Login failed" in text or "28000" in text:
        return f"authentication failed connecting to {host} (database {database!r}): {text}"
    if "Cannot open database" in text:
        return f"database {database!r} not found or not accessible on {host}: {text}"
    return f"could not connect to {host} (database {database!r}): {text}"


@contextmanager
def session(conn: pyodbc.Connection) -> Iterator[pyodbc.Connection]:
    """Transaction scope: commit on clean exit, roll back on exception, always close."""
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def run_query(
    conn: pyodbc.Connection, sql: str, params: Sequence[Any] | None = None
) -> list[dict[str, Any]]:
    """Run a parameterised query and return rows as a list of dicts."""
    cursor = conn.cursor()
    try:
        cursor.execute(sql, params if params is not None else [])
        if cursor.description is None:
            return []
        columns = [col[0] for col in cursor.description]
        return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
    finally:
        cursor.close()


def exec_proc(conn: pyodbc.Connection, proc: str, **params: Any) -> list[dict[str, Any]]:
    """Execute a stored procedure with named parameters, returning any result set.

    The procedure name and parameter names are tool-controlled constants; only
    the *values* come from config, and they are always bound as parameters — no
    value is ever interpolated into the SQL text.
    """
    names = list(params)
    assignments = ", ".join(f"@{name} = ?" for name in names)
    sql = f"EXEC {proc} {assignments}".strip()
    values = [params[name] for name in names]
    logger.debug("exec %s with %s", proc, {k: _redact(k, v) for k, v in params.items()})

    cursor = conn.cursor()
    try:
        cursor.execute(sql, values)
        if cursor.description is None:
            return []
        columns = [col[0] for col in cursor.description]
        return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
    finally:
        cursor.close()


def _redact(name: str, value: Any) -> Any:
    return "***" if "pass" in name.lower() or "pwd" in name.lower() else value


def check_cdc_permissions(conn: pyodbc.Connection) -> None:
    """Preflight: fail early with a clear message if the user can't manage CDC.

    Enabling CDC requires membership in ``db_owner`` (or sysadmin). We check that
    explicitly so an apply surfaces a permission problem up front rather than
    part-way through. If the check is inconclusive (NULL), we let the apply
    proceed and surface any real error from the proc.
    """
    rows = run_query(conn, "SELECT IS_MEMBER('db_owner') AS is_owner;")
    is_owner = rows[0]["is_owner"] if rows else None
    if is_owner == 0:
        raise DatabaseError(
            "the connected login is not a member of db_owner; managing CDC "
            "requires db_owner (or sysadmin). Grant it or use a different profile."
        )
