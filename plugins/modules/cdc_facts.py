#!/usr/bin/python
# -*- coding: utf-8 -*-
#
# (c) 2026, Mykola Kharchenko <mykola.kharchenko@outlook.com>
# MIT License

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r"""
---
module: cdc_facts
short_description: Gather Change Data Capture (CDC) state from a SQL Server database
version_added: "0.1.0"
description:
  - Reads the database-level CDC flag and the live capture-instance inventory,
    and registers them as C(ansible_facts.mssql_cdc) for downstream tasks
    (drift checks, dashboards, conditional logic).
  - Read-only. Always returns C(changed=false).
author:
  - Mykola Kharchenko (@mykola-kharchenko)
options:
  host:
    description: SQL Server host.
    type: str
    required: true
    aliases: [login_host, server]
  port:
    description: TCP port.
    type: int
    default: 1433
  login_user:
    description: SQL Server login that can read C(cdc.change_tables).
    type: str
    required: true
  login_password:
    description: Password for I(login_user). Use Ansible Vault.
    type: str
    required: true
  database:
    description: Database to inspect.
    type: str
    required: true
    aliases: [db]
  encrypt:
    description: Enable TLS on the connection.
    type: bool
    default: true
  trust_server_certificate:
    description: Trust a self-signed server certificate.
    type: bool
    default: false
  connect_timeout:
    description: Connection timeout in seconds.
    type: int
    default: 30
  gather_schema_details:
    description:
      - When true (the default), also reads source-table structure (column
        types, primary keys, identity columns, foreign keys and
        C(MS_Description) comments) for every captured table and exposes it
        under each table's C(schema) key.
      - Set to C(false) to skip those extra catalog queries — useful when you
        only need the CDC inventory (a few-millisecond saving per database).
    type: bool
    default: true
requirements:
  - "C(pyodbc) on the host that executes the module"
  - "Microsoft ODBC Driver 18 for SQL Server + unixODBC"
attributes:
  check_mode:
    description: The module is read-only, so C(check_mode) returns the same facts as a normal run.
    support: full
  diff_mode:
    description: Read-only modules do not produce diffs.
    support: none
"""

EXAMPLES = r"""
- name: Gather CDC state
  mykola_kharchenko.mssql_cdc.cdc_facts:
    host: prod-orders.db.internal
    login_user: cdc_admin
    login_password: "{{ vault_cdc_admin_pw }}"
    database: prod_orders
  register: cdc

- name: Fail if a critical table is not captured
  ansible.builtin.fail:
    msg: "dbo.orders is not under CDC on {{ inventory_hostname }}"
  when: "'dbo.orders' not in (ansible_facts.mssql_cdc.tables | map(attribute='source_table'))"
"""

RETURN = r"""
ansible_facts:
  description: Facts registered by the module.
  returned: always
  type: dict
  contains:
    mssql_cdc:
      description: Live CDC state of the inspected database.
      type: dict
      contains:
        database:
          description: The database that was inspected.
          type: str
        cdc_enabled:
          description: Whether database-level CDC is enabled.
          type: bool
        tables:
          description: One entry per live capture instance.
          type: list
          elements: dict
          contains:
            source_table:
              description: C(schema.table) of the source table.
              type: str
            capture_instance:
              description: The capture-instance name (may be C(_vN) after a safe recreate).
              type: str
            supports_net_changes:
              description: Whether the instance supports net-changes queries.
              type: bool
            role_name:
              description: Role granted access to the change table (or null).
              type: str
            index_name:
              description: Unique index used to support net changes (CDC's choice when unset).
              type: str
            filegroup_name:
              description: Filegroup the change table lives in.
              type: str
            columns:
              description: Columns currently captured.
              type: list
              elements: str
            source_columns:
              description: Every column of the source table (used to compute C(captures_all_columns)).
              type: list
              elements: str
            captures_all_columns:
              description: True when the capture set equals the full source-column set.
              type: bool
            schema:
              description:
                - Source-table structure for documentation rendering. Present
                  only when I(gather_schema_details=true).
              type: dict
              contains:
                note:
                  description: The table's C(MS_Description), if any.
                  type: str
                columns:
                  description: All source columns with type and constraint info.
                  type: list
                  elements: dict
                  contains:
                    name:
                      description: Column name.
                      type: str
                    type:
                      description: DBML-style type (e.g. C(varchar(50)), C(decimal(10,2))).
                      type: str
                    nullable:
                      description: Whether the column allows NULL.
                      type: bool
                    is_pk:
                      description: Whether the column is part of the primary key.
                      type: bool
                    is_identity:
                      description: Whether the column is an IDENTITY column.
                      type: bool
                    ref:
                      description: C(schema.table.column) target for a foreign key, or null.
                      type: str
                    note:
                      description: The column's C(MS_Description), if any.
                      type: str
"""

from ansible.module_utils.basic import AnsibleModule

from ansible_collections.mykola_kharchenko.mssql_cdc.plugins.module_utils._engine import (
    schema as engine_schema,
)
from ansible_collections.mykola_kharchenko.mssql_cdc.plugins.module_utils._engine import (
    state as engine_state,
)
from ansible_collections.mykola_kharchenko.mssql_cdc.plugins.module_utils.cdc import (
    COMMON_ARGUMENT_SPEC,
    connect_from_module,
    fail_from_engine,
)


def _serialise_schema_table(schema_table):
    return dict(
        note=schema_table.note,
        columns=[
            dict(
                name=col.name,
                type=col.data_type,
                nullable=col.nullable,
                is_pk=col.is_pk,
                is_identity=col.is_identity,
                ref=col.ref,
                note=col.note,
            )
            for col in schema_table.columns
        ],
    )


def _serialise(state, schema_tables):
    tables = []
    for instance in sorted(state.instances, key=lambda i: (i.source, i.capture_instance)):
        entry = dict(
            source_table=instance.source,
            capture_instance=instance.capture_instance,
            supports_net_changes=instance.supports_net_changes,
            role_name=instance.role_name,
            index_name=instance.index_name,
            filegroup_name=instance.filegroup_name,
            columns=list(instance.columns),
            source_columns=list(instance.source_columns),
            captures_all_columns=instance.captures_all_columns,
        )
        # schema_tables is None when gather_schema_details=false; otherwise it
        # carries only the source tables that are actually under CDC.
        if schema_tables is not None:
            schema_table = schema_tables.get(instance.source)
            if schema_table is not None:
                entry["schema"] = _serialise_schema_table(schema_table)
        tables.append(entry)
    return dict(database=state.database, cdc_enabled=state.cdc_enabled, tables=tables)


def main():
    argument_spec = dict(COMMON_ARGUMENT_SPEC)
    argument_spec["gather_schema_details"] = dict(type="bool", default=True)
    module = AnsibleModule(argument_spec=argument_spec, supports_check_mode=True)

    conn = connect_from_module(module)
    try:
        try:
            state = engine_state.read_state(conn, module.params["database"])
        except Exception as exc:
            fail_from_engine(module, exc, context="reading live state")

        schema_tables = None
        if module.params["gather_schema_details"] and state.cdc_enabled:
            # Scope the read to source tables that are actually under CDC —
            # cheaper than pulling the whole catalog when only a handful of
            # tables are captured.
            captured_sources = {i.source for i in state.instances}
            if captured_sources:
                try:
                    schema_tables = engine_schema.read_schema(conn, only_tables=captured_sources)
                except Exception as exc:
                    fail_from_engine(module, exc, context="reading schema details")
            else:
                schema_tables = {}

        conn.close()
        module.exit_json(
            changed=False, ansible_facts={"mssql_cdc": _serialise(state, schema_tables)}
        )
    except Exception as exc:
        try:
            conn.close()
        except Exception:  # pragma: no cover - best-effort cleanup
            pass
        fail_from_engine(module, exc)


if __name__ == "__main__":
    main()
