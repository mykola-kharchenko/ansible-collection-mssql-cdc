# -*- coding: utf-8 -*-
#
# (c) 2026, Mykola Kharchenko <mykola.kharchenko@outlook.com>
# MIT License
"""Shared helpers for the ``mykola_kharchenko.mssql_cdc`` modules.

Wraps the vendored engine under :mod:`...module_utils._engine` so individual
modules don't duplicate the connection-string boilerplate or the engine-error
translation. Modules merge :data:`COMMON_ARGUMENT_SPEC` into their own
``argument_spec`` and call :func:`connect_from_module` to get a ready-to-use
pyodbc connection — failures become clean ``module.fail_json`` outputs, never
raw tracebacks.
"""

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import traceback

from ansible_collections.mykola_kharchenko.mssql_cdc.plugins.module_utils._engine import db
from ansible_collections.mykola_kharchenko.mssql_cdc.plugins.module_utils._engine.errors import (
    MssqlCdcMgrError,
)

# Connection arguments common to every module in this collection. Modules merge
# this dict into their own argument_spec; the names follow the convention used
# by community.mssql / community.general so users don't have to relearn them.
COMMON_ARGUMENT_SPEC = dict(
    host=dict(type="str", required=True, aliases=["login_host", "server"]),
    port=dict(type="int", default=1433),
    login_user=dict(type="str", required=True),
    login_password=dict(type="str", required=True, no_log=True),
    database=dict(type="str", required=True, aliases=["db"]),
    encrypt=dict(type="bool", default=True),
    trust_server_certificate=dict(type="bool", default=False),
    connect_timeout=dict(type="int", default=30),
)


def connect_from_module(module, *, database=None):
    """Open a pyodbc connection using the module's connection arguments.

    Args:
        module: The :class:`AnsibleModule` instance (for params + fail_json).
        database: Override the connect-target database (defaults to
            ``module.params['database']``).

    Returns:
        A live pyodbc connection. The function does *not* return on failure —
        it calls ``module.fail_json`` which terminates the module.
    """
    params = module.params
    try:
        return db.connect(
            host=params["host"],
            port=params["port"],
            user=params["login_user"],
            password=params["login_password"],
            database=database or params["database"],
            encrypt=params["encrypt"],
            trust_cert=params["trust_server_certificate"],
            timeout=params["connect_timeout"],
        )
    except MssqlCdcMgrError as exc:
        module.fail_json(msg=str(exc))


def fail_from_engine(module, exc, *, context=None):
    """Translate an engine exception to ``module.fail_json``.

    ``MssqlCdcMgrError`` subclasses already carry an actionable message; other
    exceptions are wrapped with the optional ``context`` prefix and a traceback
    so the operator can debug.
    """
    if isinstance(exc, MssqlCdcMgrError):
        module.fail_json(msg=str(exc))
    prefix = f"{context}: " if context else ""
    module.fail_json(msg=f"{prefix}unexpected error: {exc}", exception=traceback.format_exc())


def make_diff(before, after):
    """Build an Ansible ``--diff`` payload from two state dicts.

    Returns the ``{'before': str, 'after': str}`` shape Ansible's diff callback
    expects, with the dicts rendered as deterministic JSON so the on-screen
    diff is readable.
    """
    import json as _json  # local import keeps the module hot path lean

    def _render(payload):
        return _json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n"

    return {"before": _render(before), "after": _render(after)}
