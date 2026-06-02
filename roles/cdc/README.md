# `mykola_kharchenko.mssql_cdc.cdc`

Opinionated role that converges a SQL Server database to a desired CDC state in
one play. Wraps the collection's modules so you describe the goal as
inventory/vars rather than per-task module calls.

## What it does

1. Optionally toggles database-level CDC (`mssql_cdc_state`).
2. Iterates `mssql_cdc_tables` and reconciles each entry via
   `mykola_kharchenko.mssql_cdc.cdc_table` (create / recreate / drop / no-op).
   Each entry's `state` (`present`/`absent`, default `present`) decides whether
   the capture instance is created or dropped; tables you do not list at all are
   left untouched.
3. (Deprecated) Iterates `mssql_cdc_remove` to disable capture instances —
   prefer a `state: absent` entry in `mssql_cdc_tables` instead.

Both `--check` and `--diff` work end-to-end because every module supports them.

## Required variables

| Variable                  | What                                              |
|---------------------------|---------------------------------------------------|
| `mssql_cdc_login_user`    | SQL Server login with `db_owner`                  |
| `mssql_cdc_login_password`| Vault-encrypted password                          |
| `mssql_cdc_database`      | Target database                                   |

## Common variables

| Variable                          | Default               | Purpose                                  |
|-----------------------------------|-----------------------|------------------------------------------|
| `mssql_cdc_host`                  | `inventory_hostname`  | SQL Server host                          |
| `mssql_cdc_port`                  | `1433`                | TCP port                                 |
| `mssql_cdc_state`                 | `enabled`             | Database-level CDC (`enabled`/`disabled`)|
| `mssql_cdc_tables`                | `[]`                  | List of capture instances to manage      |
| `mssql_cdc_remove`                | `[]`                  | (Deprecated) capture instances to disable|
| `mssql_cdc_recreate_strategy`     | `safe`                | `safe` (versioned) or `unsafe` (in-place)|
| `mssql_cdc_default_role_name`     | `null`                | Default `role_name` for table entries    |
| `mssql_cdc_default_supports_net_changes` | `true`         | Default `supports_net_changes`           |
| `mssql_cdc_default_allow_partition_switch` | `true`       | Default `allow_partition_switch`         |

See `defaults/main.yml` for the full list.

## Table entry shape

```yaml
mssql_cdc_tables:
  - schema: dbo
    name: orders                       # required
    state: present                     # optional present|absent (default present)
    capture_instance: dbo_orders       # optional, defaults to <schema>_<name>
    captured_columns: [id, customer_id, status]  # optional, omit for "capture all"
    role_name: cdc_reader              # optional (else default)
    supports_net_changes: true         # optional (else default)
    index_name: null                   # optional
    filegroup_name: null               # optional
    allow_partition_switch: true       # optional (else default)
    recreate_strategy: safe            # optional (else mssql_cdc_recreate_strategy)
```

`captured_columns` is **merge-based**: each entry is a column name or
`{name, state}`. `present` (the default) keeps a column captured, `absent` stops
capturing it, and a column you don't list is left exactly as it is. To remove a
table entirely, set `state: absent` on its entry. Any column add/remove recreates
the capture instance (CDC can't edit one in place).

## Example play

```yaml
- hosts: prod_orders
  collections: [mykola_kharchenko.mssql_cdc]
  vars:
    mssql_cdc_login_user: cdc_admin
    mssql_cdc_login_password: "{{ vault_cdc_admin_pw }}"
    mssql_cdc_database: prod_orders
    mssql_cdc_default_role_name: cdc_reader
    mssql_cdc_tables:
      - { schema: dbo, name: orders, captured_columns: [id, customer_id, status, updated_at] }
      - { schema: dbo, name: customers, role_name: cdc_pii_reader }
      - { schema: dbo, name: legacy, state: absent }   # drop this capture instance
  roles:
    - mykola_kharchenko.mssql_cdc.cdc
```

## Secrets & inventory layout

All connection inputs — `host`, `port`, `login_user`, and above all
`login_password` — are sensitive; keep the password (at least) in Ansible Vault
and reference it as a normal variable:

```bash
ansible-vault create group_vars/sqlservers_test/vault.yml
#   vault_cdc_admin_pw: "S3cr3t"
ansible-playbook reconcile.yml --ask-vault-pass      # or --vault-password-file
```

Rule of thumb for *where* each variable belongs:

- **Per SQL Server** (a server `group_vars` file, vaulted): `mssql_cdc_login_user`,
  `mssql_cdc_login_password`, `mssql_cdc_host`/`mssql_cdc_port`,
  `mssql_cdc_encrypt`, `mssql_cdc_trust_server_certificate` — every database on
  the host shares them.
- **Per database** (`host_vars` or a per-database group): `mssql_cdc_database`,
  `mssql_cdc_tables`, and any retention/behavior overrides.

Variable sourcing is plain Ansible — the role only consumes the values already
resolved for each host, so spread them across whatever groups make sense. The
split below keeps the connection layer with the SQL Server group and the CDC
intent with a per-database group:

```text
inventory.ini
  [sqlservers_test]
  headoffice
  [cdc_headoffice]
  headoffice

group_vars/
  sqlservers_test.yml      # shared connection layer
    mssql_cdc_login_user: cdc_admin
    mssql_cdc_login_password: "{{ vault_cdc_admin_pw }}"   # vault
    mssql_cdc_encrypt: false
    mssql_cdc_trust_server_certificate: true

  cdc_headoffice.yml       # desired CDC state for this database
    mssql_cdc_database: headoffice
    mssql_cdc_tables:
      - { schema: dbo, name: orders, captured_columns: [id, status] }
      - { schema: dbo, name: legacy, state: absent }
```

`headoffice` belongs to both groups, so it inherits the connection vars *and*
the table list — no matter which group you target. `--limit sqlservers_test`
and `--limit cdc_headoffice` both run it with the same merged vars: the group
pattern only selects *which hosts* run, never *which vars* apply. To act on
hosts in both groups, intersect with `--limit 'sqlservers_test:&cdc_headoffice'`.

One gotcha: if the **same** variable (e.g. `mssql_cdc_tables`) is set in more
than one group, Ansible takes the last by group precedence — it does **not**
merge the lists. Keep each variable in a single authoritative group, or combine
explicitly in your play (`mssql_cdc_tables: "{{ tables_a + tables_b }}"`).

## Tags

- `cdc_db` — only the database-level step
- `cdc_table` — only the capture-instance steps
- `cdc_remove` — only the disable-and-drop step
- `cdc` — everything (default)

```bash
ansible-playbook reconcile.yml --tags cdc_table  # skip the database toggle
```
