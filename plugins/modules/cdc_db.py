#!/usr/bin/python
# -*- coding: utf-8 -*-
#
# (c) 2026, Mykola Kharchenko <mykola.kharchenko@outlook.com>
# MIT License

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r"""
---
module: cdc_db
short_description: Enable or disable database-level Change Data Capture (CDC)
version_added: "0.1.0"
description:
  - Toggles the database-level CDC flag in Microsoft SQL Server by invoking
    C(sys.sp_cdc_enable_db) / C(sys.sp_cdc_disable_db) on the target database.
  - Idempotent. Re-running with the same C(state) is a no-op.
  - Required before any table-level capture can be enabled with M(mykola_kharchenko.mssql_cdc.cdc_table).
author:
  - Mykola Kharchenko (@mykola-kharchenko)
options:
  host:
    description: SQL Server host name or address.
    type: str
    required: true
    aliases: [login_host, server]
  port:
    description: TCP port the SQL Server listens on.
    type: int
    default: 1433
  login_user:
    description: SQL Server login with permission to manage CDC (typically a db_owner).
    type: str
    required: true
  login_password:
    description: Password for I(login_user). Use Ansible Vault.
    type: str
    required: true
  database:
    description: Database to manage the CDC flag on.
    type: str
    required: true
    aliases: [db]
  state:
    description:
      - C(enabled) calls C(sys.sp_cdc_enable_db) when CDC is currently off.
      - C(disabled) calls C(sys.sp_cdc_disable_db) when CDC is currently on.
    type: str
    choices: [enabled, disabled]
    default: enabled
  encrypt:
    description: Enable TLS on the connection.
    type: bool
    default: true
  trust_server_certificate:
    description: Trust a self-signed or otherwise unverifiable server certificate.
    type: bool
    default: false
  connect_timeout:
    description: Connection timeout in seconds.
    type: int
    default: 30
requirements:
  - "Python C(mssqlcdcmgr) >= 0.1 on the host that executes the module"
  - "Microsoft ODBC Driver 18 for SQL Server + unixODBC"
attributes:
  check_mode:
    description: Reports what would change without invoking C(sp_cdc_enable_db) / C(sp_cdc_disable_db).
    support: full
  diff_mode:
    description: Returns a before/after JSON snippet of the C(is_cdc_enabled) flag.
    support: full
notes:
  - >-
    Run with C(delegate_to=localhost) (or via a controller-side play) if you do
    not want to install C(pyodbc) and the ODBC driver on every database host.
"""

EXAMPLES = r"""
- name: Make sure CDC is enabled on prod_orders
  mykola_kharchenko.mssql_cdc.cdc_db:
    host: prod-orders.db.internal
    login_user: cdc_admin
    login_password: "{{ vault_cdc_admin_pw }}"
    database: prod_orders
    state: enabled

- name: Tear CDC back down on a sandbox database
  mykola_kharchenko.mssql_cdc.cdc_db:
    host: "{{ inventory_hostname }}"
    login_user: cdc_admin
    login_password: "{{ vault_cdc_admin_pw }}"
    database: sandbox
    state: disabled
    trust_server_certificate: true
"""

RETURN = r"""
changed:
  description: Whether the module mutated the database.
  returned: always
  type: bool
msg:
  description: Human-readable summary of what the module did (or would do).
  returned: always
  type: str
database:
  description: The database the module operated on.
  returned: always
  type: str
state_before:
  description: CDC state before the module ran.
  returned: always
  type: str
  sample: disabled
state_after:
  description: CDC state after the module ran (the desired state in check_mode).
  returned: always
  type: str
  sample: enabled
"""

from ansible.module_utils.basic import AnsibleModule

from ansible_collections.mykola_kharchenko.mssql_cdc.plugins.module_utils.cdc import (
    COMMON_ARGUMENT_SPEC,
    connect_from_module,
    fail_from_engine,
    make_diff,
    require_engine,
)


def _read_cdc_flag(conn, database):
    # Engine import is local because require_engine() guards the import path.
    from mssqlcdcmgr import db as engine_db

    rows = engine_db.run_query(
        conn,
        "SELECT is_cdc_enabled FROM sys.databases WHERE name = ?;",
        [database],
    )
    if not rows:
        return None
    return bool(rows[0]["is_cdc_enabled"])


def _state_word(flag):
    if flag is None:
        return "unknown"
    return "enabled" if flag else "disabled"


def main():
    argument_spec = dict(COMMON_ARGUMENT_SPEC)
    argument_spec["state"] = dict(type="str", choices=["enabled", "disabled"], default="enabled")

    module = AnsibleModule(argument_spec=argument_spec, supports_check_mode=True)
    require_engine(module)

    # Late import: this block only runs after require_engine() validated the dep.
    from mssqlcdcmgr import db as engine_db  # noqa: F401

    database = module.params["database"]
    desired = module.params["state"]

    conn = connect_from_module(module)
    try:
        current_flag = _read_cdc_flag(conn, database)
        if current_flag is None:
            module.fail_json(msg=f"database {database!r} was not found on the server")

        current = _state_word(current_flag)
        result = dict(
            database=database,
            state_before=current,
            state_after=current,
            msg=f"database CDC is already {current}",
            changed=False,
        )

        if current == desired:
            if module._diff:
                result["diff"] = make_diff({"cdc": current}, {"cdc": current})
            conn.close()
            module.exit_json(**result)

        result["state_after"] = desired
        result["msg"] = f"would {desired[:-1]}" if module.check_mode else f"{desired[:-1]}d"
        result["msg"] += " database-level CDC"
        result["changed"] = True
        if module._diff:
            result["diff"] = make_diff({"cdc": current}, {"cdc": desired})

        if not module.check_mode:
            proc = "sys.sp_cdc_enable_db" if desired == "enabled" else "sys.sp_cdc_disable_db"
            try:
                engine_db.exec_proc(conn, proc)
                conn.commit()
            except Exception as exc:
                conn.rollback()
                fail_from_engine(module, exc, context=proc)

        conn.close()
        module.exit_json(**result)
    except Exception as exc:
        try:
            conn.close()
        except Exception:  # pragma: no cover - best-effort cleanup
            pass
        fail_from_engine(module, exc)


if __name__ == "__main__":
    main()
