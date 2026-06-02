# Ansible Collection: mykola_kharchenko.mssql_cdc

[![CI](https://github.com/mykola-kharchenko/ansible-collection-mssql-cdc/actions/workflows/ci.yml/badge.svg)](https://github.com/mykola-kharchenko/ansible-collection-mssql-cdc/actions/workflows/ci.yml)
[![Galaxy](https://img.shields.io/badge/galaxy-mykola__kharchenko.mssql__cdc-blue)](https://galaxy.ansible.com/ui/repo/published/mykola_kharchenko/mssql_cdc/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Declarative management of Microsoft SQL Server Change Data Capture (CDC) for
Ansible. Native modules, a role and example playbooks so a single play converges
CDC across a fleet of databases — with `--check`, `--diff` and idempotency
exactly as Ansible users expect.

Self-contained: the diff / apply / safe-recreate engine is vendored under
`plugins/module_utils/_engine/`. The only runtime dependency outside Ansible is
`pyodbc` (plus the OS-level ODBC Driver 18 for SQL Server).

> **Status:** early development — see the [CHANGELOG](CHANGELOG.md).

## Install

```bash
ansible-galaxy collection install mykola_kharchenko.mssql_cdc
pip install -r requirements.txt   # Python deps for the modules
```

The Python modules need the Microsoft **ODBC Driver 18 for SQL Server** and
`unixODBC` on whichever host runs them (the Ansible controller for
`delegate_to: localhost`, otherwise the target):

- **Debian/Ubuntu**

  ```bash
  curl -fsSL https://packages.microsoft.com/keys/microsoft.asc \
    | sudo tee /etc/apt/trusted.gpg.d/microsoft.asc > /dev/null
  curl -fsSL "https://packages.microsoft.com/config/ubuntu/$(. /etc/os-release; echo "$VERSION_ID")/prod.list" \
    | sudo tee /etc/apt/sources.list.d/mssql-release.list > /dev/null
  sudo apt-get update
  sudo ACCEPT_EULA=Y apt-get install -y msodbcsql18 unixodbc-dev
  ```

- **macOS**: `brew install msodbcsql18 unixodbc`
- **RHEL/Fedora**: install `msodbcsql18` from the Microsoft `packages.microsoft.com` repo.

## Modules

| FQCN                                                | What it does                                            |
|-----------------------------------------------------|---------------------------------------------------------|
| `mykola_kharchenko.mssql_cdc.cdc_db`                | Enable/disable database-level CDC                       |
| `mykola_kharchenko.mssql_cdc.cdc_table`             | Manage a capture instance (present/absent, settings)    |
| `mykola_kharchenko.mssql_cdc.cdc_facts`             | Read live CDC state into `ansible_facts.mssql_cdc`      |

Plus the **`cdc` role** (`mykola_kharchenko.mssql_cdc.cdc`), which wraps these
modules into one declarative play driven by a table list — the recommended entry
point. See [roles/cdc/README.md](roles/cdc/README.md).

Every module is `check_mode`-safe and supports `--diff`.

## Quick start

The quickest path is the bundled **`cdc` role** — describe the desired state as
vars and let one play converge it:

```yaml
- name: Manage CDC on prod_orders
  hosts: prod_orders
  gather_facts: false
  collections: [mykola_kharchenko.mssql_cdc]
  vars:
    mssql_cdc_login_user: cdc_admin
    mssql_cdc_login_password: "{{ vault_cdc_admin_pw }}"   # vault
    mssql_cdc_database: prod_orders
    mssql_cdc_default_role_name: cdc_reader
    mssql_cdc_tables:
      - { schema: dbo, name: orders, captured_columns: [id, customer_id, status] }
      - { schema: dbo, name: customers }                    # capture all columns
      - { schema: dbo, name: legacy, state: absent }        # drop the capture instance
  roles:
    - mykola_kharchenko.mssql_cdc.cdc
```

`captured_columns` is a **merge**: a column you don't list is left alone. To stop
capturing just one column without disturbing the rest, mark it `absent`:

```yaml
    mssql_cdc_tables:
      - schema: dbo
        name: customers
        captured_columns:
          - { name: ssn, state: absent }   # remove ssn; keep every other captured column
```

Keep `mssql_cdc_login_password` (and any other secrets) in Ansible Vault. See the
[role README](roles/cdc/README.md) for every variable, a short Vault how-to, and
the per-server vs per-database inventory layout.

<details>
<summary>Lower-level: calling the modules directly</summary>

If you'd rather not use the role, drive the modules yourself:

```yaml
  vars:
    mssql_login: &mssql_login
      host: "{{ inventory_hostname }}"
      login_user: cdc_admin
      login_password: "{{ vault_cdc_admin_pw }}"   # vault
  tasks:
    - name: Database-level CDC
      cdc_db: { database: prod_orders, state: enabled, <<: *mssql_login }

    - name: Capture instances
      cdc_table:
        database: prod_orders
        schema: dbo
        name: "{{ item.name }}"
        captured_columns: "{{ item.captured_columns | default(omit) }}"
        role_name: cdc_reader
        state: present
        <<: *mssql_login
      loop:
        - { name: orders,    captured_columns: [id, customer_id, status, updated_at] }
        - { name: customers, captured_columns: [id, email] }
```

</details>

Run it normally, or as a plan:

```bash
ansible-playbook cdc.yml                  # apply
ansible-playbook cdc.yml --check --diff   # plan: report changes, touch nothing
```

## Drift detection

Because every module is `check_mode`- and `--diff`-aware, a no-write run doubles
as a drift report — each capture instance whose live settings differ from the
desired state is reported with *why* it would change:

```bash
ansible-playbook playbooks/apply.yml --check --diff
```

```text
changed: [headoffice] => recreate dbo.orders (columns changed (added updated_by))
changed: [headoffice] => enable CDC on dbo.invoices
```

For a CI cron that should *fail* when a required table isn't captured, use the
bundled [`playbooks/drift.yml`](playbooks/drift.yml): it gathers live state with
`cdc_facts` and fails the play on any missing capture instance.

## Reading live state (`cdc_facts`)

`cdc_facts` snapshots the live CDC configuration into `ansible_facts.mssql_cdc`
(`cdc_enabled` plus a `tables` list) for your own assertions or reporting:

```yaml
- mykola_kharchenko.mssql_cdc.cdc_facts:
    host: "{{ inventory_hostname }}"
    login_user: cdc_admin
    login_password: "{{ vault_cdc_admin_pw }}"
    database: prod_orders
  register: cdc

- ansible.builtin.debug:
    var: ansible_facts.mssql_cdc.tables

- name: Fail if orders is not captured
  ansible.builtin.fail:
    msg: dbo.orders is not under CDC
  when: "'dbo.orders' not in (ansible_facts.mssql_cdc.tables | map(attribute='source_table'))"
```

## Layout

```
plugins/
  modules/                # cdc_db, cdc_table, cdc_facts
  module_utils/cdc.py     # shared connection + error mapping
roles/cdc/                # opinionated role that loops over a table list
playbooks/                # enable.yml, drift.yml, apply.yml
tests/                    # ansible-test sanity + pytest integration
```

## Schema documentation (DBML)

`cdc_facts` (with `gather_schema_details: true`, the default) also returns
column types, primary keys, identity columns, foreign keys and
`MS_Description` comments for every captured table. The bundled
`playbooks/generate-docs.yml` + `playbooks/templates/cdc.dbml.j2` use those
facts to render per-host per-database DBML you can paste into
[dbdiagram.io](https://dbdiagram.io) or preview with the VSCode DBML
extension.

```bash
ansible-playbook -i inventory playbooks/generate-docs.yml
# writes docs/<inventory_hostname>/<mssql_cdc_database>.dbml per host
```

No new module — pure facts + Jinja2. Swap the template for markdown / HTML
/ JSON / mermaid as needed without touching the collection.

## Engine

The diff / apply / safe-recreate logic lives in
[`plugins/module_utils/_engine/`](plugins/module_utils/_engine/) (vendored,
no external PyPI dependency). It is an internal implementation detail of the
collection — modules call into it through
[`plugins/module_utils/cdc.py`](plugins/module_utils/cdc.py); users only ever
touch the public modules and the `cdc` role.

## Development

```bash
# Sanity (matches CI)
pip install "ansible-core>=2.16"
mkdir -p /tmp/coll/ansible_collections/mykola_kharchenko
cp -r . /tmp/coll/ansible_collections/mykola_kharchenko/mssql_cdc
( cd /tmp/coll/ansible_collections/mykola_kharchenko/mssql_cdc \
  && ansible-test sanity --python 3.12 )

# Integration (needs Docker + ODBC Driver 18)
pip install -r tests/integration/requirements.txt
ansible-galaxy collection install .
pytest tests/integration -v
```

See [RELEASING.md](RELEASING.md) for the release process.

## License

MIT — see [LICENSE](LICENSE).
