# Ansible Collection: mykola_kharchenko.mssql_cdc

[![CI](https://github.com/mykola-kharchenko/ansible-collection-mssql-cdc/actions/workflows/ci.yml/badge.svg)](https://github.com/mykola-kharchenko/ansible-collection-mssql-cdc/actions/workflows/ci.yml)
[![Galaxy](https://img.shields.io/badge/galaxy-mykola__kharchenko.mssql__cdc-blue)](https://galaxy.ansible.com/ui/repo/published/mykola_kharchenko/mssql_cdc/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Declarative management of Microsoft SQL Server Change Data Capture (CDC) for
Ansible. Native modules, a role and example playbooks so a single play converges
CDC across a fleet of databases — with `--check`, `--diff` and idempotency
exactly as Ansible users expect.

The modules wrap the [`mssqlcdcmgr`](https://github.com/mykola-kharchenko/mssqlcdcmgr)
Python engine, so the diff/apply/safe-recreate logic is shared with the
standalone CLI.

> **Status:** early development — see the [CHANGELOG](CHANGELOG.md).

## Install

```bash
ansible-galaxy collection install mykola_kharchenko.mssql_cdc
pip install -r requirements.txt   # Python deps for the modules
```

The Python modules need the Microsoft **ODBC Driver 18 for SQL Server** and
`unixODBC` on whichever host runs them (the Ansible controller for
`delegate_to: localhost`, otherwise the target). See `requirements.txt` and the
`mssqlcdcmgr` README for the OS package list.

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

## Collection vs the `mssqlcdcmgr` CLI

This collection and the [`mssqlcdcmgr`](https://github.com/mykola-kharchenko/mssqlcdcmgr)
Python CLI share the same engine; pick the surface that fits your workflow:

| Use this collection when…                                | Use the CLI when…                                       |
|----------------------------------------------------------|---------------------------------------------------------|
| Your team already runs Ansible (inventory, vault, AAP)   | You want a single self-contained binary                 |
| You want native `--check` / `--diff` + standard recap    | You want fast (~50 ms) startup for `validate`/`plan`    |
| You want CDC declared alongside other DB/OS state        | You want a programmatic Python API (`compute_diff`, …)  |
| Rolling/serial applies, tags, block/rescue matter        | You need the DBML schema export (`mssqlcdcmgr schema`)  |

Both share the same diff semantics, so a config "ported" between them produces
identical plans.

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
