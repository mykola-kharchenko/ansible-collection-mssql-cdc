#!/usr/bin/python
# -*- coding: utf-8 -*-
#
# (c) 2026, Mykola Kharchenko <mykola.kharchenko@outlook.com>
# MIT License

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r"""
---
module: cdc_table
short_description: Manage a Change Data Capture (CDC) capture instance on a source table
version_added: "0.1.0"
description:
  - Declares the desired CDC state of one source table.
  - With C(state=present), creates the capture instance if missing or recreates
    it (safe by default) when settings differ. With C(state=absent), drops the
    capture instance if it exists.
  - Safe recreate enables a second C(_vN)-suffixed capture instance with the new
    settings, confirms it appeared, then drops the old one. SQL Server allows up
    to two instances per source table, so consumers do not see a gap.
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
    description: SQL Server login with permission to manage CDC (typically C(db_owner)).
    type: str
    required: true
  login_password:
    description: Password for I(login_user). Use Ansible Vault.
    type: str
    required: true
  database:
    description: Database the source table lives in.
    type: str
    required: true
    aliases: [db]
  schema:
    description: Source-table schema (for example C(dbo)).
    type: str
    required: true
  name:
    description: Source-table name.
    type: str
    required: true
    aliases: [table]
  state:
    description:
      - C(present) enables/recreates the capture instance as needed.
      - C(absent) disables it if it exists.
    type: str
    choices: [present, absent]
    default: present
  capture_instance:
    description:
      - Desired capture-instance name. Defaults to C({schema}_{name}), matching
        SQL Server's own default.
    type: str
  columns:
    description:
      - Source columns to capture. Omit (or pass an empty list) to capture all
        columns. The module compares this set against the live capture and
        recreates when it differs.
    type: list
    elements: str
  supports_net_changes:
    description: Enable C(supports_net_changes). The source table must have a primary key or a unique index.
    type: bool
    default: true
  role_name:
    description: SQL Server role granted SELECT on the change table. Pass null/omit for none.
    type: str
  index_name:
    description:
      - Unique index name used to support net changes. Leave unset to let CDC pick the primary key.
      - C(null)/omit is treated as "accept CDC's choice" so set values do not perpetually drift.
    type: str
  filegroup_name:
    description:
      - Filegroup the change table lives in. Leave unset to accept CDC's default.
    type: str
  recreate_strategy:
    description:
      - C(safe) creates a second C(_vN) instance with the new settings, confirms
        it exists, then drops the old one. C(unsafe) disables then re-enables
        in place (brief consumer gap).
    type: str
    choices: [safe, unsafe]
    default: safe
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
requirements:
  - "C(pyodbc) on the host that executes the module"
  - "Microsoft ODBC Driver 18 for SQL Server + unixODBC"
  - "Database-level CDC must already be enabled (use M(mykola_kharchenko.mssql_cdc.cdc_db))"
attributes:
  check_mode:
    description: Computes and reports the plan without invoking the C(sp_cdc_*) procedures.
    support: full
  diff_mode:
    description: Returns a before/after JSON snippet of the source table's capture state.
    support: full
"""

EXAMPLES = r"""
- name: Capture all columns of dbo.orders
  mykola_kharchenko.mssql_cdc.cdc_table:
    host: prod-orders.db.internal
    login_user: cdc_admin
    login_password: "{{ vault_cdc_admin_pw }}"
    database: prod_orders
    schema: dbo
    name: orders

- name: Capture a specific column set with a reader role
  mykola_kharchenko.mssql_cdc.cdc_table:
    host: prod-orders.db.internal
    login_user: cdc_admin
    login_password: "{{ vault_cdc_admin_pw }}"
    database: prod_orders
    schema: dbo
    name: customers
    columns: [id, email, created_at]
    role_name: cdc_pii_reader

- name: Decommission CDC on a retired table
  mykola_kharchenko.mssql_cdc.cdc_table:
    host: prod-orders.db.internal
    login_user: cdc_admin
    login_password: "{{ vault_cdc_admin_pw }}"
    database: prod_orders
    schema: dbo
    name: legacy
    state: absent
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
capture_instance:
  description: The capture-instance name the table is captured under after the run (may be C(_vN) after a safe recreate).
  returned: when state is present and the table is captured
  type: str
reasons:
  description: For a recreate, the human-readable list of reasons the live state differed.
  returned: when a recreate happens or would happen
  type: list
  elements: str
  sample: ["columns changed (added updated_by)"]
"""

from ansible.module_utils.basic import AnsibleModule

from ansible_collections.mykola_kharchenko.mssql_cdc.plugins.module_utils._engine import (
    state as engine_state,
)
from ansible_collections.mykola_kharchenko.mssql_cdc.plugins.module_utils._engine.apply import (
    apply_plan,
)
from ansible_collections.mykola_kharchenko.mssql_cdc.plugins.module_utils._engine.config import (
    Config,
    Table,
    merge_defaults,
)
from ansible_collections.mykola_kharchenko.mssql_cdc.plugins.module_utils._engine.diff import (
    compute_diff,
)
from ansible_collections.mykola_kharchenko.mssql_cdc.plugins.module_utils._engine.state import (
    ActualState,
)
from ansible_collections.mykola_kharchenko.mssql_cdc.plugins.module_utils.cdc import (
    COMMON_ARGUMENT_SPEC,
    connect_from_module,
    fail_from_engine,
    make_diff,
)


def _build_desired(params):
    """Build a merged Config containing only this one source table."""
    table = Table(
        schema_name=params["schema"],
        table_name=params["name"],
        capture_instance=params.get("capture_instance"),
        columns=params.get("columns") or None,
        role_name=params.get("role_name"),
        supports_net_changes=params["supports_net_changes"],
        index_name=params.get("index_name"),
        filegroup_name=params.get("filegroup_name"),
        # _explicit drives merge_defaults; mark every supplied key so the user's
        # intent (including explicit nulls for role_name) survives the merge.
        _explicit=frozenset(
            k
            for k in (
                "capture_instance",
                "columns",
                "role_name",
                "supports_net_changes",
                "index_name",
                "filegroup_name",
            )
            if params.get(k) is not None or k == "supports_net_changes"
        ),
    )
    config = Config(
        version=1,
        database=params["database"],
        host=params["host"],
        tables={table.key: table},
    )
    return merge_defaults(config)


def _scoped_state(actual_state, source_key):
    """Return ``actual_state`` with only the instances of one source table.

    This lets us reuse ``compute_diff`` without it dropping the other tables in
    the database — they simply aren't part of this module's scope.
    """
    return ActualState(
        database=actual_state.database,
        cdc_enabled=actual_state.cdc_enabled,
        instances=[i for i in actual_state.instances if i.source == source_key],
    )


def _empty_desired(params):
    """Build a 0-table merged Config (used to compute the drop plan for state=absent)."""
    return merge_defaults(
        Config(version=1, database=params["database"], host=params["host"], tables={})
    )


def _state_snapshot(scoped):
    """Compact, JSON-friendly summary of a single source-table's live state for --diff."""
    if not scoped.instances:
        return {"captured": False}
    primary = scoped.instances[-1]
    return {
        "captured": True,
        "capture_instance": primary.capture_instance,
        "supports_net_changes": primary.supports_net_changes,
        "role_name": primary.role_name,
        "columns": list(primary.columns),
    }


def _desired_snapshot(state, params, plan):
    """The post-apply view used as the ``after`` side of the diff."""
    if state == "absent":
        return {"captured": False}
    if plan.create:
        return {
            "captured": True,
            "capture_instance": plan.create[0].table.capture_instance,
            "supports_net_changes": plan.create[0].table.supports_net_changes,
            "role_name": plan.create[0].table.role_name,
            "columns": params.get("columns") or "all",
        }
    if plan.recreate:
        return {
            "captured": True,
            "capture_instance": plan.recreate[0].table.capture_instance + " (recreated)",
            "supports_net_changes": plan.recreate[0].table.supports_net_changes,
            "role_name": plan.recreate[0].table.role_name,
            "columns": params.get("columns") or "all",
        }
    # No changes: after == before.
    return None


def main():
    argument_spec = dict(COMMON_ARGUMENT_SPEC)
    argument_spec.update(
        schema=dict(type="str", required=True),
        name=dict(type="str", required=True, aliases=["table"]),
        state=dict(type="str", choices=["present", "absent"], default="present"),
        capture_instance=dict(type="str"),
        columns=dict(type="list", elements="str"),
        supports_net_changes=dict(type="bool", default=True),
        role_name=dict(type="str"),
        index_name=dict(type="str"),
        filegroup_name=dict(type="str"),
        recreate_strategy=dict(type="str", choices=["safe", "unsafe"], default="safe"),
    )

    module = AnsibleModule(argument_spec=argument_spec, supports_check_mode=True)

    source_key = f"{module.params['schema']}.{module.params['name']}"
    desired_state = module.params["state"]
    safe = module.params["recreate_strategy"] == "safe"

    conn = connect_from_module(module)
    try:
        try:
            actual_full = engine_state.read_state(conn, module.params["database"])
        except Exception as exc:
            fail_from_engine(module, exc, context="reading live state")

        scoped = _scoped_state(actual_full, source_key)
        if desired_state == "present":
            plan = compute_diff(_build_desired(module.params), scoped)
        else:
            plan = compute_diff(_empty_desired(module.params), scoped)

        result = dict(changed=False, msg="already in desired state", database=module.params["database"])
        if scoped.instances:
            result["capture_instance"] = scoped.instances[-1].capture_instance
        if plan.recreate:
            result["reasons"] = list(plan.recreate[0].reasons)

        if module._diff:
            before = _state_snapshot(scoped)
            after = _desired_snapshot(desired_state, module.params, plan)
            result["diff"] = make_diff(before, after if after is not None else before)

        if not plan.has_changes:
            conn.close()
            module.exit_json(**result)

        result["changed"] = True
        if module.check_mode:
            result["msg"] = "would " + _summarise(plan, desired_state)
            conn.close()
            module.exit_json(**result)

        try:
            report = apply_plan(conn, plan, safe=safe, continue_on_error=False)
        except Exception as exc:
            fail_from_engine(module, exc, context="applying CDC change")

        if not report.ok:
            failure = next((r for r in report.results if not r.ok), None)
            detail = failure.detail if failure else "unknown failure"
            module.fail_json(msg=f"apply failed: {detail}", apply_results=[
                dict(action=r.action, source=r.source, ok=r.ok, detail=r.detail) for r in report.results
            ])

        # Re-read the source's live state so the return reflects the post-apply truth.
        post = _scoped_state(engine_state.read_state(conn, module.params["database"]), source_key)
        if post.instances:
            result["capture_instance"] = post.instances[-1].capture_instance
        result["msg"] = _summarise(plan, desired_state)
        conn.close()
        module.exit_json(**result)
    except Exception as exc:
        try:
            conn.close()
        except Exception:  # pragma: no cover - best-effort cleanup
            pass
        fail_from_engine(module, exc)


def _summarise(plan, desired_state):
    if plan.create:
        return f"enable CDC on {plan.create[0].source}"
    if plan.recreate:
        reasons = "; ".join(plan.recreate[0].reasons)
        return f"recreate {plan.recreate[0].source} ({reasons})"
    if plan.drop:
        return f"disable CDC on {plan.drop[0].source}"
    return "no change"


if __name__ == "__main__":
    main()
