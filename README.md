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

Every module is `check_mode`-safe and supports `--diff`.

## Quick start

```yaml
- name: Manage CDC on prod_orders
  hosts: prod_orders
  gather_facts: false
  collections: [mykola_kharchenko.mssql_cdc]

  vars:
    mssql_login: { host: "{{ inventory_hostname }}", login_user: cdc_admin,
                   login_password: "{{ vault_cdc_admin_pw }}" }

  tasks:
    - name: Database-level CDC
      cdc_db:
        database: prod_orders
        state: enabled
        <<: *mssql_login

    - name: Capture instances
      cdc_table:
        database: prod_orders
        schema: dbo
        name: "{{ item.name }}"
        columns: "{{ item.columns | default(omit) }}"
        role_name: cdc_reader
        state: present
        <<: *mssql_login
      loop:
        - { name: orders,    columns: [id, customer_id, status, updated_at] }
        - { name: customers, columns: [id, email] }
```

Run it normal, or in plan/drift mode:

```bash
ansible-playbook cdc.yml                  # apply
ansible-playbook cdc.yml --check --diff   # plan + drift report
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
